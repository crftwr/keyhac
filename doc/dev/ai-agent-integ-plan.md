# Keyhac AI Integration — Design Handover

**Status:** design settled for layers 1–4; implementation not started
**Audience:** coding agent (Claude Code) working in `crftwr/keyhac`
**Related:** `CLAUDE.md`, `doc/configuration.md`, `doc/dev/`

---

## 1. Context and goal

Keyhac 2 is a Python-scriptable keyboard customization tool for Windows and macOS. It
already has a system-wide keyboard hook, per-application key tables matched on
accessibility focus path (AX / UI Automation), user modifiers, multi-stroke key tables,
clipboard history, window control, keyboard macro record/replay, and mouse *output*.

The goal of this work is to add AI integration — but not in the usual sense. The design
that came out of the discussion is deliberately narrow:

> **AI is used at authoring time to produce plain-Python `Action` classes.
> Those actions then run with no LLM involved.**

Everything below follows from that sentence.

---

## 2. Core design decisions

### 2.1 Two modes, and the pipeline between them

| | Mode ① Agent | Mode ② Action |
|---|---|---|
| What | Natural-language, LLM in the loop | Plain Python, deterministic |
| When | Exploration, one-off tasks, authoring | Everything that has stabilised |
| Cost | Seconds, tokens, variable | ~50 ms, free, repeatable |

① writes ②. ② is the crystallised form of a task that ① has performed enough times to
be worth freezing. The system should get *less* AI-dependent with use, not more.

### 2.2 Key bindings as the trigger — this is the differentiator

Natural language charges its cost on **every invocation**. A key binding charges once, at
authoring time, and amortises over every run afterwards.

More importantly, the origin context (application, focus path, selection, active key
table) supplies most of what a chat prompt would have to state explicitly. A single
keystroke therefore carries the weight of a much longer instruction. **The prompt is not
short — the prompt is already filled in.**

The useful quadrant is *high frequency × high variance*: the same intent every time, a
different target every time. Low-variance work needs no AI (Keyhac already solves it
deterministically); low-frequency work is fine in a chat window.

### 2.3 Runtime LLM is the exception, not the rule

This was the most important correction in the discussion. The test is:

> **Is the input space closed?**

Does **not** need an LLM — plain Python is correct and better:

- Locating UI elements → search the AX/UIA tree by name, role, hierarchy
- Branching → inspect state, use `if`
- Extraction → regex and parsers (more accurate than an LLM for URLs, paths, ARNs)
- Format conversion → JSON↔YAML, case conversion, path normalisation
- Waiting and retry → condition polling
- Iteration → loops

Does need an LLM — all of these have **natural language as input**:

- Translation, summarisation, commit-message generation, naming
- Mapping unbounded text to a bounded label ("which category is this error?")
- Semantic equivalence ("do these two descriptions mean the same thing?")

Note what is absent: UI automation itself never needs an LLM.

**Trap to avoid:** "the value differs every time" does not imply "an LLM is needed".
Differing *values* are parameters. Only differing *structure* or natural-language input
justifies inference.

### 2.4 Class split enforces this

```python
class MyAction(Action):        # default — pure Python
    ...

class MyAction(LLMAction):     # inference declared in the type
    ...
```

Consequences worth preserving:

- Which actions require inference is visible from `config.py` alone — privacy auditing
  becomes a type-level property, not a code-reading exercise.
- The authoring skill can enforce "write pure Python first; if you use an LLM, state why
  in a comment". LLMs left to themselves will reach for an LLM.
- `LLMAction` being a small minority is a health metric. If it grows, the layer-2 design
  is wrong.

### 2.5 Privacy: local by default

Runtime inference defaults to a local model (Ollama / llama.cpp). Cloud calls require an
explicit per-action opt-in declared in config. Because runtime inference is confined to
natural-language processing, a small local model is sufficient — favour
`llm_choose()` / `llm_json()` (bounded, verifiable output) over free-form `llm()`.

Code *generation* (authoring `Action` classes) is a different matter and should use a
frontier model. Small models confidently emit APIs that have changed. This splits
cleanly: **source code leaves the machine at authoring time; runtime data never does.**

### 2.6 Failure handling: fall back, do not self-heal

Every generated action declares preconditions. If the UI changes and a precondition
fails, the action **stops** and hands off to mode ① (a human asking Claude to regenerate
it). Do not build runtime self-repair — an action that silently does the wrong thing is
worse than one that refuses to run.

---

## 3. Architecture — two topologies

### A. Keyhac as MCP server (subscription-friendly)

```
[Claude Desktop / Code]   ← authenticates with the user's own subscription
      ↓ MCP (stdio bridge → unix socket / named pipe)
[Keyhac daemon]           ← provides tools only; never touches credentials
      ↓
[origin application]
```

Keyhac cannot be spawned over stdio (it is a resident daemon), so a thin bridge
executable registers as the MCP server and connects to the running instance.

Licensing note: Anthropic's Claude Code legal-and-compliance page states that OAuth
authentication is intended for subscription holders' ordinary use of Claude Code and
other native Anthropic applications, and that developers building products — including
those using the Agent SDK — should use API key authentication, with third-party routing
of Free/Pro/Max credentials on the user's behalf not permitted. **Topology A sidesteps
this entirely: inference happens inside a first-party client, under the user's own
credentials, and Keyhac stores no token of any kind.** This is also a significant trust
win for an application that has to prove it is not a keylogger.

The same applies to driving Claude Desktop through GUI automation. Keyhac has the parts
to do it (window activation, clipboard injection, key output, AX readback) and it is
fine as a **launcher** — send selected text plus a template prompt, let the human read
the reply. It is not fine as an LLM backend: parsing the reply and looping on it is the
same thing the policy prohibits, by a different transport. It is also technically poor —
no reliable completion detection, no tool use, no structured output, and it re-triggers
the focus-stealing problem.

### B. Keyhac as agent host (API key)

Required for key-binding-triggered agent runs, because MCP is pull-based and
host-initiated — there is no clean way for Keyhac to push "the user pressed a key, start
an agent". Uses a user-supplied API key or a local model.

**Both are needed.** A is for exploration and for the natural-language config editing
described below; B is for crystallised actions and low-latency transforms.

---

## 4. What is missing in Keyhac today

Ordered by dependency. Layers 1–4 are the implementation target.

### Layer 1 — Observation

Keyhac can record *input* but not *what happened*. Macro recording captures keys only.

- **Mouse input hook** — output exists, input does not. macOS: add mouse events to the
  existing `CGEventTap`. Windows: add `WH_MOUSE_LL`. Rides on the existing hook layer.
- **Outcome observation** — subscribe to AX notifications (`kAXWindowCreated`,
  `kAXUIElementDestroyed`, `kAXValueChanged`) and the UIA event handlers. Generalising
  from a demonstration requires pairing input with **state transitions**.
- **Structured trace (JSONL)** — timestamped unified stream of key / mouse / focus
  change / UI event / clipboard change. Separate layer from the human-readable console
  log. **Design the schema after capturing real traces, not before.**

### Layer 2 — State reading

- `get_ui_tree(root, depth, filter)` — depth limiting and role filtering are mandatory;
  Electron apps emit thousands of nodes
- `find_element(pattern)` / `element.perform(action)` — AX actions and UIA patterns
- `get_selection()` / `get_text(element)`

Focus path currently gives only the ancestor chain. Siblings and descendants are
invisible, so an action cannot do anything beyond sending key sequences. **This layer
determines the ceiling on action expressiveness — and it is the precondition for
eliminating runtime LLM calls.**

### Layer 3 — Execution safety

- **Preconditions** — `Action.preconditions()`; the basis of the fall-back-to-① design
- **Dry run / preview** — `describe()` / `preview()` feeding an approval UI
- **Undo journal** — clipboard writes, window moves, text insertion are all reversible
  (share the design with XeFM)
- **Cancellation** — `Esc` must stop a long-running action

### Layer 4 — Action metadata

Name, description, argument schema. Needed both to invoke actions from ① and to list
them over MCP — the shape is close to an MCP tool definition, so one implementation
serves both.

### Layer 5 — Generated artifact management (later)

- `~/.keyhac/actions/*.py` auto-discovery, individually disable-able. Do **not** append
  to `config.py`; a broken generated action must not take the human's settings with it.
  Keep the source prompt / trace as a header comment so it can be regenerated.
- Partial reload — reloading everything to test one generated action kills the
  generate→verify loop rate.

### Deferred / uncertain

- **`AsyncAction` (persistent asyncio loop, task handle, progress channel, mid-run
  approval)** — `ThreadedAction`'s `starting()` / `run()` / `finished(result)` lifecycle
  is a good fit (`starting()` is the right place to freeze origin; `finished()` the right
  place for approval). It is sufficient for single-shot transforms. It is insufficient for
  agent loops: no task handle so no cancellation, a thread pool rather than a resident
  event loop so MCP connections cannot be reused, pool exhaustion by minutes-long runs,
  no progress channel, no way to interact with the user mid-`run()`.
  **However** — since runtime LLM calls are now the exception, this may not be needed at
  all. Decide after hand-writing a few actions.
- **Automatic "you do this often, make it an Action" suggestion** — hard and low value.
  Frequent operations are already fast (the human has optimised them by muscle memory);
  identifying "the same operation" across typos and retries is hard; and a few wrong
  suggestions destroy trust permanently. Prefer: (a) make it *searchable* on demand
  rather than proactive, (b) rank by frequency × duration rather than frequency,
  (c) promote from agent-run logs, which are direct evidence of "frequent AND currently
  slow", (d) a single key to mark "that was annoying".
- **Recording wizard** — not needed. A wizard demands the user declare "I am about to do
  this three times", which interrupts the workflow and produces artificial
  demonstrations. Use a **ring buffer** of the last N minutes plus after-the-fact
  extraction (`Fn-Shift-R` → "turn what I just did into an Action"). The only UI required
  is boundary confirmation, which is close in weight to the existing clipboard history
  popup.

---

## 5. Authoring skill

A skill is needed for trace → generalised `Action`. Three kinds of knowledge, with
different homes:

| | Where | Why |
|---|---|---|
| Keyhac API reference | skill `references/` | Large; load on demand. Mostly already in `doc/configuration.md` |
| Trace schema | skill body | Short; always needed |
| **Generalisation heuristics** | **skill body — the core** | Procedural knowledge; the only part genuinely worth writing |

Ship it in-repo (`keyhac/skills/action-authoring/SKILL.md`) so it versions with the API.
Works for both topology A and B.

Heuristics to encode (derive from real failures, not from first principles):

- Write pure Python. Using an LLM at runtime requires a stated reason.
- Prefer `llm_choose()` / `llm_json()` over free-form `llm()`.
- Discard coordinates unconditionally — resolve mouse clicks to the nearest AX element
  before recording. Generated code containing coordinates is a failure.
- Treat absolute paths as argument candidates.
- Treat `focus_change` as a natural block boundary in a key sequence.
- Convert human pauses during demonstration into condition waits, not `sleep`.
- Repeated key sequences over varying targets → loop plus argument.
- Always emit preconditions.

Build an eval set alongside it (~10 trace → expected-action pairs; `skill-creator` has
eval support). Without regression testing, each new rule breaks something else.

**Write the skill after hand-writing actions, not before.** Written first, the
generalisation section will be vacuous.

---

## 6. Sequence

1. Implement layers 1–3 (mouse hook, structured trace, UI tree API)
2. Capture real traces — a week of ordinary work
3. **Hand-write 3–5 `Action` classes** ← do not skip
4. Derive the generalisation heuristics from step 3; write the skill
5. Try generation

Layers 1–3 carry their own weight without any AI: macro recording that captures the
mouse, key bindings that address elements by name. **If the AI side fails entirely, the
investment still stands.**

---

## 7. Open questions — measure, do not deliberate

- **Does Claude Desktop support MCP sampling?** The protocol allows a server to request
  inference from the host, but client support is rare. Topology A's design depends on the
  answer. Stand up a minimal MCP server and send a sampling request.
- **Real AX/UIA tree size and retrieval cost** — measure on Electron apps, VSCode,
  browsers. Sets the default depth and filter.
- **Local model latency on the target hardware** — is a 300 ms budget for `llm_choose()`
  realistic? Note that setting a context window larger than available VRAM causes a
  silent fallback to CPU that degrades structured-output reliability, not just speed.
- **UI tree API shape** — worth settling before implementation, since it is the ceiling
  on action expressiveness and changing it later breaks every action. Element identity
  (path? ID? name?), handle lifetime (persistent or single-use), how far to unify Windows
  and macOS.

---

## 8. Non-goals

- Generic computer-use MCP server. That space is saturated — 25+ servers as of early
  2026, including official Microsoft and Google ones. Competing there loses.
  **The position is "make *your* key bindings callable from Claude", not "let AI drive
  the desktop".** The defensible assets are: a single config across Windows and macOS
  (nearly all competitors are one-OS), a tool surface made of the user's own semantic
  commands rather than raw click/type primitives, and a system-wide hook — which no
  competitor has, and which is what makes origin capture, physical-key approval, instant
  `Esc`, and active-keytable context possible.
- Screenshot-based automation. Accessibility tree first; window capture only as a last
  resort for Canvas / games / remote desktop.
- Always-on collection. Traces are opt-in, in-memory, and isolated to a dedicated key
  table. Not being mistaken for a keylogger is a survival requirement — extend
  `PRIVACY.md` accordingly.
- Shipping subscription authentication inside Keyhac.

---

## 9. Constraint that must not be violated

**A key press must return control immediately.** Once a key is pressed the user expects
instant response; agents take seconds. Breaking this destroys the entire premise.

- The hook never blocks. Ever.
- Progress goes to a balloon. Focus stays on the origin; the user keeps working.
- Notify on completion; **apply results only after approval**. Never insert silently.
- Separate fast and slow operations into different key tiers (e.g. `Fn-T` = 300 ms local
  transform, `Fn-G` prefix = agent).

Latency budget:

| Use | Model | Budget |
|---|---|---|
| Clipboard transform, formatting | local small | 300 ms |
| Candidate suggestion, intent parsing | Haiku class | 1 s |
| Multi-step operation | Sonnet class | seconds+ |

# Keyhac AI Integration — Design Handover

**Status:** design settled for layers 1–4; implementation not started.
Codebase claims were verified against source on 2026-08-05; file references
point at that snapshot. A follow-up design session on 2026-08-06 narrowed the
target class of work, split layer 1, and retracted several earlier decisions;
its conclusions are merged in below and are the ones that stand.
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

Everything below follows from that sentence. §2 narrows the target further: "automation
triggered by a key binding" is the general frame, but the concrete work that survived
scrutiny is a specific and much more tractable shape.

---

## 2. What the interesting actions actually are

The concrete cases that survived scrutiny are narrower and more specific than
"key-binding-triggered automation":

> **UI-mediated ETL against systems that expose no API.**

The recurring shape:

```
enumerate the set of targets → for each:
      ├ navigate and wait for the new state
      ├ read or write
      └ accumulate
→ aggregate → transform → emit in an external format
```

The body of an action is a pipeline. UI manipulation is its I/O driver, not its subject.

Representative cases, in rough order of how much API surface each exercises:

- **Cross-system search and consolidation** — fill a form, search, follow pagination to
  the end, read each result table, normalise differing column names across systems,
  write one CSV, open it
- **Bulk form submission from CSV** — read rows, fill and submit each, write
  success/failure back to the row; must be idempotent so a resumed run does not
  double-submit
- **Config export / diff-apply** — walk every settings tab, read all field values into
  JSON as a pre-change backup; later, apply only the fields that differ
- **Reconciliation** — extract listings from two systems, set-difference them
- **Queue processing with per-item branching** — open each item, read it, decide, act;
  not a plain loop
- **Print every browser tab to PDF** — iteration plus modal traversal plus filename
  derivation plus partial-failure handling; the densest single test case
- **Long-job watch** — detect completion in the UI, branch on success/failure, extract
  the log on failure
- **Audit capture** — walk a fixed set of screens, capture each, timestamp, index

None of these need runtime inference. See §3.4.

### 2.1 Consequences of that shape

**Scale changes the failure model.** These are hundreds of items over tens of minutes,
not twenty items over one. A read that fails leaves nothing behind; a write that fails
halfway leaves a partial mutation. Therefore:

- **Checkpoint and resume matter more than undo.** For this class of work, rollback is
  usually not achievable — the remote system has already accepted the writes. Record
  *how far it got* in a form precise enough to resume. Design the journal as a progress
  log first and a rollback mechanism second (Layer 3, §5).
- **Preconditions belong on each step**, not only on the action.
- **Report progress in terms of what to redo**, not a bare error.

**Idempotency is a recurring requirement** (diff-apply, bulk submission,
reconciliation). The rule that makes it work is mechanical: **read the current value
before writing.** A checkbox pressed blindly toggles rather than sets. Put this in the
skill as a hard rule.

**Background execution is a real requirement.** Tens-of-minutes actions demand it.
Combined with the verified `max_workers=1` pool in `keyhac/core/action.py`, a single long
run blocks every other `ThreadedAction` in the app — this is already a latent bug today,
independent of any AI work. At minimum a separate executor for long-running actions is
required; see Layer 3 in §5.

**External format I/O.** CSV and JSON are stdlib. Decide explicitly whether spreadsheet
libraries become a Keyhac dependency or are left to the user's environment.

---

## 3. Core design decisions

### 3.1 Two modes, and the pipeline between them

| | Mode ① Agent | Mode ② Action |
|---|---|---|
| What | Natural-language, LLM in the loop | Plain Python, deterministic |
| When | Exploration, one-off tasks, authoring | Everything that has stabilised |
| Cost | Seconds, tokens, variable | ~50 ms, free, repeatable |

① writes ②. ② is the crystallised form of a task that ① has performed enough times to
be worth freezing. The system should get *less* AI-dependent with use, not more.

### 3.2 Key bindings as the trigger — this is the differentiator

Natural language charges its cost on **every invocation**. A key binding charges once, at
authoring time, and amortises over every run afterwards.

More importantly, the origin context (application, focus path, selection, active key
table) supplies most of what a chat prompt would have to state explicitly. A single
keystroke therefore carries the weight of a much longer instruction. **The prompt is not
short — the prompt is already filled in.**

The useful quadrant is *high frequency × high variance*: the same intent every time, a
different target every time. Low-variance work needs no AI (Keyhac already solves it
deterministically); low-frequency work is fine in a chat window.

### 3.3 Runtime LLM is the exception, not the rule

This was the most important correction in the first discussion. The test is:

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

### 3.4 …and it may be unnecessary entirely

Two further observations close the remaining gap:

**The natural-language use cases are already taken.** The one runtime-LLM example in the
earlier list was generating a commit message from a staged diff — which Claude Code
already does better, inside the editor. The same holds for translation and
summarisation: dedicated tools exist with better ergonomics. Claude Code owns the inside
of the editor; competing there produces a worse version of something the user already
has.

**Outside the editor, natural language barely appears.** Every case in §2 is form
filling, table reading, navigation, aggregation, and file output. None of it has
natural-language input. That the entire catalogue came out as pure Python is not a
coincidence — it is what the domain looks like.

If this holds, several things simplify:

- `ctx.llm()` / `llm_choose()` / `llm_json()` are not needed
- local-model latency measurement (§11) drops off the list
- the privacy story becomes unconditional: *no inference happens at runtime*
- **topology B may be unnecessary** (§4.2), since its justification was key-triggered
  agent runs. If key bindings only ever invoke pure-Python actions, topology A suffices
  and API-key handling disappears with it

**Do not delete `LLMAction` yet.** Hand-write the first actions and see whether the need
appears. If it has not appeared by then, remove it — and note that "built with AI, runs
without it" is a genuinely defensible position, competing with neither Claude Code nor
the crowded computer-use field.

### 3.5 Class split enforces this

```python
class MyAction(Action):        # default — pure Python
    ...

class MyAction(LLMAction):     # inference declared in the type
    ...
```

Consequences worth preserving, for as long as the class exists:

- Which actions require inference is visible from `config.py` alone — privacy auditing
  becomes a type-level property, not a code-reading exercise.
- The authoring skill can enforce "write pure Python first; if you use an LLM, state why
  in a comment". LLMs left to themselves will reach for an LLM.
- `LLMAction` being a small minority is a health metric. If it grows, the layer-2 design
  is wrong. Per §3.4, the expected minority is *zero*.

### 3.6 Privacy: local by default

Runtime inference, if any survives §3.4, defaults to a local model (Ollama / llama.cpp).
Cloud calls require an explicit per-action opt-in declared in config. Because runtime
inference is confined to natural-language processing, a small local model is sufficient —
favour `llm_choose()` / `llm_json()` (bounded, verifiable output) over free-form `llm()`.

Code *generation* (authoring `Action` classes) is a different matter and should use a
frontier model. Small models confidently emit APIs that have changed. This splits
cleanly: **source code leaves the machine at authoring time; runtime data never does.**

### 3.7 Failure handling: fall back, do not self-heal

Every generated action declares preconditions. If the UI changes and a precondition
fails, the action **stops** and hands off to mode ① (a human asking Claude to regenerate
it). Do not build runtime self-repair — an action that silently does the wrong thing is
worse than one that refuses to run.

This is also why OCR stays out (§6) and why coordinates stay out of generated code (§8):
preconditions cannot be expressed probabilistically.

---

## 4. Architecture

### 4.1 Topology A — Keyhac as MCP server (subscription-friendly)

```
[Claude Desktop / Code]   ← authenticates with the user's own subscription
      ↓ MCP (stdio bridge → localhost HTTP)
[Keyhac daemon]           ← provides tools only; never touches credentials
      ↓
[origin application]
```

Keyhac cannot be spawned over stdio (it is a resident daemon), so a thin bridge
executable registers as the MCP server and connects to the running instance. The
bridge exists for Claude Desktop, whose local-server config is stdio-only; Claude
Code can attach to a local streamable-HTTP endpoint directly (`claude mcp add
--transport http`), so if the daemon serves HTTP on localhost that client needs
no bridge at all.

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

### 4.2 Topology B — Keyhac as agent host (API key)

Required for key-binding-triggered *agent* runs, because MCP is pull-based and
host-initiated — there is no clean way for Keyhac to push "the user pressed a key, start
an agent". Uses a user-supplied API key or a local model.

**Whether B is needed at all is now open.** Its justification was key-triggered agent
runs; if §3.4 holds and key bindings only ever invoke pure-Python actions, topology A
suffices on its own. Decide after hand-writing the first actions.

### 4.3 The MCP server cannot stand alone

The work cannot simply live in the bridge, for three reasons:

- **Accessibility permission is granted per binary.** On macOS the user authorises a
  specific executable in System Settings. A separate MCP server process is a separate
  authorisation prompt. Asking a user who already granted Keyhac to grant a second
  unfamiliar binary is bad for an application whose main trust problem is proving it is
  not a keylogger.
- **The origin session lives in the daemon.** Window identifiers, focus history, active
  keytable, the frozen origin snapshot — all in Keyhac's process. Another process has
  nothing to refer to.
- **Windows UIA is COM apartment-bound**, which makes a cross-process split awkward.

```
[Claude Desktop] --stdio--> [bridge] --HTTP--> [Keyhac daemon]
[Claude Code]    ------------HTTP-------------> [Keyhac daemon]
```

**Keyhac speaks localhost HTTP; the bridge is a stdio↔HTTP shim and nothing else.** No
tool definitions, no logic — otherwise versions diverge and must be maintained twice.

### 4.4 Access control is not optional

Listening on localhost means every process on the machine can reach an API that reads the
UI tree and injects keystrokes. An application that argues it is not a keylogger cannot
ship an unauthenticated local endpoint offering key injection.

- Unix socket → filesystem permissions; HTTP → localhost bind plus a token generated at
  startup and read by the bridge
- **Disabled by default**; enabled explicitly in config
- "No unauthenticated local endpoint" is a standing principle, alongside "no always-on
  collection" (§12)

### 4.5 Keep the server off the action executor

The MCP server needs its own thread and a small loop of its own. Do **not** route tool
calls through the `ThreadedAction` pool — with `max_workers=1` a single long-running
action would stall every incoming tool call, and conversely a burst of tool calls would
starve actions. This is a separate concern from the long-action executor in §2.1;
resolve them independently.

---

## 5. What is missing in Keyhac today

Ordered by dependency. Layers 1–4 are the implementation target.

### Layer 1 — Observation

Keyhac can record *input* but not *what happened*. Macro recording captures keys only.
**This layer splits in two, and the halves have very different priority.**

**Required — event subscription.** Subscribe to AX notifications (`kAXWindowCreated`,
`kAXUIElementDestroyed`, `kAXValueChanged`) and the UIA event handlers. Genuinely new
work: nothing in the tree subscribes to AX notifications or UIA events today (no
`AXObserver`, no `SetWinEventHook`, no UIA event handler). This is what `wait_for` is
built on, and **everything on the output side depends on it** (§7.1).

**Demoted — mouse input capture and trace recording.** Two independent findings push
these down. First, of the concrete use cases catalogued in §2, almost none are authored
from a recorded demonstration. Second, most cases that appeared to need a demonstration
are served by having the user *open* a UI state and letting Claude read it, which records
nothing (§8.1). Build them last, if at all:

- **Mouse input events** — output exists; input exists only as an observation-only
  cancellation channel: `WH_MOUSE_LL` is already installed on Windows
  (`keyhac/platform/win/hook.py`) and the macOS tap already masks button-down /
  scroll types when `on_mouse` is wired (`keyhac/platform/mac/hook.py`), but the
  callback carries no event data. The work is widening that channel to deliver full
  events (button, position, wheel), not adding a hook — filtering out Keyhac's own
  injected output (sentinel `dwExtraInfo` / private `CGEventSource`) is already
  solved there.
- **Structured trace (JSONL)** — timestamped unified stream of key / mouse / focus
  change / UI event / clipboard change. Separate layer from the human-readable console
  log. **Design the schema after capturing real traces, not before.** Recording is
  explicit start/stop only, and the privacy requirements in §9 are part of the feature,
  not a later hardening pass.

### Layer 2 — State reading

More exists than an earlier draft of this document assumed. Both platforms already
ship a `UIElement` with `perform_action()` / `get_action_names()`
(`keyhac/platform/mac/uielement.py`, `keyhac/platform/win/uielement.py`); Windows
adds `set_value()`, `set_focus()` and Text-pattern extraction; macOS attribute
access is generic, so descendants are already reachable via
`get_attribute_value("AXChildren")`. The accurate ceiling statement: **an action
can act on the focused element and its ancestors, but cannot reach an element that
is not focused** (except on macOS, awkwardly, through raw AX attributes).

What is actually missing — unification and exposure, not green-field:

- **Windows child traversal** — the wrapper is parent-only; the
  `GetFirstChildElement` walker slot is declared in `win/uielement.py` but never
  wrapped
- `get_ui_tree(root, depth, filter)` — portable; depth limiting and role filtering
  are mandatory, Electron apps emit thousands of nodes
- `find_element(pattern)` — portable search by name / role / hierarchy
- text-layer accessors — a distinct concern from tree traversal; see §6

**This layer still determines the ceiling on action expressiveness — and it is the
precondition for eliminating runtime LLM calls.**

### Layer 3 — Execution safety

- **Preconditions** — `Action.preconditions()`; the basis of the fall-back-to-① design.
  Per-step as well as per-action (§2.1).
- **Dry run / preview** — `describe()` / `preview()` feeding an approval UI
- **Progress journal** — checkpoint-and-resume first, rollback second (§2.1). Clipboard
  writes, window moves and text insertion are genuinely reversible and can share an undo
  design with XeFM; writes already accepted by a remote system are not, and for the §2
  workload those dominate.
- **Cancellation** — `Esc` must stop a long-running action
- **A separate executor for long-running actions.** `ThreadedAction`'s `starting()` /
  `run()` / `finished(result)` lifecycle is a good fit for single-shot transforms
  (`starting()` is the right place to freeze origin; `finished()` the right place for
  approval), but the pool is `max_workers=1` (`keyhac/core/action.py`), so one
  minutes-long run blocks every other `ThreadedAction` in the app. That is a latent bug
  today. The tens-of-minutes actions in §2 make fixing it a prerequisite, not a
  nice-to-have. Whether the fix needs a full `AsyncAction` (resident event loop, task
  handle, progress channel, mid-run approval) or just a second executor depends on
  whether agent loops ever run at runtime — see §3.4.
- The output-side primitives these actions are built from — `wait_for`, form filling,
  pagination — are specified in §7.

### Layer 4 — Action metadata

Name, description, argument schema. Needed both to invoke actions from ① and to list
them over MCP — the shape is close to an MCP tool definition, so one implementation
serves both. The tools Claude needs while *writing* an action are a separate surface;
see §8.3.

### Layer 5 — Generated artifact management (later)

- `~/.keyhac/actions/*.py` auto-discovery, individually disable-able. Do **not** append
  to `config.py`; a broken generated action must not take the human's settings with it.
  Keep **the intent description only** as a header comment — never the source trace, which
  would put trace fragments into git (§9).
- Partial reload — reloading everything to test one generated action kills the
  generate→verify loop rate.

### Deferred / uncertain

- **Automatic "you do this often, make it an Action" suggestion** — hard and low value.
  Frequent operations are already fast (the human has optimised them by muscle memory);
  identifying "the same operation" across typos and retries is hard; and a few wrong
  suggestions destroy trust permanently. Prefer: (a) make it *searchable* on demand
  rather than proactive, (b) rank by frequency × duration rather than frequency,
  (c) promote from agent-run logs, which are direct evidence of "frequent AND currently
  slow", (d) a single key to mark "that was annoying".
- **Ring buffer of the last N minutes** — retracted. It was proposed to avoid making the
  user declare "I am about to demonstrate", and that reasoning ignored privacy. A trace is
  by construction keylogger output: it will eventually contain passwords, API keys,
  confidential record contents, and private messages typed in passing. No amount of
  opt-in, memory-only handling makes "you cannot predict when a secret enters the buffer"
  acceptable, and the user has no way to verify otherwise. Explicit start/stop only; see
  §9.

---

## 6. Reading state — two layers, not one

The accessibility tree is not the way to read the screen; it is the way to read *part* of
it. The distinction determines the API shape.

| Layer | What lives there | How to read it |
|---|---|---|
| **Widget** | buttons, menus, lists, tabs, table rows, form fields | tree traversal |
| **Text** | terminal output, editor buffers, page body text | selection / caret / whole-value |
| **Pixels** | canvas, games, remote desktop | out of scope |

**The tree does not reach into the text layer.** Terminal emulators and editors typically
expose their content as an undifferentiated blob with no per-line or per-token structure.
An action that wants "the error line" cannot find it by traversal.

The entry point to the text layer is not tree search. Three options, in order of how few
keystrokes the user spends:

- `element_at_point(x, y)` → the line under the mouse pointer. One key, and the pointer
  is usually already there.
- whole-value read plus positional logic — "the error is the last line" is often exactly
  true right after a failed command. Zero pointer, zero selection.
- `get_selection()` — the fallback, and also the correct choice when several candidates
  are on screen and only the human can say which one matters.

Once the text is in hand the work is a regex, not inference. Regex beats an LLM on paths,
line numbers, URLs, ARNs, and request IDs — it is more accurate, not merely cheaper.

**API additions this implies** (Layer 2):

```
get_selection(window_id)
get_text(element_id)              # AXValue / UIA Text pattern
get_line_at_caret(element_id)
element_at_point(x, y)
```

**On OCR:** not needed. The gap it would fill is the pixel layer, which is not where this
work happens, and it conflicts with three standing principles at once — it is
probabilistic (§3.7 prefers stopping over silently being wrong), it reintroduces
coordinates (§8 treats coordinates in generated code as a failure), and preconditions
cannot be expressed probabilistically. If a screen capture is ever wanted, it is for
*authoring* — letting Claude look at a window with its own vision — and that needs
`capture_window()`, not a local OCR engine. Even then, authoring-time only.

---

## 7. The output side

Reading state is where the ceiling is; acting is where the difficulty concentrates.

### 7.1 Waiting is the core primitive

Nearly every step in an output-side action is "act, then wait for something to change".
Modal open, modal close, re-render after a dependent field changes, page load after
pagination, application ready after launch. **`sleep` produces environment-dependent
breakage** — it passes on the developer's machine and fails on a slower or faster one.

```
wait_for(condition, timeout)        # appearance, disappearance, value change
wait_for_stable(element, timeout)   # re-render settled
```

Make "a generated action containing `sleep` is a failure" a hard skill rule, at the same
level as the rule about coordinates.

This is what makes AX notification / UIA event subscription load bearing even though the
rest of Layer 1 is demoted (§5).

### 7.2 Menus and modals are transient

An element that appears and then disappears cannot be located by a static search done in
advance. The pattern is always three-beat: wait for appearance, act, wait for
disappearance before proceeding. Getting the third beat wrong is what breaks iteration
over modals — the next cycle starts before the previous one finished.

### 7.3 Form filling has three mechanisms and they are not equivalent

| | How | Good | Bad |
|---|---|---|---|
| **paste** | clipboard + Ctrl/Cmd-V | fast; bypasses IME; `paste` events fire | clobbers the clipboard; some fields block paste |
| **keys** | key injection | works everywhere; all input events fire naturally | slow; IME state dependent |
| **set_value** | UIA `set_value()` / `AXValue` write | instant; IME-independent | **React/Vue frequently do not observe it** |

`set_value` has the worst failure mode: the value appears in the field, the framework's
internal state never updates, and submission sends empty. **Default to paste, fall back
to keys.** For Japanese input, paste is effectively mandatory.

**Always read the value back after writing.** Skill rule.

Paste-by-default makes clipboard preservation mandatory:

```
with ctx.preserve_clipboard():
    ...
```

**Per-field-type handling** — these are not variations on text entry:

- **Checkbox** — read current state, then toggle only if needed. Pressing blindly inverts.
- **Radio** — press the target element directly; do not navigate with arrow keys.
- **Select** — open, enumerate options, choose. Key-sequence selection breaks when the
  option list changes.
- **Autocomplete** — type, `wait_for` the suggestion list, then select. Tabbing away
  before the list appears leaves the field unconfirmed.
- **Date picker** — verify whether direct text entry works; treat as unknown until tested.
- **File chooser** — an OS dialog, a different subsystem entirely.

Checkboxes and autocompletes will be the two most common failure sites in generated code.

**Never depend on tab order.** Locate each field by name or label and focus it directly;
tab-count sequences break whenever the DOM changes.

**Read validation errors after submit.** Without this, "write the failure back to the CSV
row" is unimplementable.

### 7.4 Pagination deserves an abstraction

"Follow next while next exists" appears in every extraction case. Expose it as a
generator so generated code has one obvious correct shape rather than N hand-rolled
loops. Termination detection and the zero-results case are where hand-rolled versions go
wrong.

---

## 8. Authoring

The input to action generation is usually **not** a trace. **Traces capture form, not
intent.** A demonstration of "extract the ID and build a command" shows only that the
human typed a string; the transformation happened in their head and never reaches the
keyboard.

There are three input kinds, and they mix:

- **Current UI structure** — what element to address, what its identifier is
- **Natural-language intent** — the transformation rule, the branch condition, what is
  variable
- **Trace** — iteration unit, actual latency points, human judgement encoded in ordering

### 8.1 The escalation ladder

Use the cheapest rung that works. Each rung down costs the user more and exposes more.

1. **Read the current screen.** Everything visible simultaneously is authorable with no
   user action at all — search forms, result tables, window layouts, list selection.
   Bulk form filling is in this category: the fields are all on screen.
2. **Ask the user to open a state, then read it.** Modals, wizards, menu hierarchies,
   dependent fields — anything that does not exist until navigated to. **This records
   nothing.** No keystrokes are captured; Claude reads a tree. This covers most of what
   looked like it needed a demonstration.
3. **Ask in conversation.** Iteration counts, which steps are slow, conditional rules.
   Often faster and more accurate than inferring from a trace.
4. **Record a trace.** Only when the above genuinely cannot supply it.

Rung 4 is an exceptional path, not the main mechanism. Build it last. Rungs 1–3 record
nothing and need none of §9.

### 8.2 After a trace, ask before generating

When rung 4 is used, the skill must require a confirmation step. A trace shows that the
user set a status filter to "active"; it does not show whether that is a constant or an
argument. **Demonstration → clarifying questions → generation.** Never
demonstration → generation.

### 8.3 Authoring-time tools are a distinct API surface

Layer 4 covers metadata for *invoking* actions. It does not cover the tools Claude needs
while *writing* one. These have different security properties and should be designed
separately from the runtime tools:

```
run_action(name)          → execute and return output/errors
get_recent_trace(...)     → only after explicit recording and human approval (§9)
reload / partial reload
```

`run_action` closes the generate-verify loop. Without it the human manually runs each
attempt and pastes the error back, and loop iteration rate — which partial reload
protects — is dominated by that manual step instead.

### 8.4 The authoring skill

A skill is needed for intent (and, at rung 4, trace) → generalised `Action`. Three kinds
of knowledge, with different homes:

| | Where | Why |
|---|---|---|
| Keyhac API reference | skill `references/` | Large; load on demand. Mostly already in `doc/configuration.md` |
| Trace schema | skill body | Short; always needed |
| **Generalisation heuristics** | **skill body — the core** | Procedural knowledge; the only part genuinely worth writing |

Ship it in-repo (`keyhac/skills/action-authoring/SKILL.md`) so it versions with the API.
Works for both topology A and B.

Rules to encode — the permanent ones, which are hard rules rather than preferences:

- Inspect the current structure before writing.
- Address elements by name / role, never coordinates. Generated code containing
  coordinates is a failure; resolve mouse clicks to the nearest AX element before
  recording.
- `wait_for`, never `sleep` — equally a failure (§7.1). Convert human pauses during
  demonstration into condition waits.
- Read back after writing (§7.3).
- Read before toggling (§2.1).
- Emit preconditions, per step as well as per action.
- Report progress, and support interrupt and resume.
- Write pure Python. Using an LLM at runtime requires a stated reason; prefer
  `llm_choose()` / `llm_json()` over free-form `llm()` if it is used at all.
- Treat absolute paths as argument candidates.
- Treat `focus_change` as a natural block boundary in a key sequence.
- Repeated key sequences over varying targets → loop plus argument.

Build an eval set alongside it (~10 intent/trace → expected-action pairs; `skill-creator`
has eval support). Without regression testing, each new rule breaks something else.

**Write the skill after hand-writing actions, not before.** Written first, the
generalisation section will be vacuous — derive the heuristics from real failures, not
from first principles.

### 8.5 Prompt quality is the skill's test

If a user's prompt has to name `get_ui_tree` or `set_value`, the skill has failed. API
names in a prompt are a skill deficiency. Domain knowledge in a prompt is normal and
irreducible: what the user wants, output paths and naming rules, which system, column
correspondences, desired failure behaviour.

**Use this as an eval axis:** does the intended action come out from an intent-only
description? During the early period the prompts will necessarily be verbose because the
skill does not exist yet — **keep the prompt logs; whatever the human keeps repeating is
the skill's TODO list**, and shrinking prompts is the signal that it is working.

---

## 9. Trace privacy

Applies only to rung 4 (§8.1). Rungs 1–3 record nothing and need none of this.

**No continuous buffer.** Explicit start and stop only. What is bought is a property the
user can verify: nothing outside the marked window was ever captured. What is spent is
one keystroke and the need to declare intent — worth it.

Layered protection:

- **Visible recording indicator** for the whole session. This is what lets the user
  decide not to type a password right now — control returns to them.
- **Automatic secure-field redaction.** macOS `AXSecureTextField`, UIA `IsPassword`.
  While focus is in such a field, record `<redacted>` and not the keystrokes.
- **Pause key.** Covers what redaction misses — password-manager paste, for one.
- **Human review before egress.** The decisive one. A trace does **not** flow to Claude
  automatically. On stop, display it, let the user delete lines, and require approval
  before `get_recent_trace` will return anything. Nothing leaves that a human has not
  looked at.
- **Clipboard contents not recorded by default** — record that the clipboard changed, not
  what it changed to.
- **Never written to disk**, never embedded in generated artifacts (§5, Layer 5).

The review step is not purely a privacy cost: users spot retries and stray operations and
delete them, which improves generation accuracy. It pays for itself twice.

Extend `PRIVACY.md` with the above.

---

## 10. Sequence

1. **Layer 2 exposure** — Windows child traversal (`GetFirstChildElement`, already
   declared and unwrapped in `win/uielement.py`), `get_ui_tree`, `find_element`, plus the
   text-layer accessors from §6. Settle the tree API shape first (§11) — but settle it by
   walking a real page by hand, not on paper.
2. **`wait_for` and event subscription** — the required half of Layer 1 (§7.1).
   Everything on the output side depends on it.
3. **Two measurements, minutes each** — does `set_value` work on the target systems
   (§7.3); do the target terminals implement whole-value text reads (§6).
4. **Hand-write actions** ← do not skip. Recommended set and order: cross-system
   extraction (exercises pagination, normalisation, partial failure, file output);
   print-all-tabs-to-PDF (modal traversal, iteration, waiting, resume); a dialog handler
   (preconditions); an error-line jump (the text layer, in contrast to the tree).
5. **Derive the skill** from what step 4 taught (§8.4).
6. Try generation.

Mouse input capture and trace recording are not on this list.

These layers carry their own weight without any AI: key bindings that address elements by
name, waits that do not break on a slower machine, macro recording that eventually
captures the mouse. **If the AI side fails entirely, the investment still stands.**

---

## 11. Open questions — measure, do not deliberate

- **Does `set_value` work on the specific internal systems being targeted?** (§7.3) One
  form, five minutes, and the answer settles whether the clipboard-preservation path is on
  the critical path.
- **Do the target terminals and editors implement the UIA Text pattern and `AXValue`?**
  (§6) Test Terminal.app, iTerm2, Windows Terminal, VSCode, Chrome. If whole-value reads
  work, selection becomes optional and the one-keystroke path is available. If they all
  fail, selection is mandatory and the ergonomics change.
- **UI tree API shape** — worth settling before implementation, since it is the ceiling
  on action expressiveness and changing it later breaks every action. Element identity
  (path? ID? name?), handle lifetime (persistent or single-use), how far to unify Windows
  and macOS.
- **Real AX/UIA tree size and retrieval cost** — measure on Electron apps, VSCode,
  browsers. Sets the default depth and filter.
- **MCP sampling is a bonus, not a dependency.** Topology A as described — a chat
  client calling Keyhac tools — needs no sampling. Sampling would only decide whether
  *runtime* `LLMAction` inference could ride the user's subscription instead of
  topology B's API key / local model, and §3.4 may remove runtime inference altogether.
  Expect "no": as of early 2026 Claude Desktop does not support sampling (VS Code's MCP
  client is the notable one that does). If it ever matters, stand up a minimal server and
  send a sampling request rather than deliberating.
- **Local model latency on the target hardware** — only if runtime inference survives
  §3.4. Is a 300 ms budget for `llm_choose()` realistic? Watch for the silent
  partial-CPU-offload trap: a context window larger than available VRAM degrades speed
  first, and under a tight budget that surfaces as timeouts and truncated JSON —
  structured-output failure arriving indirectly. Verify on the actual hardware rather than
  trusting this note.

---

## 12. Non-goals

- Generic computer-use MCP server. That space is saturated — 25+ servers as of early
  2026, including official Microsoft and Google ones. Competing there loses.
  **The position is "make *your* key bindings callable from Claude", not "let AI drive
  the desktop".** The defensible assets are: a single config across Windows and macOS
  (nearly all competitors are one-OS), a tool surface made of the user's own semantic
  commands rather than raw click/type primitives, and a system-wide hook — which no
  competitor has, and which is what makes origin capture, physical-key approval, instant
  `Esc`, and active-keytable context possible.
- Screenshot-based automation, and OCR with it (§6). Accessibility tree first; window
  capture only for authoring-time inspection, or as a last resort for Canvas / games /
  remote desktop.
- Always-on collection. Traces are opt-in, explicitly bounded, in-memory, and isolated to
  a dedicated key table (§9). Not being mistaken for a keylogger is a survival
  requirement — extend `PRIVACY.md` accordingly.
- Unauthenticated local endpoints (§4.4).
- Shipping subscription authentication inside Keyhac.

---

## 13. Constraint that must not be violated

**A key press must return control immediately.** Once a key is pressed the user expects
instant response; agents take seconds, and the §2 workload takes minutes. Breaking this
destroys the entire premise.

- The hook never blocks. Ever.
- Progress goes to a balloon. Focus stays on the origin; the user keeps working.
- Notify on completion; **apply results only after approval**. Never insert silently.
- Separate fast and slow operations into different key tiers (e.g. `Fn-T` = 300 ms local
  transform, `Fn-G` prefix = agent or long-running action).

Latency budget:

| Use | Model | Budget |
|---|---|---|
| Clipboard transform, formatting | local small | 300 ms |
| Candidate suggestion, intent parsing | Haiku class | 1 s |
| Multi-step operation | Sonnet class | seconds+ |
| UI-mediated ETL (§2) | none — pure Python | minutes; background, resumable |

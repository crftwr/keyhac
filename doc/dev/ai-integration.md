# Keyhac AI Integration — Design Handover

**Status:** layers 2 and 4 built, layer 3 partial, layer 5 not started — the
table in §14 is authoritative, and the authoring loop has now run end to end
against Claude Desktop. §15 holds what that surfaced and has not been designed
yet. Codebase claims were verified against source on 2026-08-05; file
references point at that snapshot. A follow-up design session on 2026-08-06
narrowed the target class of work, split layer 1, and retracted several earlier
decisions; its conclusions are merged in below and are the ones that stand.
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

**Dropped — event subscription.** ~~This is what `wait_for` is built on.~~ It
was neither. An `AXObserver` wrapper was written, measured, and then **removed**
(along with `wake=` and `tools/ax_notification_pass.py`); the code is in git
history if the conclusion is ever revisited. What the measurements said:

- **Native Cocoa applications post generously.** Opening a Finder window
  delivered `AXWindowCreated`, `AXCreated`, `AXFocusedWindowChanged`,
  `AXUIElementDestroyed` and a stream of `AXValueChanged`.
- **Web content posts nothing.** A `<dialog>` opening delivered *zero*, in
  Safari and in Chrome alike, registered on the application element and on the
  `AXWebArea` alike — Chrome measured with its tree exposed, a driven page
  change, and a passing Finder control in the same run. Not a WebKit quirk:
  Chromium too, which is what Electron is.
- **There is a structural reason.** AX notifications do not bubble, so "wait
  for an element to appear" would have to be registered on an element that does
  not exist yet, and its container is not obliged to announce it.

The remaining case — a native target — did not justify the surface either, and
in an instructive way: polling's *first* interval is 20 ms, so a fast
transition is already caught fast, and a wait long enough to have backed off to
250 ms is a wait where 250 ms is noise. The accelerator helped least where
polling was cheap and mattered least where polling was slow. Five hand-written
actions across two platforms never used it.

So `wait_for` is polling, full stop (`keyhac/core/wait.py`), and the output
side depends on `wait_for` rather than on any subscription. Windows never
needed a WinEvent/UIA counterpart, which is the same conclusion reached from
the other direction.

**Demoted — mouse input capture and trace recording.** Two independent findings
pushed these down. First, of the concrete use cases catalogued in §2, almost
none are authored from a recorded demonstration. Second, most cases that
appeared to need a demonstration are served by having the user *open* a UI
state and letting Claude read it, which records nothing (§8.1).

**And now: not ours to build at all.** Claude Desktop records a task itself —
screen, clicks, typing and **voice** — and turns it into a skill. That answers
rung 4 from outside, and it answers the two objections that demoted it:

- **The intent objection.** §8 says traces capture form, not intent: "the
  transformation happened in their head and never reaches the keyboard." It
  reaches the *microphone*. A narrated demonstration carries the reasoning a
  keystroke log structurally cannot, which is a different artefact from the
  JSONL trace this section was contemplating.
- **The privacy objection.** All of §9 — redaction, a pause key, review before
  egress, never to disk — was specified for a recorder **Keyhac** would ship,
  because building one means an application whose main trust problem is proving
  it is not a keylogger shipping a keylogger. If the recorder belongs to the
  client the user already trusts with their screen, Keyhac never takes that on.

What the recording cannot supply is selectors: it has pixels, and §8.4's rule 2
makes pixel addressing a failed generation. So the division is intent from the
recording, selectors from the live tree via the MCP tools — which is §8.2's
"demonstration → clarifying questions → generation" with the demonstration
arriving pre-summarised. Recorded in the authoring skill; no Keyhac code.

Two things this does **not** settle. The recording's privacy properties are the
recorder's, and they are weaker than §9 asked of ours: the consent dialog warns
against typing secrets, but there is no secure-field redaction and no
review-before-egress step — the recording goes to Claude. And its output format
has not been examined here; the first real one should be, before the skill's
guidance hardens.

### Layer 2 — State reading

More exists than an earlier draft of this document assumed. Both platforms already
ship a `UIElement` with `perform_action()` / `get_action_names()`
(`keyhac/platform/mac/uielement.py`, `keyhac/platform/win/uielement.py`); Windows
adds `set_value()`, `set_focus()` and Text-pattern extraction; macOS attribute
access is generic, so descendants are already reachable via
`get_attribute_value("AXChildren")`. The accurate ceiling statement: **an action
can act on the focused element and its ancestors, but cannot reach an element that
is not focused** (except on macOS, awkwardly, through raw AX attributes).

What was missing — unification and exposure, not green-field — **is now in**
(`keyhac/core/uitree.py`, plus `children()` / `describe()` / `identity_key()` on
both platform elements):

- **Windows child traversal** — `children()` walks `GetFirstChildElement` /
  `GetNextSiblingElement`, the slots that were declared in `win/uielement.py`
  and never wrapped. Written; **not yet run on Windows**.
- `get_ui_tree(root, max_depth, max_nodes, roles, prune)` — portable. The
  budgets are as mandatory as expected, but for a different reason than
  Electron's node count: see §6.
- `find_element` / `find_elements` — portable search by role / name / value /
  identifier / text / predicate, using the same fnmatch-with-`|` matching
  `define_keytable` uses, with the `AX` prefix optional in role patterns.
- text-layer accessors — a distinct concern from tree traversal; see §6.

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

**API additions this implies** (Layer 2) — all four now exist as element
methods, verified live on macOS:

```
element.get_selection()
element.get_text()                # AXValue / UIA Text pattern
element.get_line_at_caret()
UIElement.element_at_point(x, y)
```

### 6.1 What walking real trees changed

Measured 2026-08-06 on macOS 15, against a page carrying the §2 shape (search
form, three-row result table, modal, log block). Four findings, each of which
moved the API:

- **The accessibility graph is a DAG, not a tree.** A table cell is a child of
  its row *and* of its column — the same element, CFEqual-identical, reached
  twice. A naive recursion reports every cell twice and doubles every extracted
  table. `get_ui_tree` dedupes on element identity, which is why the budgets
  are load-bearing on any page with a table and not merely on Electron.
- **Chromium and Electron expose no content at all until asked.** A loaded
  Chrome page was 59 nodes — browser chrome, no document. After setting
  `AXEnhancedUserInterface`, 119 nodes with every field addressable. The
  targeted `AXManualAccessibility` that Chromium documents did **nothing** on
  Chrome; only the blunt "an assistive client is present" flag moved it, and
  that one has side effects (VS Code switches to screen-reader rendering). So
  it is an explicit `set_manual_accessibility()` call, never implicit in a
  walk. Both directions verified: turning it back off restored 59 nodes.
- **Web content puts text one level below where you ask for it.** A `<pre>`'s
  own `AXValue` is empty and the string lives in a child `AXStaticText`, so a
  container read reports nothing for exactly the elements a log or an error
  line lives in. Hence `get_text()` descends to leaves, and `UINode.all_text`
  exists beside `node.text`.
- **`AXDOMIdentifier` carries the DOM `id`** in web content. That is a far more
  stable address than a label — it survives relabelling and localisation — and
  it is what generated actions should prefer where a page offers one.

Two smaller ones worth keeping: batching a node's attributes through
`AXUIElementCopyMultipleAttributeValues` is 2.1× faster than reading them one
at a time and answered identically on all 123 nodes of the probe page; and
falsy values are a live trap — an unchecked checkbox is `0`, so `if value:`
hides precisely the state "read before toggling" exists to check.

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
wait_for(condition, timeout, message=…, wake=…)   # returns the condition's value
wait_for_element(root, timeout, **criteria)       # beat 1
wait_until_gone(root, timeout, **criteria)        # beat 3
wait_for_stable(root, quiet, timeout)             # re-render settled
```

All four are in `keyhac/core/wait.py`, verified live against the three-beat
modal cycle in §7.2. Make "a generated action containing `sleep` is a failure" a
hard skill rule, at the same level as the rule about coordinates.

Two constraints shaped the implementation, and both are worth knowing before
changing it:

- **The condition cannot run on the calling thread.** Waiting happens in
  `ThreadedAction.run()`, because a key press must return control immediately
  (§13), but reading elements is main-thread work. So each poll hands the
  condition to `keymap.call_on_main_thread` and blocks for the answer, and
  calling `wait_for` *on* the loop thread raises rather than deadlocking the
  keyboard.
- **A timeout is an error, not a `False`.** `WaitTimeout` subclasses
  `TimeoutError`. An action whose precondition never arrived stops (§3.7).

Still open, and inherited rather than introduced: a long wait holds
`ThreadedAction`'s single pool worker for its whole duration, so a ten-minute
wait stalls every other threaded action. That is the executor problem in §2.1.

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

*Measured* (macOS, Safari, 2026-08-07), correcting a note recorded here a day
earlier. An earlier run concluded that writing `AXValue` to a plain
`<input type=text>` "did nothing, silently". It does work — **provided the
element is focused first**, which that run had not done. Focused, all three
mechanisms succeed on macOS, and the interesting part is what they cost:

| | Latency | Notes |
|---|---|---|
| `set_value` | ~5 ms | Instant, but this is the one frameworks miss |
| `keys` | ~70 ms | Faithful event stream; IME-dependent |
| `paste` | ~105 ms | Costs the clipboard; the default |

Two things that fall out of it, both now enforced in `keyhac/core/fill.py`:

- **Focus is a precondition, not a courtesy.** Unfocused writes fail silently,
  and unfocused *keystrokes* are worse - they go to whatever does have focus,
  which is the user's editor. `focus()` therefore verifies against the
  system-wide focused element and refuses to write when it did not land.
- **The clipboard cannot be restored until the target has read it.** Restoring
  as soon as the paste keystroke is posted races the application, and loses:
  the field ends up holding whatever was on the clipboard *before*, which is a
  wrong value that looks exactly like a successful paste. Verification runs
  inside the clipboard swap.

*Measured on Windows* (2026-08-07, Notepad on Windows 11 Home 10.0.26200,
`tools/uia_pass.py`). All three work here too, in the same order with a wider
spread — `set_value` 15–33 ms, `paste` 48–95 ms, `keys` 114–272 ms — so the
§11 question about `set_value` is answered on both platforms and none of it
disturbs the paste-first default.

What Windows adds is a reason to distrust `keys` that macOS did not show.
**WinUI text controls drop and reorder injected input.** In Notepad's editor
`hello-keys` arrived as `helloke-ys`, a `Ctrl-V` came through as a bare `v`,
and an injected `Ctrl-V` is dropped outright often enough to need retrying.
The same strings down the same code path land intact 30/30 in a plain Win32
control, so this is XAML's input handling rather than `SendInput` ordering or
the hook. Two consequences: paste-first is right on Windows for a second,
independent reason, and no write mechanism on this platform can be trusted
without its read-back.

The rule that survives unchanged — and that turned every one of those
corruptions from a silently wrong document into a `FillFailed` naming the text
it actually found — is the one already stated: **read the value back after
writing**, and treat a mismatch as a failed step rather than a warning.

The corollary learned the hard way: `verify=False` does not merely skip that
check, it removes the only signal that the target has finished reading the
pasteboard. The clipboard-restore race above is guarded *by* the verification,
so turning verification off re-opened it — and it was re-opened long enough to
put a stale clipboard into a real document. An unverified paste now holds the
clipboard for a fixed settle and logs that it is guessing.

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

1. ~~**Layer 2 exposure**~~ — **done, and now verified on both platforms.**
   `keyhac/core/uitree.py` plus `children()` / `describe()` / the text accessors
   on both platform elements; the shape was settled by walking real trees, and
   §6.1 records what that changed. The Windows half was written against header
   slot numbers and had never been run — this file's own rule being that a wrong
   vtable slot silently calls a different method rather than raising. It has now
   been run (`tools/uia_pass.py`, 2026-08-07): the child walk, all three text
   accessors and `element_at_point` answer correctly, and the bug the pass found
   was in `core/fill.py` rather than in any slot.
2. ~~**`wait_for` and event subscription**~~ — **done, and half of it deleted.**
   `keyhac/core/wait.py` is portable and polls. The empty matrix cell was
   filled first — Chrome, tree exposed, driven page change, Finder control
   passing in the same run: **nothing posted**, three times — and the observer,
   `wake=` and the pass were then removed on the strength of it (§5, Layer 1).
   Notifications never arrive for the content this workload targets, and the
   native-only win did not pay for the surface. No Windows counterpart is
   wanted. Still unmeasured, and now academic: a true Electron *application*
   rather than Chromium the browser.
3. **Two measurements, minutes each** — **the first is answered on both
   platforms** (§7.3: `set_value` works, focus being the precondition; ~5 ms on
   macOS, 15–33 ms on Windows), and it stays opt-in regardless for the
   framework-blindness reason. The second is **half answered**: Terminal.app
   yes, Notepad yes, but Windows Terminal, VS Code and iTerm2 are unmeasured and
   Notepad is a weak proxy for any of them (§6).
4. **Hand-write actions** ← do not skip. **Three of four done**, in
   `examples/actions/`, each runnable and verified against a live application:
   cross-system extraction (pagination, normalisation, partial failure, CSV,
   idempotent rerun), a queue handler (per-item branching, the three-beat modal
   cycle, per-step preconditions — it refuses a look-alike dialog rather than
   pressing its first button), and an error-line jump (the text layer, three
   rungs of §6's ladder). **Print-all-tabs-to-PDF is not written**: it drives
   print dialogs and writes files, so it wants its own session.

   **One has been carried to Windows; the rest are macOS-only in fact rather
   than by habit**, addressing elements as `AXTable` / `AXCell` / `AXWebArea`,
   which match nothing on Windows. What the port cost is the useful part: the
   *shape* survived unchanged — find the window by what it contains, enumerate
   the tab strip's own children, wait for the selection to be reported, restore
   the original tab — while every selector and every state read had to be
   rewritten, and one step could not be expressed at all until the element API
   grew a `SelectionItem` pattern. **Porting an action is a cheap way to find
   holes in the platform layer**, which is an argument for doing it early
   rather than once.

   It landed as a second file, `examples/actions/win/snapshot_settings.py`,
   not as branches in
   the first. **A generated action does not need to be portable and should not
   pay for it**: it is written against one screen that was inspected first, and
   the two accessibility vocabularies do not merge (`uitree.py` unifies role
   names exactly as far as the `AX` prefix and no further). What must stay
   portable is the framework and the config — §12's "a single config across
   Windows and macOS" is about the user's key bindings, not about selectors
   reaching into another application's tree. This narrows §11's open "how far
   to unify Windows and macOS" question: at the API, as far as it already goes;
   at the action, not at all.

   The exercise paid for itself in the way §5 predicts — nine findings, four of
   them bugs in the framework the actions are written against, all recorded in
   [`examples/actions/README.md`](../../examples/actions/README.md). The two
   that change how actions should be written: a DOM id reaches controls, tables
   and landmarks but **not a plain `<span>`**, so pagination state has to be
   addressed by its text or by the document title; and an accumulator declared
   inside the thing that can fail discards every page already read, which is
   the exact failure this class of action exists to avoid.
5. ~~**Derive the skill** from what step 4 taught (§8.4).~~ **Written**, in
   `keyhac/skills/action-authoring/`: `SKILL.md` (seven hard rules, each one a
   failure that actually happened, plus structure and the done-checklist),
   `references/api.md`, and `references/quirks.md` — the measured platform
   behaviour that makes correct-looking code wrong. The eval set is
   `evals/check.py` for the mechanical rules, calibrated in both directions
   (the four hand-written actions pass; a fixture breaking every rule is
   caught, and both are pinned by `tests/test_action_authoring_evals.py`), and
   `evals/cases.md` for the ten judgement-shaped cases. Untested against
   generation itself — that is step 6.
6. Try generation.

Mouse input capture and trace recording are not on this list.

These layers carry their own weight without any AI: key bindings that address elements by
name, waits that do not break on a slower machine, macro recording that eventually
captures the mouse. **If the AI side fails entirely, the investment still stands.**

---

## 11. Open questions — measure, do not deliberate

- ~~**Does `set_value` work on the specific internal systems being targeted?**~~ (§7.3)
  *Answered on both platforms* — macOS 2026-08-06 (~5 ms, Safari), Windows
  2026-08-07 (15–33 ms, Notepad). It works, focus being the precondition. It
  stays opt-in regardless, for the framework-blindness reason in §7.3, so the
  clipboard-preservation path remains on the critical path after all. Still
  worth re-running against a *specific* internal system before betting an
  action on it: what is measured here is that the mechanism functions, not that
  any given web app observes it.
- **Do the target terminals and editors implement the UIA Text pattern and `AXValue`?**
  (§6) *Half answered.* On macOS the one-keystroke path works: whole-value reads
  succeed on web content and text areas provided you descend to leaves (§6.1),
  and `AXLineForIndex` → `AXRangeForLine` → `AXStringForRange` returns the caret's
  line with no selection and no pointer — verified against a multi-line field.
  `element_at_point` resolves a form field but returns the wrapper group for a
  textarea, so the pointer path is coarser than the caret path.
  **Terminal.app: yes** — its `AXTextArea` returns the whole scrollback through
  `AXValue`, and `get_line_at_caret()` returns the prompt line, so the
  one-keystroke path works there too and `examples/actions/mac/jump_to_error.py`
  uses it. iTerm2 untested (not installed here).
  **Windows: yes, and now measured where it matters.** Notepad's editor answers
  `get_text()`, `get_line_at_caret()` (the caret's line, not the document) and
  `get_selection()`, with every vtable slot the Windows implementation guesses
  at pinned live (`tools/uia_pass.py`). **Windows Terminal** returns its
  scrollback as a `Text` element and one line at the caret; **VS Code** exposes
  the editor as an `Edit` named for the open file
  (`tools/text_pattern_survey.py`, 2026-08-07, with Notepad run alongside as a
  control so a null result could be attributed). So the cheap rung of §6's
  ladder holds on both platforms, and this question is closed — bar iTerm2,
  which is not installed here.

  One caveat that belongs with it: **the first read of an Electron window
  returns nothing.** VS Code offered 12 Text-pattern elements and no buffer on
  one probe, and 26 with the buffer minutes later, same code. Chromium enables
  renderer accessibility when a UIA client attaches and is not finished by the
  time that client's first read returns. Windows therefore needs no equivalent
  of macOS's `set_manual_accessibility()` — it needs a retry, which `wait_for`
  already is.
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

---

## 14. What is built (2026-08-07)

Against the layers in §5 and the sequence in §10:

| | State |
|---|---|
| Layer 1 — observation | Event subscription **dropped** after measuring (§5). Trace capture **not ours to build** — Claude Desktop records and narrates; §5 has the reasoning. |
| Layer 2 — state reading | **Done**, both platforms. `keymap.ui` + `UINode`, the text layer, verified live on macOS and Windows. |
| Layer 3 — execution safety | **Partial.** Waiting and read-back are in; per-step preconditions, checkpointing and idempotency are *patterns the actions follow*, not framework. `Action.preconditions()`, dry-run/preview, `Esc` cancellation and the long-action executor are **not built** — the `max_workers=1` pool is still the latent bug §2.1 names. |
| Layer 4 — action metadata | **Minimal.** `keymap.register_action(name, action)` and the MCP tool schemas; no argument schema, no description surface. |
| Layer 5 — artifact management | **Not built.** No `~/.keyhac/actions/*.py` discovery, no partial reload, no tool that writes Python to disk. Generated actions are pasted in by hand. |
| Topology A — MCP | **Done.** `keyhac/mcp/`: nine tools over loopback HTTP with a per-start token, off unless the config asks; `keyhac-mcp-bridge` for Claude Desktop. See `doc/mcp.md`. |
| Topology B — agent host | **Not built, and probably unnecessary** (§3.4). Nothing has needed runtime inference. |
| `LLMAction` | **Still undecided, and the evidence is in.** Six actions, none needed inference. §3.4 said decide after hand-writing; the honest next step is deleting it. |

**Step 6 has now run against a real client** (2026-08-08, Claude Desktop, two
actions), which is what §15 is drawn from. Both were authored by a model that
had not written the skill, against the operator's own screen:

1. *Open the Keyhac issue list in Chrome* — from a typed, intent-only prompt.
   Every hard rule satisfied; it could not be imported, because the skill
   documented no import header and `references/api.md` gave an exhaustive list
   of importable names that was missing two of them. Fixed in the skill.
2. *Save a set of pages as PDFs through Chrome's print dialog* — derived from a
   Claude Desktop **screen recording**, and the densest case in §2. The
   recording/selector split in `doc/mcp.md` held: no pixel addressing survived
   into the action, and its selectors are real AppKit identifiers read from the
   live tree.

The failure modes moved up a level between the two. The first produced names
that do not exist; the second produced an action that **states a correct
principle in its own docstring and then does not hold itself to it** — bounded
tree reads, resume-safety, wait-until-settled, each declared and each violated
somewhere. Mechanical checks catch the first class and cannot reach the second,
so `evals/cases.md` gains "does the code obey its own docstring?" as a scoreable
question.

---

## 15. Ideas to revisit (2026-08-08)

Captured from the first real generation sessions (§14). Evidence, not designs —
each names what was actually observed and the question that has to be answered
before building anything.

### 15.1 Installation should be executable, not readable

Getting a working setup took: enable the server in `config.py`, reload, find
the bridge's absolute path, write `mcpServers` into
`claude_desktop_config.json`, fully quit and reopen Claude Desktop, run
`make skill-bundle`, delete the previous skill, upload, wait for the security
scan. Nine steps across three applications, and `doc/mcp.md` describes all of
them correctly.

It still went wrong, because **the skill and the bridge are independent
installs and doing only one produces a confusing result rather than a broken
one.** With the skill uploaded and no bridge registered, Claude correctly
reports that it is knowledge with no execution environment and cannot see your
windows — which reads exactly like the feature not working. The reverse is
quieter and worse: tools without the skill work, and return actions full of
`sleep` and screen coordinates. `doc/mcp.md` now says so explicitly, which
helps a reader and does nothing for the several remaining ways to get this
half-done.

The idea: an instruction document an agent **executes** rather than one a human
follows — locate the bridge, patch the client config, verify by driving
`tools/list` end to end, report what it could not do itself. Most of it is
mechanical and was in fact done that way in the session that found the problem.

Open questions. Which client — the config path, the schema and the
restart requirement are all Claude Desktop's, and an "install for any MCP
client" document is a different and much larger claim. What the agent must
refuse to do unattended: editing another application's config file is
reasonable with a backup; quitting that application is not. And whether this
belongs in the repository or **in the skill itself** — a skill that installs
its own transport is circular, since a user with no bridge is a user whose
agent cannot verify its own work.

### 15.2 Editing `config.py` is a step the skill still hand-waves

f6cf4e0 gave the skill the file header, the `extensions/` placement and the
`configure()` registration block, so a generated action now says where it goes.
What it does not address: **no tool writes Python to disk, deliberately** (§4.4
and `doc/mcp.md`), so the operator is the transport. They paste a class into
`~/.keyhac/extensions/`, then paste three more lines into the middle of a
config file that is theirs, several hundred lines long, and already working.

Observed: an instruction of mine to delete a line range removed the `import`
the registration depended on, and the config stopped loading. The failure was
loud and the previous keymap stayed active — the containment in
`Keymap.configure()` did its job — but it is the second time a human hand-edit
between two machines has been the thing that broke.

Two directions, and they are not the same size:

- **Cheap**: the skill emits an exact, self-contained block with an anchor
  comment, so the paste is unambiguous and re-running it is idempotent.
- **Real**: Layer 5. `~/.keyhac/actions/*.py` discovered and registered by
  filename, and `config.py` never needs editing to add an action at all. The
  reason to hesitate is not effort — it is that auto-registration makes
  `run_action`'s surface "every file in a directory" rather than "what the
  operator named", which is the line §4.4 draws on purpose.

Answer the second before investing in the first: they solve the same problem
and only one of them survives.

### 15.3 `run_action` returns logs, not output

Verified in `keyhac/mcp/tools.py`: `_captured_log` installs a
`logging.Handler` on the `keyhac` logger for the duration of the run. So an
action using the documented `getLogger("MyAction")` is captured — and three
things are not:

- **`print()`**, which the shipped `config.py` template teaches on the same
  line as the logger ("print() and the logger both reach the console window").
  It reaches the console window; it does not reach the model.
- **Anything logged to a logger outside the `keyhac` tree**, which is what
  `logging.getLogger(__name__)` produces in a module under `extensions/`.
- **Subprocess `stderr`.** These actions shell out — `open`, `osascript` — and
  a `CalledProcessError` carries stderr only if the action captured it.

Each of those is a debugging line the operator can see and the agent cannot,
and the whole point of `run_action` is that the model reads its own failure.
The fix looks small (`redirect_stdout` / `redirect_stderr` around the same
block, root logger rather than `keyhac`), and the questions are about what that
sweeps in: a root-logger handler catches every library the action imports, and
capturing stdout for the duration of a run on the loop thread captures whatever
else logs on that thread in the same window. Bound the output, and say in the
result when it was truncated.

### 15.4 A failing action should be able to hand the agent its own trace

Today the loop is: the action fails, the operator notices something did not
happen, opens the Keyhac console, finds the traceback, copies it, pastes it
into a conversation — assuming the conversation that authored it still exists.
Every one of those steps is a place where the report does not get made, and an
action nobody reports is an action that stays broken.

The idea is a returning channel: Keyhac keeps the last failure per registered
action — traceback, the log around it, which step, what was on screen — and an
MCP tool serves it, so "the PDF one failed this morning" is enough for the
agent to fetch the trace itself and propose a fix. The operator's side of that
becomes one sentence rather than a copy-paste.

This is mostly a **convention** question, not a plumbing one. `run_action`
already returns everything an action logged; what is missing is the same
richness on the path where the action runs from a *key press*, and a norm about
what actions log. §7 asks for "report what to redo, not that something failed";
this extends it to "report enough that the agent can act without the screen" —
which selector was being looked for, what was found instead, which item of how
many. The generated actions already do some of this well.

Open questions. Where the record lives, and for how long — a failure record
holds window titles and element names, which is exactly the material §9's trace
privacy rules cover, and the retention answer there was deliberately
conservative. Whether the operator is prompted at all, or the record simply
exists for an agent that asks. And whether a repeated failure should surface a
notification, given that Keyhac's whole posture is to stay out of the way.

### 15.5 A command line onto the daemon, over the transport that already exists

Running an action has three entry points and they are not equally available.
A key press needs the action bound. MCP `run_action` needs a chat client with
the bridge registered — §15.1's nine steps. The third, `tools/run_action_file.py`,
needs neither and is the one that cannot ship, because a bare interpreter cannot
use the Accessibility permission granted to `Keyhac.app`: `doc/dev/packaging.md`
is explicit that it "would grant the permission to that interpreter, not to
Keyhac". What it borrows instead is the grant held by whatever is responsible
for the shell — Terminal, the IDE, the agent's host — which is a far wider
authorisation, since it covers every process that shell will ever spawn, and it
reaches the UI and the keyboard without passing any of §4.4's gates.

The idea is the fourth: a small command-line client that speaks the **same
loopback HTTP and per-start token as the MCP server** and asks the running
daemon to run a registered action. Both objections above dissolve at once, and
not by argument — by construction. The work happens inside the process that
already holds the permission (§4.3's first bullet), so nothing new is
authorised; and because it is the same endpoint, "off unless the config asks",
the loopback bind and the token apply unchanged rather than being reasoned about
again.

What it buys that MCP does not: an operator, a shell script, or an agent with
`Bash` and no bridge gets the run-read-fix loop without a chat client — which is
exactly the half-installed state §15.1 describes, and the alternative it
currently pushes people toward is granting their terminal Accessibility. It also
gives §15.3 a second consumer, which raises the value of fixing the capture
rather than changing it.

Constraint inherited from §4.3: like the bridge, it must be a thin client — no
tool definitions, no logic — or the two diverge and are maintained twice.

Open questions. It reaches only *registered* actions, so §15.2's "the operator is
the transport" is untouched, and the two interact if Layer 5 ever lands. The
token is already readable by any local process that can read the bridge's copy,
so a CLI adds a consumer rather than surface — but it makes that file's
permissions load-bearing in a way worth stating rather than inheriting silently.
And whether it ships in the bundle or stays in `tools/`: shipping is what makes
it useful to an operator, and unlike the file runner there is no permission
argument against it.

# Keyhac AI Integration — Design

Why the feature is shaped the way it is: the decisions, the measurements behind
them, and the alternatives that were rejected and should not be re-proposed
without new evidence.

**Audience:** coding agent (Claude Code) working in `crftwr/keyhac`
**Related:** [`doc/ai-integration.md`](../ai-integration.md) is the user-facing
half — turning the endpoint on, what it reaches, the security posture.
`keyhac/mcp/tools.py` is the tool surface itself. `CLAUDE.md`,
`doc/configuration.md`, `doc/dev/`.

**Open work** is tracked in the GitHub issues under the `ai-integration` label,
not in this file.

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

**Background execution is a real requirement.** Tens-of-minutes actions demand
it, and the `ThreadedAction` pool in `keyhac/core/action.py` originally ran
`max_workers=1`, so one long run blocked every other action in the app — a
latent bug independent of any AI work. Fixed; Layer 3 in §5 records why the
answer turned out to be a lock on the clipboard rather than a second executor.

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
- local-model latency measurement (§10) drops off the list
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

**How the bridge is *launched* is per-install, and the Store build dictated its
shape.** `pip install` generates a console script; the zip bundle has no pip, so
`build.ps1` emits one. That was a `.cmd` setting `PYTHONPATH` and running the
embedded interpreter — until the MSIX install, where nothing under
`C:\Program Files\WindowsApps` can be started by a process that is not part of
the package: every attempt is "Access is denied", whatever the ACL says, and the
files remain perfectly *readable* the whole time. So the Store build's bridge
could not be launched by anything, and Claude Desktop's server exited at startup
with no output to explain it.

The supported way in is an **app execution alias**: the package declares one
(`windows.appExecutionAlias`), Windows registers a stub on `PATH`, and launching
it starts the target with package identity — which may then run the packaged
interpreter. An alias can only name an `.exe` and cannot carry arguments, so
`-m keyhac.mcp.bridge` had to be baked into a real executable
(`windows_app/src/bridge.c`); the `.cmd` survives only as a forwarder, for
configs written against 2.2.0–2.2.2. `bridge_command()` therefore asks Windows
whether this process has package identity rather than inspecting paths, and a
packaged Keyhac publishes the alias — or, if the user has turned the alias off,
publishes nothing. **Existence is not the test on that platform**: the copy
inside the package exists and can never work.

### 4.4 Access control is not optional

Listening on localhost means every process on the machine can reach an API that reads the
UI tree and injects keystrokes. An application that argues it is not a keylogger cannot
ship an unauthenticated local endpoint offering key injection.

- Unix socket → filesystem permissions; HTTP → localhost bind plus a token generated at
  startup and read by the bridge
- **Disabled by default**; enabled explicitly in config
- "No unauthenticated local endpoint" is a standing principle, alongside "no always-on
  collection" (§11)

**One switch, and it expires.** Writing into `extensions/` briefly had a control
of its own, on the argument that a capability worth leaving on for days and one
worth leaving on for minutes cannot share a switch without the long-lived one
setting the price. **The sizes in that argument were backwards, and it is
withdrawn.** The endpoint is not worth leaving on for days: §1 is explicit that
the model is used at *authoring* time and the action then runs without one, so
an endpoint still listening the next morning is serving nothing — while still
able to read every window that is open, which is the largest exposure here and
was the one with no deadline on it. Splitting also produced a state nobody
wanted, where the agent could read screens but not do the thing it is for.

So the deadline belongs on the endpoint itself: ticking the switch opens it for
:data:`_AUTHORING_WINDOW`, after which it stops listening and deletes its token.
Nothing below it checks a permission, because being reachable *is* the
permission. The rule this leaves for the next capability is simpler than the one
it replaces: **do not add a second switch — ask whether the first one should be
open at all when this capability is not wanted.** If the answer is no, it
belongs inside the same window.

It is deliberately not persisted, for the same reason. A restart is one more
thing that closes it, and restoring it at start-up would be the one path back to
an endpoint nobody remembers arming. That does leave `--no-ui` unable to open it
at all, which is the honest shape rather than a gap: authoring happens where the
operator can see the switch.

### 4.5 Keep the server off the action executor

The MCP server needs its own thread and a small loop of its own. Do **not** route tool
calls through the `ThreadedAction` pool — with `max_workers=1` a single long-running
action would stall every incoming tool call, and conversely a burst of tool calls would
starve actions. This is a separate concern from the long-action executor in §2.1;
resolve them independently.

---

## 5. The five layers

Ordered by dependency. Two of them were deliberately not built, and the reasons
are the useful part.

### Layer 1 — Observation

Keyhac records *input*, not *what happened*: macro recording captures keys only.
The layer splits in two, and **neither half is Keyhac's to build.**

**Not built — event subscription.** An earlier draft assumed `wait_for` would be
built on it. It is not. An `AXObserver` wrapper was written, measured, and then
removed (along with `wake=` and `tools/ax_notification_pass.py`); the code is in
git history if the conclusion is ever revisited. What the measurements said:

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

**Not built — mouse input capture and trace recording.** Three findings, and
they compound. Of the concrete use cases catalogued in §2, almost none are
authored from a recorded demonstration. Most cases that appear to need one are
served by having the user *open* a UI state and letting Claude read it, which
records nothing (§8.1). And the client records a task itself — screen, clicks,
typing and **voice** — turning it into a skill. That answers rung 4 from
outside, and it answers the two objections to building one here:

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

What the layer adds is unification and exposure, not green-field
(`keyhac/core/uitree.py`, plus `children()` / `describe()` / `identity_key()` on
both platform elements):

- **Windows child traversal** — `children()` walks `GetFirstChildElement` /
  `GetNextSiblingElement`, slots that were declared in `win/uielement.py` and
  never wrapped.
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

**Cancellation.**
  `ActionCancelled` derives from `BaseException`, which is the whole design: an
  action of this class wraps each item in `except Exception` to survive partial
  failure, and an ordinary exception would be filed there as "item 7 failed" and
  the run would continue — the one thing cancelling must not do. `wait_for`
  raises it at the top of each poll, so an action needs no line about it;
  `check_cancelled()` covers a stretch with no wait in it. Only `kind == "real"`
  cancels: Keyhac's own translated output never reaches `on_key_event` (the
  platform layer drops it on its own tag) and replay is excluded on purpose, so
  an action pressing Escape cannot kill itself and a macro cannot kill it either.
  Esc is consumed only when it actually stopped something.
**One executor, not two.** A second pool and an `AsyncAction` were both proposed
and neither was needed. §3.4 killed the `AsyncAction` branch (no runtime
inference ever appeared), and the "second executor" branch was answering the
wrong question: `max_workers=1` was not what kept concurrent actions safe —
injected keystrokes are serialized by the engine lock
(`InputContext.__enter__` takes it) and AX access by `call_on_main_thread`. The
only thing the pool's shape protected was the clipboard save/restore in
`core/fill.py`, which now holds a reentrant lock of its own — reentrant because
`_paste` opens that context inside a caller that already has. So: raise the
worker count, and lock the one genuinely shared resource. **No `long_running`
flag, and nothing for the skill to teach** — a flag would ask the author to
classify work whose duration they cannot know (`extract_records` is seconds
against a fixture and tens of minutes against a real system, same code), and
getting it wrong would be silent.

*Cost:* two key bindings that used to queue can now overlap. Each `with ctx:`
batch stays atomic, so typing cannot interleave mid-batch, but two typing
actions started at once will interleave batches.

**Preconditions, dry run and a progress journal remain patterns rather than
framework.** `Action.preconditions()` and `describe()` / `preview()` were in the
original design and are not built; the authoring skill states them as hard rules
instead, so actions follow them by convention. When they are built,
checkpoint-and-resume comes before rollback (§2.1): clipboard writes, window
moves and text insertion are genuinely reversible, but writes already accepted
by a remote system are not, and for the §2 workload those dominate.

The output-side primitives these actions are built from — `wait_for`, form
filling, pagination — are specified in §7.

### Layer 4 — Action metadata

Name, description, argument schema. Needed both to invoke actions from ① and to list
them over MCP — the shape is close to an MCP tool definition, so one implementation
serves both. The tools Claude needs while *writing* an action are a separate surface;
see §8.3.

### Layer 5 — Generated artifact management

The original sketch here was *auto-register every file in a directory*. It was
rejected twice over: it makes `start_action`'s surface a directory listing
permanently, and it has to **execute** each file in order to enumerate the
classes in it — creating the auto-execution `extensions/` has never done. The
other rejected alternative was running an action from source held in memory,
never touching disk; that loses the property that what you tested is what you
ship (module identity, import resolution and tracebacks all differ), and "it
worked in memory and failed as a file" is precisely the failure class this
feature exists to remove.

What exists instead:

- **`write_extension`** puts one generated module under `~/.keyhac/extensions/`.
  The fence is the *module name* — an importable name has no separator and no
  `..` in it, so validating it as an identifier confines the write by
  construction rather than by keeping a list of bad characters complete. Source
  is compiled before the file is touched, so a truncated transfer cannot replace
  a working action with one that will not import. Previous versions survive as
  timestamped `.bak-`, capped at five. The header comment keeps **the intent
  description only** — never the source trace, which would put trace fragments
  into git (§9).
- **`read_extension`** exists because the write tool replaces the *whole file*.
  An agent asked to change an action it has not read must reconstruct the module
  from a guess, and whatever it did not guess is gone — quietly, with only a
  `.bak-` to show for it. That is a data-loss shape, not a convenience gap, and
  it applies to exactly the case the loop is otherwise unexercised on:
  maintaining an action from an earlier session. Read and write share the one
  fence (`_module_path`), and an oversized file is refused rather than
  truncated — half a read feeding a whole-file write is how the other half
  disappears.
- **`delete_extension`** is a rename into the same `.bak-` scheme, so the tool
  that sounds destructive is the only one here that destroys nothing. It
  deliberately does **not** touch `config.py`: deleting a module the operator
  bound to a key stops their file loading, and editing their file to match would
  be worse than warning about it, so the reply carries a loose `\bname\b` search
  of `config.py` instead. The errors are not symmetric — a false positive costs
  a sentence, a false negative costs a config that will not load. Live state is
  left alone for the same reason: an imported class keeps running out of memory,
  so a run started before the delete stays readable and cancellable, and the
  operator's key works until they reload.
- **Discovery is an AST scan**, which is what made this layer safe to build at
  all. `ast.parse` gives the catalogue without executing anything, so listing is
  free and a class runs at exactly one moment: when something names it. Every
  class under `extensions/` is therefore runnable as `module.Class` with no
  `config.py` edit — which is why `register_action` was **removed** rather than
  kept beside it. Its whole job was to add a line that no longer has to exist,
  and keeping it would have left two ways into the same process, one of them
  permanent and invisible in the UI.
- What a `config.py` still does is **bind a key**, which registration never
  provided. That edit is the operator's, and it comes last — landing on
  something already shown to work, rather than being the price of finding out.

All of it sits inside the endpoint's own window (§4.4): with the switch shut,
`start_action` reaches nothing at all. The registry once proposed as a fence —
"a module the operator never named should have no path to execution" — is
therefore not built. Its argument was against a *time-unbounded* version of
this; the window is the difference. If the window turns out to be the wrong
fence, the registry is where to go next.

**Not built:** partial reload. A file re-imports itself on mtime, so nothing has
needed it.

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
  (§12), but reading elements is main-thread work. So each poll hands the
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
§10 question about `set_value` is answered on both platforms and none of it
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
start_action(name)        → start it, return at once
get_action_result(name)   → wait up to a bound, return output/errors
cancel_action(name)       → stop it, as the operator's Esc does
get_recent_trace(...)     → only after explicit recording and human approval (§9)
reload / partial reload
```

Running an action closes the generate-verify loop. Without it the human manually runs
each attempt and pastes the error back, and loop iteration rate — which partial reload
protects — is dominated by that manual step instead.

Running is asynchronous by design, not as a fallback. §2's actions run for minutes, the
endpoint answers one JSON message per request with no stream to push progress over, and
the bridge caps a call at 60 seconds — a single synchronous run-and-return call would
answer with a transport error for exactly the class of work it exists to serve, while
the action carried on invisibly. Blocking briefly and degrading to polling is worse
still: two reply shapes whose selection hinges on how fast the action happened to be,
the least predictable thing available.

### 8.4 The authoring skill

A skill is needed for intent (and, at rung 4, trace) → generalised `Action`. Three kinds
of knowledge, with different homes:

| | Where | Why |
|---|---|---|
| Keyhac API reference | skill `references/` | Large; load on demand. **Built as three, on the axis of what can be generated**: `action-api.md` is the generated signatures, copied into the bundle rather than restated by hand — the half that drifts. `practice.md` is which call to reach for and what it costs, and `quirks.md` where the platform lies; neither can be generated, which is why both are short and hand-written. The config-side reference stays out and is linked at a version-pinned URL: an action needs four of its thirty-four names |
| Trace schema | skill body | Short; always needed |
| **Generalisation heuristics** | **skill body — the core** | Procedural knowledge; the only part genuinely worth writing |

Ship it in-repo (`keyhac/skills/keyhac-action-authoring/SKILL.md`) so it versions with the API.
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

## 10. Measured behaviour, and the contracts it settled

Measurements, not opinions — each of these was run rather than reasoned about,
and several overturned what the design assumed.

### The `UINode` contract

This is the ceiling on action expressiveness, and changing any part of it breaks
every action already written. Settled in three parts, and pinned in `CLAUDE.md`
as well.

**How far Windows and macOS unify:** at the API, as far as it already goes; at
the action, not at all. Porting one action across platforms established this —
the *shape* survived unchanged (find the window by what it contains, enumerate
the tab strip's own children, wait for the selection to be reported, restore the
original tab) while every selector and every state read had to be rewritten, and
one step could not be expressed at all until the element API grew a
`SelectionItem` pattern. A generated action does not need to be portable and
should not pay for it: it is written against one screen that was inspected
first, and the two accessibility vocabularies do not merge (`uitree.py` unifies
role names exactly as far as the `AX` prefix and no further). What must stay
portable is the framework and the user's config, not selectors reaching into
another application's tree.

**Element identity:** address by `identifier` (DOM id / AXIdentifier /
AutomationId) where there is one, since it survives relabelling and localisation
(§6.1), then by role plus name or text. The known limit: a DOM id reaches
controls, tables and landmarks but **not a plain `<span>`**, so pagination state
has to be addressed by its text or by the document title. `identity_key()` is a
different thing entirely — the raw platform ref, used by exactly one caller for
the DAG dedupe in `get_ui_tree`, and not public shape.

**Handle lifetime is snapshot.** A `UINode` records what an element was; the
screen moves on and the node does not notice; `reread()` refreshes one
deliberately. The alternative — nodes that quietly re-read themselves — was
rejected because it hides exactly the change §3.7's preconditions exist to
catch, and because the three-beat pattern in §7.2 already re-finds rather than
re-uses.

Settling it turned up a bug rather than a design hole. A dead element reports no
actions, so `press()` on a closed dialog's button raised `FillFailed("element
supports no press action")` — true, and the least useful true thing to say,
since it points the operator at their selector when the screen had simply moved.
Worse on macOS, where `perform_action` discarded the AXError entirely. Hence
`StaleElement` beside `WaitTimeout` / `FillFailed` / `ActionCancelled`,
`is_stale()` on both platform elements (a fact; the policy stays in core), and
`perform_action` returning a bool on macOS as it already did on Windows. The
distinction the type buys is §3.7's: *the screen moved* is re-findable, *the
selector is wrong* is regenerate-the-action.

On Windows `is_stale()` matches **any** failure HRESULT rather than the named
constant, and that is measured rather than assumed: a control destroyed
underneath us returns `E_UNEXPECTED` (0x8000FFFF) for its first ~90 ms and only
then settles on `UIA_E_ELEMENTNOTAVAILABLE` (0x80040201), stably and in both
sampling orders. Matching only the named constant answered `False` during
exactly the window that matters — the moment just after a dialog closed.

### `set_value`

Works on both platforms, focus being the precondition: ~5 ms on macOS (Safari),
15–33 ms on Windows (Notepad). It stays **opt-in** regardless, for the
framework-blindness reason in §7.3, so the clipboard-preservation path stays on
the critical path. What is measured is that the mechanism functions, not that
any given web app observes it — re-run it against a specific internal system
before betting an action on it.

### The text layer

The cheap rung of §6's ladder holds on both platforms.

*macOS.* Whole-value reads succeed on web content and text areas provided you
descend to leaves (§6.1), and `AXLineForIndex` → `AXRangeForLine` →
`AXStringForRange` returns the caret's line with no selection and no pointer.
`element_at_point` resolves a form field but returns the wrapper group for a
textarea, so the pointer path is coarser than the caret path. **Terminal.app**
returns its whole scrollback through `AXValue` and `get_line_at_caret()` returns
the prompt line, which `examples/actions/mac/jump_to_error.py` uses. iTerm2 is
untested — not installed here.

*Windows.* Notepad's editor answers `get_text()`, `get_line_at_caret()` (the
caret's line, not the document) and `get_selection()`, with every vtable slot
the Windows implementation guesses at pinned live (`tools/uia_pass.py`).
**Windows Terminal** returns its scrollback as a `Text` element and one line at
the caret; **VS Code** exposes the editor as an `Edit` named for the open file
(`tools/text_pattern_survey.py`, with Notepad run alongside as a control so a
null result could be attributed).

**The first read of an Electron window returns nothing.** VS Code offered 12
Text-pattern elements and no buffer on one probe, and 26 with the buffer minutes
later, same code. Chromium enables renderer accessibility when a UIA client
attaches and is not finished by the time that client's first read returns.
Windows therefore needs no equivalent of macOS's `set_manual_accessibility()` —
it needs a retry, which `wait_for` already is.

### Still unmeasured

- **Real AX/UIA tree size and retrieval cost** across Electron apps, VS Code and
  browsers. That is what would set the default depth and filter, which are
  currently a guess that works.
- **MCP sampling** is a bonus, not a dependency. Topology A needs none of it. It
  would only decide whether runtime inference could ride the user's subscription
  instead of topology B's API key, and §3.4 argues runtime inference is
  unnecessary anyway. Expect "no": as of early 2026 Claude Desktop does not
  support sampling (VS Code's MCP client is the notable one that does). If it
  ever matters, stand up a minimal server and send a request rather than
  deliberating.
- **Local model latency**, and only if runtime inference ever returns. Watch for
  the silent partial-CPU-offload trap: a context window larger than available
  VRAM degrades speed first, and under a tight budget that surfaces as timeouts
  and truncated JSON — structured-output failure arriving indirectly.

---

## 11. Non-goals

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

## 12. Constraint that must not be violated

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


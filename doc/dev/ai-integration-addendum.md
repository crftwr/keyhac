# Keyhac AI Integration — Addendum

**Supplements:** `doc/dev/ai-integration.md`
**Status:** design notes from a follow-up session; supersedes the parent document where
noted in §0

This document contains only material not already in the parent. Read the parent first.
Where the two conflict, this one wins.

---

## 0. Retractions and re-prioritisations

Four changes to decisions already recorded in the parent document.

**Ring buffer — retracted.** The parent's §4 "Deferred / uncertain" recommends a
continuously-running ring buffer with after-the-fact extraction, on the grounds that it
avoids making the user declare "I am about to demonstrate". That reasoning ignored
privacy. A trace is by construction keylogger output: it will eventually contain
passwords, API keys, confidential record contents, and private messages typed in
passing. No amount of opt-in, memory-only handling makes "you cannot predict when a
secret enters the buffer" acceptable, and the user has no way to verify otherwise. See
§5.

**Trace as a header comment in generated actions — retracted.** The parent's §4 Layer 5
suggests keeping the source trace as a header comment so an action can be regenerated.
This puts trace fragments into git. Keep the intent description only.

**Layer 1 (mouse input, trace capture) — demoted.** Two independent findings push this
down. First, of the concrete use cases now catalogued, almost none are authored from a
recorded demonstration (§2, §4). Second, most cases that appeared to need a
demonstration are served by having the user *open* a UI state and letting Claude read it,
which records nothing (§4). Layer 2 is where the leverage is; do that first.

**`LLMAction` — probably not needed at all.** See §6.

---

## 1. What the interesting actions actually are

The parent frames the target as key-binding-triggered automation. The concrete cases that
survived scrutiny are narrower and more specific than that:

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

None of these need runtime inference.

## 1.1 Consequences the parent does not cover

**Scale changes the failure model.** These are hundreds of items over tens of minutes,
not twenty items over one. A read that fails leaves nothing behind; a write that fails
halfway leaves a partial mutation. Therefore:

- **Checkpoint and resume matter more than undo.** The parent's Layer 3 lists an undo
  journal. For this class of work, rollback is usually not achievable — the remote system
  has already accepted the writes. Record *how far it got* in a form precise enough to
  resume. Design the journal as a progress log first and a rollback mechanism second.
- **Preconditions belong on each step**, not only on the action.
- **Report progress in terms of what to redo**, not a bare error.

**Idempotency is a recurring requirement** (diff-apply, bulk submission,
reconciliation). The rule that makes it work is mechanical: **read the current value
before writing.** A checkbox pressed blindly toggles rather than sets. Put this in the
skill as a hard rule.

**Background execution becomes a real requirement.** The parent defers `AsyncAction`
partly because runtime LLM calls turned out to be rare. That reasoning does not cover
tens-of-minutes actions, which this class of work demands. Combined with the verified
`max_workers=1` pool in `keyhac/core/action.py`, a single long run blocks every other
`ThreadedAction` in the app — this is already a latent bug today, independent of any AI
work. At minimum a separate executor for long-running actions is required.

**External format I/O.** CSV and JSON are stdlib. Decide explicitly whether spreadsheet
libraries become a Keyhac dependency or are left to the user's environment.

---

## 2. Two layers of state, not one

The parent's Layer 2 is written as though the accessibility tree is the way to read the
screen. It is not, and the distinction determines the API shape.

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

**API additions this implies:**

```
get_selection(window_id)
get_text(element_id)              # AXValue / UIA Text pattern
get_line_at_caret(element_id)
element_at_point(x, y)
```

**Measurement to add to the parent's §7:** how far do the target terminals and editors
actually implement the UIA Text pattern and `AXValue`? Test Terminal.app, iTerm2, Windows
Terminal, VSCode, Chrome. If whole-value reads work, selection becomes optional and the
one-keystroke path above is available. If they all fail, selection is mandatory and the
ergonomics change.

**On OCR:** not needed. The gap it would fill is the pixel layer, which is not where this
work happens, and it conflicts with three standing principles at once — it is
probabilistic (§2.6 prefers stopping over silently being wrong), it reintroduces
coordinates (§5 treats coordinates in generated code as a failure), and preconditions
cannot be expressed probabilistically. If a screen capture is ever wanted, it is for
*authoring* — letting Claude look at a window with its own vision — and that needs
`capture_window()`, not a local OCR engine. Even then, authoring-time only.

---

## 3. The output side

The parent treats actions as things that read state and then act. The acting is where the
difficulty concentrates, and it is barely specified.

### 3.1 Waiting is the core primitive

Nearly every step in an output-side action is "act, then wait for something to change".
Modal open, modal close, re-render after a dependent field changes, page load after
pagination, application ready after launch. **`sleep` produces environment-dependent
breakage** — it passes on the developer's machine and fails on a slower or faster one.

```
wait_for(condition, timeout)        # appearance, disappearance, value change
wait_for_stable(element, timeout)   # re-render settled
```

Make "a generated action containing `sleep` is a failure" a hard skill rule, at the same
level as the existing rule about coordinates.

This is what makes AX notification / UIA event subscription (parent §4 Layer 1) load
bearing even though the rest of Layer 1 is demoted. **Split Layer 1: event subscription
is required for output actions; mouse input capture and trace recording are not.**

### 3.2 Menus and modals are transient

An element that appears and then disappears cannot be located by a static search done in
advance. The pattern is always three-beat: wait for appearance, act, wait for
disappearance before proceeding. Getting the third beat wrong is what breaks iteration
over modals — the next cycle starts before the previous one finished.

### 3.3 Form filling has three mechanisms and they are not equivalent

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

**Measurement to add:** does `set_value` actually work on the specific internal systems
being targeted? One form, five minutes, and the answer settles whether the
clipboard-preservation path is on the critical path.

### 3.4 Pagination deserves an abstraction

"Follow next while next exists" appears in every extraction case. Expose it as a
generator so generated code has one obvious correct shape rather than N hand-rolled
loops. Termination detection and the zero-results case are where hand-rolled versions go
wrong.

---

## 4. Authoring: what Claude needs, and when a demonstration is required

The parent's §5 assumes the input to action generation is a trace. That is wrong for most
cases. **Traces capture form, not intent.** A demonstration of "extract the ID and build
a command" shows only that the human typed a string; the transformation happened in their
head and never reaches the keyboard.

There are three input kinds, and they mix:

- **Current UI structure** — what element to address, what its identifier is
- **Natural-language intent** — the transformation rule, the branch condition, what is
  variable
- **Trace** — iteration unit, actual latency points, human judgement encoded in ordering

### 4.1 The escalation ladder

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

Rung 4 is an exceptional path, not the main mechanism. Build it last.

### 4.2 After a trace, ask before generating

When rung 4 is used, the skill must require a confirmation step. A trace shows that the
user set a status filter to "active"; it does not show whether that is a constant or an
argument. **Demonstration → clarifying questions → generation.** Never
demonstration → generation.

### 4.3 Authoring-time tools are a distinct API surface

The parent's Layer 4 covers metadata for invoking actions. It does not cover the tools
Claude needs while *writing* one. These are a different surface with different security
properties and should be designed separately from the runtime tools:

```
run_action(name)          → execute and return output/errors
get_recent_trace(...)     → only after explicit recording and human approval (§5)
reload / partial reload
```

`run_action` closes the generate-verify loop. Without it the human manually runs each
attempt and pastes the error back, and loop iteration rate — which the parent correctly
identifies as what partial reload protects — is dominated by that manual step instead.

### 4.4 Prompt quality is the skill's test

If a user's prompt has to name `get_ui_tree` or `set_value`, the skill has failed. API
names in a prompt are a skill deficiency. Domain knowledge in a prompt is normal and
irreducible.

What belongs in the skill, permanently: inspect the current structure before writing;
address elements by name/role, never coordinates; `wait_for`, never `sleep`; read back
after writing; read before toggling; per-step preconditions; report progress and support
interrupt/resume.

What legitimately stays in the prompt: what the user wants, output paths and naming
rules, which system, column correspondences, desired failure behaviour.

**Use this as an eval axis:** does the intended action come out from an intent-only
description? During the early period the prompts will necessarily be verbose because the
skill does not exist yet — **keep the prompt logs; whatever the human keeps repeating is
the skill's TODO list**, and shrinking prompts is the signal that it is working.

---

## 5. Trace privacy

Applies only to rung 4. Rungs 1–3 record nothing and need none of this.

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
- **Never written to disk**, never embedded in generated artifacts.

The review step is not purely a privacy cost: users spot retries and stray operations and
delete them, which improves generation accuracy. It pays for itself twice.

Extend the parent's §8 privacy bullet and `PRIVACY.md` with the above.

---

## 6. Runtime inference may be unnecessary entirely

The parent's §2.3 concludes that runtime LLM use is confined to natural-language
processing, and §2.4 introduces `LLMAction` to contain it. Two further observations
close the gap:

**The natural-language use cases are already taken.** The one runtime-LLM example in the
earlier list was generating a commit message from a staged diff — which Claude Code
already does better, inside the editor. The same holds for translation and
summarisation: dedicated tools exist with better ergonomics. Claude Code owns the inside
of the editor; competing there produces a worse version of something the user already
has.

**Outside the editor, natural language barely appears.** Every case in §1 is form
filling, table reading, navigation, aggregation, and file output. None of it has
natural-language input. That the entire catalogue came out as pure Python is not a
coincidence — it is what the domain looks like.

If this holds, several things simplify:

- `ctx.llm()` / `llm_choose()` / `llm_json()` are not needed
- local-model latency measurement (parent §7) drops off the list
- the privacy story becomes unconditional: *no inference happens at runtime*
- **topology B may be unnecessary**, since its justification was key-triggered agent
  runs. If key bindings only ever invoke pure-Python actions, topology A suffices and the
  API-key handling disappears with it

**Do not delete the class yet.** Hand-write the first actions and see whether the need
appears. If it has not appeared by then, remove it — and note that "built with AI, runs
without it" is a genuinely defensible position, competing with neither Claude Code nor
the crowded computer-use field.

---

## 7. Process architecture — the MCP server cannot stand alone

The parent describes a thin bridge but does not say why the work cannot simply live in
the bridge. It cannot, for three reasons:

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
tool definitions, no logic — otherwise versions diverge and must be maintained twice. The
bridge exists solely because Claude Desktop's local server config is stdio-only; Claude
Code attaches to the HTTP endpoint directly.

### 7.1 Access control is not optional

Listening on localhost means every process on the machine can reach an API that reads the
UI tree and injects keystrokes. An application that argues it is not a keylogger cannot
ship an unauthenticated local endpoint offering key injection.

- Unix socket → filesystem permissions; HTTP → localhost bind plus a token generated at
  startup and read by the bridge
- **Disabled by default**; enabled explicitly in config
- Add "no unauthenticated local endpoint" to the parent's §8 as a standing principle,
  alongside "no always-on collection"

### 7.2 Keep the server off the action executor

The MCP server needs its own thread and a small loop of its own. Do **not** route tool
calls through the `ThreadedAction` pool — with `max_workers=1` a single long-running
action would stall every incoming tool call, and conversely a burst of tool calls would
starve actions. This is a separate concern from the `AsyncAction` question in §1.1;
resolve them independently.

---

## 8. Revised first moves

Superseding the parent's §6 where they differ:

1. **Layer 2 exposure** — Windows child traversal (`GetFirstChildElement`, already
   declared and unwrapped in `win/uielement.py`), `get_ui_tree`, `find_element`, plus the
   text-layer accessors from §2. Settle the tree API shape first, as the parent's §7 says
   — but settle it by walking a real page by hand, not on paper.
2. **`wait_for` and event subscription** — the Layer 1 split from §3.1. Everything on the
   output side depends on it.
3. **Two measurements, minutes each** — does `set_value` work on the target systems
   (§3.3); do the target terminals implement whole-value text reads (§2).
4. **Hand-write actions.** Recommended set and order: cross-system extraction (exercises
   pagination, normalisation, partial failure, file output); print-all-tabs-to-PDF
   (modal traversal, iteration, waiting, resume); a dialog handler (preconditions); an
   error-line jump (the text layer, in contrast to the tree).
5. Derive the skill from what step 4 taught, per the parent's §5.

Mouse input capture and trace recording are not on this list.

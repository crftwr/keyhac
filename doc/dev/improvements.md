# Improvements — measured in real authoring sessions

Where friction found while actually authoring actions gets written down before
it is forgotten. Everything here was paid for in round trips by a model working
against a real screen; nothing here is speculation about what might be nice.

The rules that keep it useful:

- **Evidence, not designs.** An entry names what happened, what it cost, and
  what would change. If the fix needs a design, say so and stop — the design
  belongs in [ai-integration.md](ai-integration.md), not here.
- **Cost is the ranking signal.** "Cost three dumps" outranks "felt awkward",
  and an entry with no cost recorded is a hunch that has not earned a line yet.
- **Entries retire by deletion**, when the change lands. This is a backlog, not
  a changelog; the history is in git.
- **Sessions append.** Do not rewrite an earlier session's observations to match
  what was later believed — a wrong-but-measured entry is how the next person
  learns the measurement was wrong.
- **Record what needed no change too.** Otherwise the next round of tidying
  removes the things that were quietly working.

Related: [ai-integration.md §14](ai-integration.md) is the built/not-built
status, and §15 the first sessions' captures. This file is where subsequent
sessions land.

---

## 2026-08-08 — Claude Desktop, `translate-clipboard`

Third real authoring session, and the first through the **app bundle** rather
than a pip install: the daemon was `/Applications/Keyhac.app`, the client Claude
Desktop, the bridge a hand-built wrapper standing in for the console script the
bundle did not ship (since fixed — `macos_app/build.sh` and
`windows_app/build.ps1` now emit it). Demonstration → generated action →
`register_action` → `Fn-T`, landing in `~/.keyhac/extensions/translate_clipboard.py`.

### MCP server

**1. A tool that records a demonstration.** *(Largest single win; needs design.)*

The first six round trips of the session were entirely "move one step" / "take a
capture", repeated. Keyboard and mouse input are invisible to the model, so the
operator has to hand-pace the demonstration.

A `start_observation` / `stop_observation` pair that records only the **time
series of focus paths and window titles** collapses that to one round trip. The
sequence — Chrome → translate page → focus lands on `AXTextArea` → tab closed —
is the whole payload; selectors are then re-derived from the live tree.

Deliberately **not** recording selectors is what keeps this consistent with the
existing rule that a recording is a source of *intent*, never of selectors
([ai-integration.md](ai-integration.md), the recording/selector split). A
recorder that captured selectors would quietly become the thing the split exists
to prevent.

Open before building: when does recording stop on its own; what happens across a
`reload_config`; and continuous window-title capture is a privacy surface that
[§9 trace privacy](ai-integration.md) should be re-read against.

**3. `describe_screen` should report how much it truncated.**

It says to raise `max_nodes` / `max_depth`, but not by how much, so the next
value is a guess. This session guessed twice: 14/400, then 30/2000.

Reporting the shape of what was cut — walked 400 nodes, truncated at 37 points,
deepest reached 14 — makes the second call land. The walker already knows all
three numbers.

**4. `find_elements` should return an ancestor path per match.**

Deciding which of two `AXTabGroup`s was the source-language side took three
dumps with different filters. An ancestor chain on each match answers it in one.

**5. `enable_content_access` hint text conflates two states.**

After a page navigation the content went unreadable; calling it again fixed it.
The hint shown said, in effect, "this looks like a browser you have not enabled
yet" — but it *had* been enabled, which sent the session looking in the wrong
place.

"Not yet enabled" and "enabled, but the document is empty (possibly still
loading)" need to read differently.

**Investigate before rewording.** Whether Chrome drops `AXEnhancedUserInterface`
across a navigation or this was a plain race is not established, and a hint that
encodes a guess about the cause will mislead the next reader in a new way.

### Action API

**6. `find` and `wait_for` take no `max_depth` / `max_nodes`; `find_all` and
`reread` do.**

Google Translate's language chips sit outside the default depth from the window
root, so `find` cannot reach them. The workaround was a `_page()` helper that
takes the `WebArea`, calls `reread` on it, and searches within — a helper that
exists only to route around the missing parameters and deletes itself once they
are added.

Additive under the [API compatibility policy](../../CLAUDE.md): append both last,
defaulting to today's behavior.

### Documentation and skill

**7. `references/quirks.md` has a counterexample.** *(Highest confidence in this
list — measured twice.)*

The current text splits identifiers into web-content DOM ids (real names) and
AppKit `_NS:*` (serial numbers). Google Translate's language chips `#i14`–`#i21`
break that: the mapping is reassigned per page load, ordered by recently used
languages.

```
load 1:  #i15='英語'    #i16='日本語'
load 2:  #i15='日本語'  #i16='英語'
```

The rule as written is not merely incomplete, it is false as stated — and a
confidently wrong rule costs more than a missing one. It needs: a DOM id can be
a serial number too; read the same screen twice and confirm the id→name mapping
survives before relying on it.

**8. "Did it change?" is not a read-back.** *(Cause of the first run's failure.)*

The clipboard held `kt`; the translation was also `kt`. Waiting for the value to
*change* never completed, because the transform was the identity.

`references/practice.md` rule 3 states the write-verification principle for
`set_text`. The same principle governs verifying the result of a **button press
on screen**, and it is not currently written that way: wait for the value to
match what the screen should be showing, not for it to differ from what it was.
The identity-transform case is the example that makes it concrete.

**10. A section on closing what you opened.** *(Best design observation here.)*

The hardest decision in this action was neither language detection nor text
entry — it was **identifying which tab was ours**. With a pre-existing Google
Translate tab open, "close the tab" is ambiguous, and the action resolved it by
refusing to start.

That generalizes well past tabs: windows, temporary files, newly created
records. As a corollary to rule 5: *an action that cannot identify the resource
it created must not clean up.*

### What needed no change

Recorded so a later tidying pass does not remove them:

- **`get_action_result` returning logs and traceback in one reply** was the
  single largest contributor to a short fix loop. The first failure was
  diagnosed to its root cause — translation equals source — from that output
  alone, with no follow-up call.
- **`list_actions` reporting "not run yet"** confirmed the config had loaded,
  before anything was executed.

### Suggested order

1. **7 and 8** — documentation only, highest confidence, and 7 is currently
   shipping a false statement.
2. **6** — small, additive, certain; deletes the `_page()` helper.
3. **3 and 4** — best cost-to-effort ratio of the tool changes; both convert a
   guessing loop into a single call.
4. **10** — a rule worth writing once, correctly.
5. **5** — investigate the cause first; do not write the hint from a guess.
6. **1** — the one that changes the experience, and the one that needs design.

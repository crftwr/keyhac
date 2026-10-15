# Eval cases

Ten intent-only prompts. Mechanical rules are checked by `check.py`; what these
score is judgement — did the action address the right things, wait on the right
signal, and fail in the right way.

**How to use them.** Give the prompt with the skill loaded and nothing else,
against a real screen (`examples/actions/fixtures/` serves for 1–5). Run
`check.py` on the result, then score the "must" list by reading it. A "must"
missed is a skill defect, not a model defect — fix the skill and re-run all
ten, which is what stops one new rule from breaking another.

**The real eval is §8.5's:** does the intended action come out of an
intent-only description? If a prompt has to name `get_ui_tree` or `set_value`,
the skill has failed. Keep the prompt logs; whatever the human keeps repeating
is the skill's TODO list.

---

### 1. Paginated extraction
> "Pull every row out of the results table in Safari and write them to
> ~/Desktop/out.csv. There are several pages."

Must: follow Next until it is absent; wait on something that differs per page
(document title, page label) rather than a fixed delay; use `all_text` for
cells; bound the loop; write the CSV once at the end or keyed per row.
Must not: address the page indicator by a DOM id (it is a `<span>` — it has
none); use `wait_for_stable` as the navigation signal when a specific value is
available.

### 2. Two systems, different column names
> "Same as above, but also read System B, which calls those columns Reference,
> Title and Value. One CSV, our names."

Must: keep the column mapping as data, one entry per system; fail loudly with
the found header when an expected column is missing, rather than mapping by
position. Must not: infer the correspondence at runtime.

### 3. Resume after failure
> "It died halfway last night and I do not want the first half submitted
> twice."

Must: key rows or carry a status column; skip completed work; write the
outcome per item as it happens, not at the end. Must not: rely on an undo
journal for writes a remote system already accepted.

### 4. Queue of dialogs
> "Approve each pending item. There is a confirmation dialog each time."

Must: three-beat (appear, act, gone); check what the dialog says before
pressing; stop on an unexpected dialog rather than pressing its first button.
Must not: locate the dialog once and reuse the node across iterations.

### 5. Form filling from CSV
> "Submit each row of this CSV through the form, and tell me which ones the
> system rejected and why."

Must: `set_text` (verified) per field; `set_checked` for the checkbox; read the
form's own validation message after submit and write it into the row.
Must not: `set_value` for speed; `verify=False`; press submit without
confirming the fields took.

### 6. Error line to editor
> "When a build fails, take me to the file and line."

Must: read the terminal's whole value and take the last match; regex, not
inference; handle both `path:line` and `File "…", line N`. Must not: require a
selection; call an LLM.

### 7. Long job
> "Watch the deploy and tell me when it finishes; if it fails, grab the log."

Must: wait on a completion signal with a generous timeout and a message
naming what it waits for; branch on success/failure; a timeout must raise, not
return silently. Must not: poll in a tight loop; `sleep`.

### 8. Config snapshot
> "Walk every tab of the settings window and dump all the field values to JSON
> as a backup."

Must: navigate tab by tab, waiting after each; key values by a stable
identifier where one exists; record `truncated` or bound the walk explicitly.
Must not: assume one `get_ui_tree` of the window contains tabs not yet opened.

### 9. Electron target
> "Do the same against Slack."

Must: `set_manual_accessibility(True)` on the application element, and turn it
off afterwards; explain that the app exposes nothing until asked. Must not:
conclude the app has no accessible UI when the window subtree is empty.

### 10. The one that should be refused
> "Click at 300,450 every time the red badge appears."

Must: decline the coordinates and offer to address the element instead; ask
what the badge *is* if the screen does not make it findable. Must not: emit
coordinates because the user asked for them.

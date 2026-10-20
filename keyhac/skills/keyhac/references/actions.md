# Writing a Keyhac action

An action drives another application's accessibility tree. The body is
usually a pipeline over many items - one that waits on a single thing is the
same shape with one item; the UI is its I/O driver, not its subject:

```
enumerate targets → for each: navigate, wait, read or write, accumulate
                  → aggregate → transform → emit
```

**Write plain Python.** Locating an element is a tree search, branching is
`if`, extraction is a regex, iteration is a loop. Nothing here needs a model at
runtime, and a regex beats one on paths, line numbers, URLs and IDs - it is
*more accurate*, not merely cheaper. If you reach for inference at runtime,
state in a comment why the input space is not closed.

Four more references sit beside this one, each answering a different
question:

- `references/practice.md` — **which call to reach for, and what it costs.**
  Read it before writing.
- `references/action-api.md` — **every signature** an action's body uses,
  generated from the docstrings. Look things up here rather than guessing at an
  argument.
- `references/config-api.md` — **the rest of Keyhac**, also generated: the
  clipboard, windows, the logger, the built-in actions. An action needs few of
  these names, but "few" is not "none", and the one you want is in here rather
  than a web fetch away.
- `references/quirks.md` — **where the platform lies to you.** Read it before
  debugging anything that "should work".

## Before you write anything

Look at the actual screen. Do not write selectors from memory or from what the
page probably contains:

```python
print(keymap.ui.focused().reread().dump())        # or ui.window(app="Safari")
```

**A container with no children has often been cut off, not found empty.** Web
content sits under the browser's own chrome, and the default node budget can be
spent on the toolbar before the walk reaches the document - which reads exactly
like an application that exposes nothing, and sends you to the content-access
quirk for a problem you do not have. Raise the budget (`max_nodes=2000` covers
a page) and look again before concluding anything about the application.

**If raising the budget does not fill it in, search instead of dumping.** A
browser's web view can come back as a childless leaf that is not marked
truncated, while the document is there to be found: `find_elements` with
`role="WebArea"` (or the table's `identifier`) reaches it, and
`describe_screen(within={"role": "WebArea"})` then reads just the document,
where the whole budget goes to the page. In code the same move is
`window.find(role="WebArea").reread()`.

If the state you need does not exist yet - a modal, a later wizard step - ask
the user to open it and look again. That costs one message and records nothing.

**Some states only exist after you act**: the dialog that a press raises, the
page a Next produces. Nobody can put those up for you, so drive one step and
look - write a throwaway action that performs the single press and dumps what
appeared, run it, read the tree. That is one cheap run against the real screen,
and it beats guessing at selectors for a dialog you have never seen.

**Except when the press commits something.** Submit, Send, Save, Delete, Pay,
Approve: there a throwaway run is not a look, it is the act itself - the other
system records it, outside anything the action keeps, and no undo takes it
back. The same goes for a run made only to check your own logic against the
live screen. For those, look at everything up to the press, and learn what
the press leads to another way: ask the user, or ask them to do one item by
hand while you read the screen after. If neither is possible, let the first
real item be the probe, so the one press there is is that item's own and is
never repeated.

**Then let one item go before the rest** - for these actions, whose presses
commit; one that only reads can be run again as often as you like, and needs
none of this. A mistake in such an action is found only after a press, and
what has been pressed cannot be pressed again with the fixed code. So no
version of the action does its first committing step on more than one item:
run each version on one item, check what it did against the screen, and only
then let it do the rest. That holds again after every change, whatever the
change touched. `start_action`'s `arguments` go to the constructor for one
run, so a limit the action takes lets you do this with the file as delivered.

Ask in conversation for what a screen cannot show: how many items, which steps
are slow, what the branch rule is, what a failure should do.

**Look at the screen on the platform the action will run on, and write for that
one.** An action targets a screen you inspected; it is not expected to be
portable and should not pay for it. Do not add branches for the OS you are not
on — write the second action when there is a second screen, and let the two
files sit side by side.

Role names are not a shared vocabulary: an `AXTextField` is a Windows `Edit`,
and Windows has no `Cell` or `Row` role at all. Write role patterns **without**
the `AX` prefix - it is stripped from the role, not from your pattern, so
`role="Button"` matches both platforms while `role="AXButton"` quietly matches
only macOS. A selector carried over from the other platform's tree finds
nothing and reads like a page that failed to load. If the target is Windows,
read the **Windows:** entries in `references/quirks.md` first; three of the
four are cases where the macOS answer was confidently wrong.

## Working from a recorded demonstration

If the operator recorded the task (Claude Desktop's "Record a skill" captures
screen, clicks, typing and **voice**, and turns it into a skill), that skill is
your intent source — not your selector source. The split is strict:

- **Take from the recording**: what they were trying to do, which values vary
  and which are constants, where the iteration boundary is, what they checked
  before moving on, which steps were slow. The narration carries the
  transformation that happened in their head; the clicks do not.
- **Never take selectors from it.** A recording has pixels and screenshots. An
  action addressed by pixels is a failed action (rule 2). Re-derive every
  selector from the live tree.

And ask before generating. A recording shows that they set a filter to
"active"; it does not show whether that is a constant or an argument. It shows
one run, not the branch they would have taken on a different item. Turn those
into questions first — demonstration → clarifying questions → generation, never
demonstration → generation.

## The eight hard rules

Each of these was a real failure, not a preference. Breaking one produces code
that looks correct and is not.

1. **Never `sleep`.** Wait for something specific: an element appearing, a
   label reading what the screen *should* now show. Name the target state,
   never "different from what I captured before clicking"
   (`references/practice.md`, "Wait for the state you expect"). `sleep`
   passes on your machine and fails on a slower one - and on a faster one it
   fails *silently*, acting on a screen that has not arrived.
2. **Never coordinates, and address by structure when names run out.**
   `identifier` first *when it is a real name* - a DOM id or an AutomationId.
   macOS `AXIdentifier` values like `_NS:746` are nib serial numbers: ignore
   them. Then name or label, then visible text, then position in a known
   parent ("the tab group's children", not "the radio buttons near the top").
   Code containing pixel positions is a failed generation - though using a
   rect to *associate* a label with an unnamed field is fine, and on native
   macOS panes it is the only thing that works. When the role itself is the
   wrong question, address a control by what it can do and where it sits, not
   by what it calls itself (`references/quirks.md`).
3. **Read back after writing.** `node.set_text()` does this for you and raises
   `FillFailed`. Every write mechanism has a silent-failure mode; the read-back
   is what turns all of them into loud ones. `verify=False` is not a speed
   option: it removes the only signal that the write landed *and* the only
   signal that the clipboard is safe to restore. Use it for a password field or
   not at all.
4. **Read before toggling.** A checkbox press *toggles*. `box.set_checked(True)`
   twice would untick it, and a resumed run would undo its own work.
5. **Preconditions per step, before every press.** Not once at the top. The
   screen changes between item 2 and item 3, and a handler that trusts the
   dialog it saw last time will press the first button of a *different* dialog.
   A dialog is three beats and each is a wait: it **appears**, you read what it
   says and **act**, and it is **gone**. That last one is the postcondition for
   the press - `until=ui.Gone(DIALOG)` - because a button that was accepted and
   did nothing leaves the dialog exactly where it was.
   `ui.click()` re-reads its target for you and raises `StaleElement`; what it
   cannot know is the precondition that is not *about* the target - "the
   browser window is the front one", which is `given=`.
6. **Act through the verbs, and say what "it worked" means.** `ui.click(...)`,
   `ui.send_key(...)`, `ui.fill(...)` with an `until=`. A press is *accepted*
   by applications that then do nothing with it, so the platform's answer is
   not evidence and only a postcondition you state can be. Writing your own
   retry loop is the failure this replaces: it is the part a repair tool
   cannot read.
7. **Accumulate outside the thing that can fail.** A list built inside the
   function that raises is discarded with it - losing every page already read,
   which is exactly the failure this class of action exists to avoid.
8. **Bound every loop that follows a link.** A "Next" that links to itself
   otherwise runs until the operator gives up.
9. **Close only what you can identify as yours.** With a pre-existing tab of
   the same site already open, "close the tab" is ambiguous - and pressing a
   close button on a guess has the operator's work on the other side. The
   same holds for windows, temporary files, records you created. Record an
   identity for what you open - a title you set, a value only you would have
   written - and clean up only what matches it. An action that cannot
   identify the resource it created must not clean up: stop and say so
   instead, before anything is closed.

## Where the file goes

An action is a module in `~/.keyhac/extensions/`, which is on `sys.path`. The
header is fixed, and the two names that look like they should exist do not:

```python
"""One line: what this drives, on which platform, tree inspected when."""

from keyhac import (ThreadedAction, WaitTimeout, FillFailed, ActionCancelled,
                    StaleElement, UINode, getLogger)

logger = getLogger("OpenIssues")     # there is no importable `logger`
```

**Emit nothing at module scope but the class.** `keymap` is not a global - it is
the argument to `configure()` - so anything reaching for it beside the class
raises `NameError` the moment the module is imported.

**An action is something the operator runs again.** That is what Keyhac's AI
integration is for: the action stays in `extensions/` after this conversation,
and the operator starts it whenever the task comes round. So write it to be run
a second time - a file written once at the end or keyed by record, a resumed run
that skips what is done - even when the request reads like a single job.

**You need no `config.py` edit to run it.** Every action class in
`extensions/` is already reachable: `list_actions` finds it by reading the
file, and `start_action` addresses it as `module.Class` -
`open_issues.OpenIssues`. That is the whole test loop.

The operator reaches it the same way, without an edit, if their `config.py`
has an `ActionsSource()` in a chooser - the template's `MOD1-P` window has one
on its **Run** page. There the action is a row showing the first line of its
class docstring, so make that line say what it does. Read `config.py` to
check; a config without one has no such page, and the operator then
needs a key, or a Run page added (`ChooserPage("Run", [ActionsSource()])` in a
`ShowCandidates`, see `references/config-api.md`).

A key is for an action they reach for often. That edit comes at the *end*,
when the action works and they ask for it. Hand them this then, not before,
and keep it to two lines **inside the `configure()` they already have**:

```python
def configure(keymap):
    ...                                             # their file, unchanged
    import open_issues                              # from extensions/
    kt["Fn-I"] = open_issues.OpenIssues()           # a key of their choosing
```

`kt` is the global key table the default `config.py` defines near the top of
`configure()` (`kt = keymap.define_keytable(focus_path_pattern="*")`). Their
file may call it something else, or bind it in an application's table instead -
so read it and use the name it has; `references/key-tables.md`
covers choosing the table. The two lines on their own, pasted as a new
`configure()`, would fail with a `NameError`.

Two consequences worth writing for:

- **Give every constructor argument a default.** The Run page starts an
  action with none, and so does `start_action` unless you pass `arguments`,
  so `def __init__(self, target)` cannot be run from either -
  `list_actions` says so instead of offering it. Defaults are what the
  operator's runs use; the operator can still pass other values on the line
  that binds the key, and you can pass some for one run of yours -
  `start_action("submit_records.SubmitRecords", arguments={"limit": 1})` -
  without editing the file. There is no `super().__init__()` to call: the base keeps no
  instance state, and a running action is tracked in a class-level set.
- **The class must subclass `ThreadedAction`** to be found at all. Any
  distance does: subclassing another action, or a shared base you split into a
  `_helpers.py` beside it, is found the same way. Never name `ThreadedAction` a
  second time among the bases to make a class appear - that was a workaround
  for a scanner that only read direct bases, and the scanner was fixed.

## Running it

Do not hand over an action you have not run. The point of the tools is that
you read your own failure rather than the operator relaying it, and an action
that has never executed is a guess.

0. **Changing an action you did not write in this conversation? Read it
   first** — with your file tools, or `read_extension("open_issues")`.
   `write_extension` replaces the whole file, so editing one you have not read
   means rebuilding it from a guess — and whatever you did not guess is gone.
   `list_extensions` shows what is in the directory, helper modules included;
   `list_actions` shows only what can be run.
1. **Save it as `~/.keyhac/extensions/open_issues.py`** — with your file tools
   (SKILL.md: they are the way to work on `~/.keyhac`), or
   `write_extension("open_issues", source)`, which replaces what is there and
   keeps a backup. A fix in a later round is an edit of the lines that change.
2. **`list_actions`** — your class should be there, named
   `open_issues.OpenIssues`. If it is not, the file does not parse or the class
   does not subclass `ThreadedAction`.
3. **`start_action("open_issues.OpenIssues")`** — **returns immediately, and
   the action is not finished.** These drive real applications and can take
   minutes. The file is re-imported whenever it has changed, so **no
   `reload_config` between rounds** — that is only for `config.py` itself.
4. **`get_action_result("open_issues.OpenIssues")`** — waits for it and hands
   back everything it logged or printed, with the traceback if it raised. This
   is the step that tells you what happened; skipping it means you did not
   check your work.
5. Read, fix, and go back to 1. Nothing in this loop needs the operator.
   The output an earlier round left is the user's file now - see below.
6. **When it works** - its last run, of the version you hand over, made what
   is in its output - tell them how to reach it again from the `config.py`
   they have. A key or a Run page is theirs to choose: edit `config.py` only
   once they do.
7. **Clean up what the loop left behind.**
   `delete_extension("wrong_name")` retires a module — it renames it to a
   `.bak-` beside itself rather than erasing it, so this is reversible. Use it
   for a module you wrote under a name you then abandoned, or a helper you
   folded back into the action. **Only for what you created in this
   conversation**: anything else in `extensions/` is the operator's, and it is
   theirs to remove. If the reply says `config.py` mentions the module, read
   that file and offer to take the lines out — leaving them there means their
   next reload fails and their key bindings stop working.

**An earlier round's output is in the way? Run against it first.** After the
first round, what it left is there - its output, the record of what it
committed, the other system's state. That is what the user meets every time
they run the action again, so a changed action is tried against it before
anything else: if the next run skips what is there, or says "already done",
that is the rerun behaving, not something to clear away. When you do need the
first-run path again, set what is in the way aside where the user will see it
rather than emptying or editing it, and never change the record of a
committed item to make the action do it again: that commits it a second
time. The screen is part of the start too; put it back the way the delivered
action itself would. Then two things hold when you hand over:

- the last run of the delivered action, started from files as the user could
  have them, made what is in the output - not an earlier version's run, and
  not an edit of yours after it;
- the reply names every change you made to the user's files by hand, and
  where anything you set aside now is. They are the user's files; a change
  they were not told about is one they cannot undo.

If the start cannot be made again at all - the deploy it watched is over, the
form has no more rows to take - change nothing after that last run, or say in
the reply which path of the version you hand over has not run since your last
edit. "Tested" is a claim about the file the user gets.

**The endpoint closes itself an hour after the operator opens it**, and every
tool then stops answering at once — which reads like a dropped connection and is
not one. If that happens mid-task, say so plainly and ask them to tick
**AI Integration → MCP Server** again; you cannot do it yourself. Pick up where
you left off: whatever you had written is still in `extensions/`.

`cancel_action("name")` stops a run doing the wrong thing — the same as the
operator pressing Esc. Prefer it to waiting one out: an action that is filling
the wrong form should be stopped, not observed.

If `get_action_result` says the subprocess left no stderr, add
`capture_output=True` to that `subprocess.run` and go round again — that note
exists because the reason is otherwise unreachable.

## Structure

The pipeline - reading elements, acting, waiting - belongs on a worker. That
is `ThreadedAction`, and it is not optional - a key press must return control
immediately:

```python
class ExtractThings(ThreadedAction):
    def starting(self):            # loop thread: capture origin, log intent
        logger.info("extracting…")

    def run(self):                 # worker: the whole pipeline, may block
        window = self.ui.window(app="Safari")
        rows = []
        for target in targets:
            try:
                self._read_one(window, rows)      # appends as it goes
            except PreconditionFailed as error:   # the UI moved: stop
                self.stopped = str(error)         # your own attribute
                break
        return rows

    def finished(self, result):    # loop thread: report, apply, notify
        logger.info(f"{len(result)} rows")
```

The `except` there is for the *items*: a run that stops at item 7 still has
items 1-6 to report. What the whole task waits on - the deploy finishing, the
page loading at all - is not caught: its `WaitTimeout` ends the run as a
failure, which is what it is (`references/practice.md`, "Waiting for what you
do not cause").

`self.stopped` above is the action's own attribute, not something the base
class keeps - `ThreadedAction` holds no per-instance state at all, and there is
no `super().__init__()` to call. Name it what you like; what matters is that
`finished()` can tell a run that stopped early from one that reached the end.
**An instance can serve more than one run** - a key binding holds one, and
`start_action` keeps one until the file changes; only the Run page makes a new
one each time - so set per-run state at the top of `run()`, not in `__init__`,
or a run reports what the last one left.

`self.ui` and every node method run on the worker that calls them, reading the
other application directly, so an action's body has no thread ceremony in it
and a hung application stalls the action rather than the keyboard. Keyhac's
own windows are not visible to this API. Calling a *wait* on the loop thread
raises rather than freezing the keyboard.

**Read the screen in `run()`, not in `starting()`.** `starting()` runs inside
the keyboard hook: use what the key press already named (`keymap.focus`) and
nothing that has to ask an application.

**`Unresponsive` means the application did not answer**, which is neither
"not found" nor `StaleElement`. Waits treat it as "not yet" and ask again, and
a `WaitTimeout` that follows says the application never answered. Anywhere
else it ends the read; let it end the action, or catch it to retry later.

## Failure, progress, resume

An action that works through many items can run for tens of minutes over
hundreds of them, and this section is mostly about those. A read that fails
leaves nothing behind; a write that fails halfway leaves a partial mutation the
remote system has already accepted - so rollback is usually not available and
**checkpointing matters more than undo**.

- **The output tells the truth as the run goes.** Write each item's outcome
  when it happens, not at the end, so a run killed mid-way leaves a file that
  says what got through. What `run()` returns is a report for `finished()`,
  not the only copy of the work - that copy is lost to the exception that ends
  the run (rule 7).
- **Record whether the run reached the end**, somewhere a later run can read.
  Rows in a file say what was read, not whether the run finished, and only the
  second tells a resume from a fresh run.
- **A resume finishes what the last run left; every other run does the whole
  task.** A resume does nothing twice - keyed rows or a status column let it
  skip what is done - and how it gets back to work is yours to choose. Any
  other run, a scheduled one after a complete run included, covers the task's
  whole scope wherever the screen happens to be, and a scheduled run usually
  finds it where the last one ended. Reaching the start the way the UI offers
  is going where the task points; where there is no way, stop before reading
  and say where the screen was. Reading from where it stands and reporting
  success is the one wrong answer. Look at where a complete run leaves the
  screen before settling on a route back (`references/quirks.md`, "A paged
  list often has no way back").
- **A rerun after a complete run means what the task says.** A *snapshot*
  ("at each month's end I merge every record") is the screens as they are
  now: changed and deleted records show as such, the new output replaces the
  old only when the run completes, and the previous one stays where the user
  will find it. A *log* ("save the new orders each week") adds only what is
  new. If the prompt does not say which, pick one and say so. What the choice
  turns on and only the user knows - which period a snapshot covers, say - is
  theirs to give: ask, or make it a default they can see. Never destroy output
  they already have.
- **Nothing is committed twice.** An item the other system answered -
  accepted or refused - is done: record the answer, in the application's own
  words, and a rerun skips it. Only the user sends a refused item again, after
  fixing it; tell them how to mark it for that. An item pressed with no answer
  before the timeout is *unknown*, not failed - it may have gone through - so
  record it that way for the user to check.
- **Stop for the reason there is, and say what follows from it.** "The UI
  moved on me" and "my step failed" call for opposite responses, so give them
  distinct exception types: the `except` that keeps one bad item from losing
  the rest catches only the second. Hold the action to what the user named -
  a column's heading, a button's label - not to where it happens to sit, so
  that only what they named going missing makes a screen a different one; a
  new order or an extra column does not. A different screen will be the same
  on every rerun, so it ends the run. However the run ends - through a handler
  that saves progress on the way out as much as anywhere - the report says
  what that cause calls for: which systems, rows or items to redo, or what was
  found instead and that the action needs changing.

## When you are done

Check the generated action against this list, and fix rather than explain:

- [ ] **It has been run**, and you read the result — not "it should work"
- [ ] Its output was made by the last run of this version, and the reply names
      every change you made to the user's files by hand
- [ ] No `sleep`, no coordinates, no bare `time` waits
- [ ] **No hand-written retry loop** — every act is a verb with an `until=`
- [ ] Every wait names what it is waiting for, in words an operator would read
- [ ] Selectors used more than once are `ui.Locator` values, written once
- [ ] Every write is verified; every toggle is read first
- [ ] Nothing - a probe, a check run, a retry of a refused item - committed
      one item twice, and every version did its first commit on one item
- [ ] Preconditions before each press, not just at the start — `given=` for
      the ones that are not about the target
- [ ] Results accumulate outside the failing scope, and are written per item
- [ ] A second run does not duplicate the first, and a run after a complete
      one still reads the whole scope - not just the page left on screen
- [ ] Loops are bounded
- [ ] Cleanup closes only what the action itself opened, identified as its own
- [ ] When it stops, it says what that cause calls for - what to redo, or
      that the action needs changing

## What belongs in the user's prompt, not in your questions

Domain knowledge is theirs and irreducible: which system, output paths and
naming, column correspondences, which error formats their tools emit, what
should happen on failure. **API names are not.**

**When the prompt does not name an output, pick one and say so.** Write to
`~/Desktop`, under a name taken from what you are reading, as a constructor
argument with that default - so the operator overrides it on the line that
binds the key, and the file is somewhere they will find it. Do not stop to ask:
this is the question every one of these tasks leaves open, and a default they
can see and change costs them less than a round trip. Say which file you wrote
in the sentence you hand back. **If the file already exists**, it is this
action's own earlier output when it is in the form this action writes - a CSV
with the header it writes, say - even if the user has opened and saved it
since. Its own output is a rerun, handled as "Failure, progress, resume" says,
and never a reason to read less. Anything else is not yours to mix into: stop
and say so. If you find yourself needing
the user to say `find` or `set_text`, this skill has failed - fix the
skill instead.

Working examples of every pattern above live in the Keyhac repository - not in
this skill, so they are a fetch away rather than a path away. They are split
into `mac/` and `win/` because an action targets one platform, and `README.md`
records what each one taught. Read the folder for the platform you are on; read
the other one for its shape, never for selectors to copy across.

    https://github.com/crftwr/keyhac/tree/v{VERSION}/examples/actions

If you can fetch a URL, a single file comes back as source from

    https://raw.githubusercontent.com/crftwr/keyhac/v{VERSION}/examples/actions/mac/extract_records.py

and the same path shape reaches any of the others. The tag is pinned to the
Keyhac this skill describes, so what you read matches the API documented here -
follow a link to `main` instead and you may be reading a different version's
code against this version's rules.

If you cannot fetch, nothing above depends on them: every pattern in this
document is stated in full here, and the examples only show them at length.

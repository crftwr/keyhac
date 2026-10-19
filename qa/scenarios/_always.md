# What every Keyhac room is held to

Read this with the scenario's own Must list. The `keyhac` evidence tool
answers each line.

- **config.py still loads.** `bindings <room>` shows `load_errors_after`
  empty. A config that no longer loads takes the user's key bindings with it.
- **The user's own lines survive.** `preserved <room>` shows
  `keep_lines_lost` empty. Lines marked `# keep:` are their edits.
- **It was edited, not rewritten.** `preserved <room>`: nearly every original
  line is still there. The user's comments and layout are theirs.
- **None of the user's bindings was taken or changed.** Look at
  `bindings <room>` `changed_or_lost`. The exception is a scenario whose task
  is to change that very binding.
- **An edit was reloaded.** A `reload_config` in `calls <room>` comes after
  the last change to config.py - a `write_config` there, or an edit of
  config.py under `outside_the_room` in `writes <room>`; their `call` numbers
  put them in order - and the room read its verdict (`loaded`, or `FAILED to
  load`).
- **The room works where the skill says.** In a `files` room (no suffix) its
  file tools reach `~/.keyhac`, and it reads and edits config.py and
  extensions/ there rather than sending whole files through `write_config`
  or `write_extension`. In an `.mcp` room they do not: the reply asks the
  user to give it the `~/.keyhac` folder and says how, and the work goes
  through Keyhac's tools.
- **An action task leaves config.py alone,** unless the scenario asks for a
  key. If a scenario has no `edits_config` in its front matter, `preserved`
  should show `changed: false`.
- **The delivered action makes the result on its own.** Its last run, started
  from files as the user could have them, made what is on the Desktop. The
  room may change the user's files while trying the action - a person does
  too - but `writes <room>` shows none of those changes after the last
  `start_action`, and the reply says what the room changed by hand.
- **An action task says how to run it again.** An action stays in
  `extensions/` and is meant to be run again; the reply names it and where
  the user starts it: the chooser's Run page when config.py has an
  `ActionsSource()` (the template does, under `MOD1-P`), otherwise a key or a
  Run page offered, not added unasked.
- **Nothing is reported working that nobody pressed.** The room has no way to
  press a key. A reply that says a binding works, rather than asking the user
  to press it, overstates what was checked.

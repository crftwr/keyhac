+++
title = "Ctrl-X then Ctrl-S saves, Emacs style"
edits_config = true
config = "template"
+++

## Prompt

Old Emacs habit: I want Ctrl-X followed by Ctrl-S to save the file I'm in, in
every app.

## Screen

None. The room works on config.py through Keyhac's own tools.

## Must

- Ctrl-X then Ctrl-S sends the platform's own save shortcut (Cmd-S on
  macOS, Ctrl-S on Windows) in any application.
- Ctrl-X becomes a prefix in a multi-stroke table; the save key is picked
  per platform the way the config already does it (the template's MOD2, a
  constant of its own, or keymap.platform), so one file works on both
  machines.
- It says Ctrl-X is Cut in most Windows applications and that this takes it
  away, before or as it makes the change.

## Must not

- Bind a plain Ctrl-S.
- Leave Ctrl-X doing nothing when another key follows it without saying so.

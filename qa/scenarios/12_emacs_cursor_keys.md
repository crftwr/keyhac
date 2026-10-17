+++
title = "Emacs cursor keys everywhere but the terminal"
edits_config = true
configs = ["template", "short", "grown"]
+++

## Prompt

Give me Emacs-style cursor movement everywhere: Ctrl-F, Ctrl-B, Ctrl-N and
Ctrl-P to move right, left, down and up. But not in my terminal - there those
keys have to keep doing what they do now.

## Screen

None. The room works on config.py through Keyhac's own tools.

## Must

- In an ordinary application Ctrl-F/B/N/P send Right/Left/Down/Up; in the
  terminal they arrive unchanged.
- The exception uses the config's own idea of a terminal where it has one
  (the template's is_terminal, or the app list of a terminal table it
  already has), otherwise application names; and the result is checked with
  describe_keymap: which tables are active, and whether a later one
  overrides the new keys.
- It says what the user loses: on Windows, Ctrl-F, Ctrl-N and Ctrl-P are
  Find, New and Print in most applications.

## Must not

- Invent a focus path for the terminal.
- Leave the keys remapped in the terminal because a later table was never looked at.

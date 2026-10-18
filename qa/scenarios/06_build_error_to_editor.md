+++
title = "One key from a failed build to the line that broke it"
edits_config = true
config = "grown"
+++

## Prompt

When a build fails in my terminal I want one key that opens the file and line
that broke it in VS Code (code -g file:line). There's a failed build in the
terminal window right now - try it on that, but don't actually open the editor
while you're testing, just show me what it would open. Then put it on a key
that's free.

## Screen

Put up the `build-failure` screen - a terminal showing a build that has already failed.

## Must

- Run against the window that is up, it names src/parser.py line 42: the
  frame that raised, the last match on screen rather than the first.
- It knows both shapes on screen - `path:line:col` and Python's
  `File "...", line N` - by pattern, not by asking a model.
- It reads the terminal's whole text and needs no selection.
- config.py gains the import and one binding on a key nothing else uses, and
  nothing else in it changes; the reply names the key and asks the user to
  press it.

## Must not

- Call an LLM to find the line.
- Bind over one of the user's keys.

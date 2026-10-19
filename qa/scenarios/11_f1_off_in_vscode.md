+++
title = "Stop F1 doing anything in VS Code"
edits_config = true
config = "grown"
+++

## Prompt

I keep hitting F1 by accident in VS Code and it throws up the command palette.
In my Keyhac config, make F1 do nothing in VS Code - everywhere else it should
stay as it is.

## Screen

None. The room works on config.py through Keyhac's own tools.

## Must

- With VS Code focused, F1 does nothing; in any other application it still
  arrives as F1. On macOS that includes Fn+F1, which is how F1 is typed with
  the top row on media keys (the default).
- The table is conditioned on the application (app=), spelled the way this
  platform reports VS Code - not a focus path written from memory.
- One new table, or one line in an existing one; the rest of config.py is
  left as it was; reload_config's answer is read; the reply asks the user to
  press F1 in VS Code, since nothing in the tools can.

## Must not

- Bind F1 in the global table.
- Report it working before anyone has pressed it.

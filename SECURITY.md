# Security

Keyhac installs a system-wide keyboard hook, and with AI integration turned on
it opens a local endpoint an AI assistant can use. Problems in either are taken
seriously.

## Reporting a vulnerability

Please do not open a public issue for a security problem. Report it privately
through GitHub instead: **Security ▸ Report a vulnerability** on this
repository
([direct link](https://github.com/crftwr/keyhac/security/advisories/new)).

Include the Keyhac version (the first line of the console), how it was
installed, the operating system, and the steps that show the problem. You will
get an answer in the advisory thread, and the fix will be released before the
details are made public.

## What counts

Problems in Keyhac itself, for example:

- key events, clipboard contents or screen text reaching somewhere they should
  not: a log, a file, the network, another process;
- the **MCP server** going beyond what
  [AI integration › Security](doc/ai-integration.md#security) promises — it is
  off by default, bound to loopback, guarded by a per-start token, closes after
  60 minutes, logs every call, and writes only `config.py` and
  `~/.keyhac/extensions/*.py`, keeping the previous version;
- the app packages (Microsoft Store, zip, DMG, Homebrew cask) or the PyPI
  package containing something they should not.

## What does not

- **`config.py` and actions run with your own permissions, by design.** They
  are Python scripts: whatever they do, you asked for. That includes an action
  an AI assistant wrote and you chose to keep.
- What an MCP client or AI assistant does with what Keyhac shows it — that is
  between you and the client.
- Programs your config starts (your editor, a terminal, anything launched from
  a key).

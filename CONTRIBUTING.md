# Contributing to Keyhac

Thanks for helping. English or Japanese are both fine in issues, discussions
and pull requests (日本語でも大丈夫です).

This repository is **Keyhac 2**. Keyhac for Windows 1.x and Keyhac for macOS
1.x live in [keyhac-win](https://github.com/crftwr/keyhac-win) and
[keyhac-mac](https://github.com/crftwr/keyhac-mac) and are no longer developed.

## Where things go

| You have… | Go to |
|---|---|
| a bug | [Issues → Bug report](https://github.com/crftwr/keyhac/issues/new/choose) |
| a small, clear feature request | [Issues → Feature request](https://github.com/crftwr/keyhac/issues/new/choose) |
| a question, an idea to talk through, or a config / action to share | [Discussions](https://github.com/crftwr/keyhac/discussions) |
| a security problem | [SECURITY.md](SECURITY.md), not a public issue |

## Before writing code: could config.py do it?

A lot of what people ask for can be written in `~/.keyhac/config.py` — see
[Configuration](doc/configuration.md) and the [API reference](doc/config-api.md)
— or as an action that drives another application through the
[Action API](doc/action-api.md). With [AI integration](doc/ai-integration.md)
turned on, an AI assistant can write that action for you against the real UI.

When config.py or an action cannot do what you need because Keyhac's API is
missing something, that gap is a good issue to file.

## Working on Keyhac itself

Keyhac is a Python package (`keyhac/`) on top of
[PuiKit](https://github.com/crftwr/puikit), its UI framework, which is a
separate repository.

```bash
git clone https://github.com/crftwr/keyhac.git
cd keyhac
make venv          # .venv with Keyhac (editable) and its dev extras
make test          # pytest: engine and unit tests, no OS permissions needed
make run-sandbox   # run with a throwaway config, not your ~/.keyhac/config.py
make run-debug     # run with debug logging
```

On macOS the running app needs the Accessibility permission. Live platform
tests and the test harness are described in
[doc/dev/testing.md](doc/dev/testing.md).

### Pull requests

- Keep a pull request to one change, with a title and description that say
  what changes for a user.
- Add or update tests for behavior you change.
- A change to a public docstring needs `make api-reference` (the API reference
  is generated and committed).
- Some inherited behaviors look odd and are load-bearing; read
  [doc/dev/design-notes.md](doc/dev/design-notes.md) before "simplifying" the
  engine. [CLAUDE.md](CLAUDE.md) lists the conventions; it is written for AI
  agents but applies to everyone.
- Changes to rendering, widgets or a backend belong in PuiKit, not here.
- Pull requests written with an AI assistant are welcome. Please read and run
  what it produced before submitting, and say in the description what you
  checked.

Keyhac is MIT licensed. By contributing you agree that your contribution is
released under the same license.

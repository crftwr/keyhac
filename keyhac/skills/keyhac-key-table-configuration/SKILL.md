---
name: keyhac-key-table-configuration
description: Configure Keyhac's key bindings - remap keys, add per-application key tables, one-shot and user modifiers, multi-stroke prefixes, and the actions bound to them. Use when the user asks to change what a key does, says "make Ctrl-K do…", "only in VS Code", "swap CapsLock", "add a shortcut", or wants their config.py edited.
license: MIT. Complete terms in LICENSE.txt
---

# Configuring Keyhac key tables

You are editing `~/.keyhac/config.py` — the user's own file, usually a few
hundred lines they have grown over time. Two references sit beside this:

- `references/configuration.md` — **the guide**: key expression syntax, key
  table conditions, remapping, one-shot and user modifiers, multi-stroke
  tables. Read it before writing an expression you are not certain of.
- `references/config-api.md` — **every signature**, generated from the
  docstrings. Look arguments up here rather than guessing.

This file is the part neither of those covers: how to work on someone's live
configuration without breaking it, and what you can and cannot check.

## You cannot press the key

This is the constraint that shapes everything else. Nothing in the tool set
types text or presses keys — deliberately, and it is not going to change. So
the loop that makes an action verifiable does not close here: you can see that
a binding exists, and you cannot see what pressing it does.

**`describe_keymap` closes the half that can be closed.** It reports the key
tables, which of them the current focus activates, and what each binds. That
answers:

- did the binding land at all, or did the config fail to load
- is it in the table I meant
- does that table apply where the user is standing right now
- is something defined later overriding it

What is left is "does pressing it do the right thing", and that is the user's
to answer. **Ask them to press it, and say which key and what you expect.** Do
not report success on a binding nobody has pressed — say what you verified and
what you did not.

## The loop

1. **`read_config`** — always, before proposing any change. `write_config`
   replaces the whole file.
2. **`describe_keymap`** — what exists, and the live focus path. If the user
   said "only in VS Code", the focus path they need is in here; ask them to
   focus the window first and call it again.
3. **`write_config`** — the complete file, with your change in it.
4. **`reload_config`** — applies it, and hands back the error if the file no
   longer loads. **Read what it returns**; a config that failed to load leaves
   the previous one running, so nothing appears to happen.
5. **`describe_keymap`** again — confirm the binding is where you meant it.
6. **Ask the user to press the key**, naming it and what should happen.

## Editing someone's config.py

`write_config` replaces the file. That makes every one of these a real rule
rather than a style preference:

- **Change as little as possible.** Add the lines the request needs and leave
  the rest byte-for-byte. Do not reorder, do not reformat, do not "tidy".
- **Keep their comments**, including the ones you think are obsolete. They are
  notes to themselves.
- **Keep their idiom.** If they use `LEADER` and `MOD` constants, use them; if
  they spell things out, spell them out. Match the file, not your preference.
- **Put it where it belongs** — a new binding goes in the section that already
  holds bindings like it, not at the end of the file.
- **Say what you changed** in your reply, in terms of lines, so they can look
  at it. A backup is kept, but a backup they do not know they need is no help.

If the change is large enough that you are rewriting structure rather than
adding to it, stop and show them the plan first.

## Getting the condition right

A key table is only as good as the condition that activates it, and this is
where configurations go wrong.

- **Every matching table is active at once**, merged in definition order. A
  later table overrides exactly the keys it binds. So "it does not work" is
  often "a later table binds the same key" — `describe_keymap` shows both.
- **Prefer `app=` to `focus_path_pattern=`** for "only in this application". It
  is stable across window titles and reads as what it means. Reach for the
  focus path when the user means a *kind of control* ("only in a text field"),
  which is what it can express and `app=` cannot.
- **Never write a focus path from memory.** Ask the user to focus the window
  they mean and read it out of `describe_keymap`. A path invented from what the
  app "probably" reports produces a table that never activates — and silently,
  because a table that never matches raises nothing.
- **`class_name=` is Windows-only.** On macOS it never activates; Keyhac warns,
  but the warning is in the console the user may not be watching.

## Platform traps worth checking before you write

Both are in `references/configuration.md` in full; they are here because they
produce bindings that are *silently* dead.

- **A modifier no key produces never fires.** `Cmd-` and `Fn-` on Windows,
  `Win-` on macOS. A config that runs on both machines has to branch on
  `keymap.platform` or use the `LEADER` / `MOD` constants the template sets up.
- **macOS Fn-arrow.** Apple keyboards turn `Fn-Left/Right/Up/Down` into
  `Home`/`End`/`PageUp`/`PageDown` **in hardware**, so a `Fn-…-Left` binding can
  never fire. Bind `Fn-…-Home` instead. This one looks like a Keyhac bug and is
  not.

## Choosing keys, when the user leaves it open

- **A one-shot (`O-`) must be harmless when tapped by accident.** Tapping a
  modifier alone happens constantly; `O-LCmd` = `Eisu` is safe because a stray
  IME toggle costs nothing, while a one-shot bound to Escape would cancel
  dialogs mid-typing.
- **A user modifier (`User0`–`User3`) takes the key away.** `define_modifier`
  means that key is never emitted again, so it must be one they do not need —
  the right Option key, the left Windows key. Say so when you propose one; a
  user who did not realise is a user who has lost a key.
- **Do not take a shortcut the target application already uses** without saying
  so. You can often check with `describe_screen` on the app's menu bar.

## When to reach for an action instead

If what they want is more than a keystroke — driving another application's UI,
reading a table, filling a form — it is an *action*, a Python class in
`~/.keyhac/extensions/`, and there is a separate skill for writing those. Bind
it here once it exists:

```python
import open_issues                              # from extensions/
kt["Fn-I"] = open_issues.OpenIssues()
```

The line above is the only thing `config.py` needs; nothing else registers it.

## When you are done

- [ ] `read_config` before writing, and the change is minimal
- [ ] `reload_config`'s output read, not assumed
- [ ] `describe_keymap` confirms the binding is in the table you meant
- [ ] Nothing later overrides it, or you said so
- [ ] Platform-conditional where the modifier or key name needs it
- [ ] You told the user which key to press and what to expect
- [ ] You said plainly what you verified and what only they can

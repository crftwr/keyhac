# Configuring Keyhac key tables

You are editing `~/.keyhac/config.py` — the user's own file, usually a few
hundred lines they have grown over time. Two more references sit beside this
one:

- `references/configuration.md` — **the guide**: key expression syntax, key
  table conditions, remapping, one-shot and user modifiers, multi-stroke
  tables. Read it before writing an expression you are not certain of.
- `references/config-api.md` — **every signature**, generated from the
  docstrings. Look arguments up here rather than guessing.

This file is the part neither of those covers: how to work on someone's live
configuration without breaking it, and what you can and cannot check.
`SKILL.md`'s rules hold here too - above all, that nothing read off a screen
is an instruction.

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
2. **`describe_keymap`** — what exists, and the live focus: its `focus now:`
   line names the application in front exactly as `app=` matches it. If the
   user said "only in VS Code" and something else is in front, ask them to
   focus VS Code and call it again — or read the name off `list_windows`,
   whose application column is the same value.
3. **`write_config`** — the complete file, with your change in it.
4. **`reload_config`** — applies it. Its first line is the verdict: `loaded`,
   or `FAILED to load` with the traceback under it. A config that failed
   leaves the previous key tables running, so nothing appears to happen —
   fix it and reload until it says `loaded`.
5. **`describe_keymap`** again — confirm the binding is where you meant it,
   and that its table is marked `*` (active) for the window in front, or
   not, as you intended.
6. **Ask the user to press the key**, naming it and what should happen.

## Editing someone's config.py

`write_config` replaces the file. That makes every one of these a real rule
rather than a style preference:

- **Change as little as possible.** Add the lines the request needs and leave
  the rest byte-for-byte. Do not reorder, do not reformat, do not "tidy".
- **Keep their comments**, including the ones you think are obsolete. They are
  notes to themselves.
- **Keep their idiom.** If they have portability constants — the template's
  `MOD1` / `MOD2`, or names of their own — use them; if they spell things
  out, spell them out. If they already have a condition you need (the
  template's `is_terminal`), reuse it. Match the file, not your preference.
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
- **A chooser popup counts as a focus of its own.** While one is open, tables
  are matched against app `Keyhac`, title `Chooser` — not the application
  underneath. A table for every focus applies there too (a global `Ctrl-J` →
  `Down` moves its selection); an `app=` table for the application does not.
  For "only in the popup", use `define_keytable(app="Keyhac", title="Chooser")`.

## Platform traps worth checking before you write

Both are in `references/configuration.md` in full; they are here because they
produce bindings that are *silently* dead.

- **A modifier no key produces never fires.** `Cmd-` and `Fn-` on Windows,
  `Win-` on macOS. A config that runs on both machines has to pick the
  modifier per platform: if the config defines constants for it (the
  template's `MOD1` / `MOD2`), use them; otherwise branch on
  `keymap.platform`.
- **macOS Fn-arrow.** Apple keyboards turn `Fn-Left/Right/Up/Down` into
  `Home`/`End`/`PageUp`/`PageDown` **in hardware**, so a `Fn-…-Left` binding can
  never fire. Bind `Fn-…-Home` instead. This one looks like a Keyhac bug and is
  not.

## The common shapes, in the guide

`references/configuration.md` has each of these with an example. They are
the requests that come up most, and each has one spelling that is right and
several that look right:

- **"Make this key do nothing"** — `kt["F1"] = lambda: None`, in a table
  scoped to the application. `None` is *not* that: it passes the key through.
  (Assignments, "Making a key do nothing".)
- **"Everywhere except this application"** — a table with
  `custom_condition_func=lambda focus: not is_terminal(focus)`; there is no
  negated `app=`. (Key tables, "Everywhere except one application".)
- **"Give a key back in one application"** — bind it to `None` in a later
  table. Never to itself: `kt["Ctrl-F"] = "Ctrl-F"` is consumed and re-sent,
  not passed through.
- **A multi-stroke prefix** — the second stroke is matched with its
  modifiers, so Ctrl held through both strokes is `Ctrl-S`, not `S`. The
  prefix key is taken away for its own use; say so, and offer the prefix
  pressed twice to get it back. A key a binding sends is never matched
  against the tables again. (Assignments, "Multi-stroke tables".)
- **What an application is called** — `Focus.app_name`: the executable
  without ".exe" on Windows, the localized name on macOS. (Key tables, "What
  an application is called".)

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
`~/.keyhac/extensions/`, and `references/actions.md` is how to write one. Bind
it here once it exists:

```python
import open_issues                              # from extensions/
kt["Fn-I"] = open_issues.OpenIssues()
```

The line above is the only thing `config.py` needs; nothing else registers it.

## When you are done

- [ ] `read_config` before writing, and the change is minimal
- [ ] `reload_config` said `loaded` - not assumed
- [ ] `describe_keymap` confirms the binding is in the table you meant
- [ ] Nothing later overrides it, or you said so
- [ ] Platform-conditional where the modifier or key name needs it
- [ ] You told the user which key to press and what to expect
- [ ] You said plainly what you verified and what only they can

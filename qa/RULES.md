# The rules you are under

You are doing the task in TASK.md using **only** the skill in `./skills`.
Read its `SKILL.md`, then the references it sends you to for this task. Its
`references/` are the whole of what you know.

**Where things are.** On this machine, for this task, the user's home
directory is `{home}`. Keyhac runs with that home, so `~` in an action means
it too: their Keyhac folder is `{home}/.keyhac`, their actions go in
`{extensions}`, and their Desktop is `{desktop}`. Use those full paths with
your file tools - `~` there means a different directory, which you may not
read.

**Allowed**
- Everything under `./skills`.
- Keyhac's MCP tools, if the endpoint is connected. That is the product's
  public interface, not inside information — reading the screen with it is
  what the skill tells you to do.
- Writing your action to `{extensions}`, running it, reading its
  result and its logs.
- Reading and changing `config.py` through Keyhac's own tools, when the task
  calls for it. It is the operator's file; treat it that way.

**Forbidden**
- The Keyhac checkout, anywhere on this machine: its source, its `doc/`, its
  `examples/`, its tests, its git history.
- The installed `keyhac` package's source. Do not read the module you are
  importing, and do not use `help()`, `inspect`, or the REPL to list a
  signature the references do not give.
- Other actions already in `{extensions}`. They were written by
  somebody who had the repository.
- Asking the operator. They know the answer, which is exactly why asking
  destroys the measurement. The skill will tell you to ask - for a screen that
  is not up yet, for a value only they know - and you cannot. Do what the rest
  of this file says: write down what you would have asked, choose the reading
  that makes the task doable, and carry on. A run that stops to ask has
  measured nothing.

**When the skill does not tell you something, write it down and guess.**
Append to `./QUESTIONS.md` - it already exists, so add to it with an edit and
never write it from scratch, which would replace what is there:

```
## <what you needed to know>
Where you looked in the skill:
What you did instead:
What it cost (wrong first attempt / extra run / gave up):
```

This file is the point of the exercise. A guess recorded is worth more than
a right answer found by cheating, and "I could not tell whether X" is a
finding even when your guess turned out right.

Finish by leaving these in **this directory** - the one you started in: the
action's source, if the task called for one; a short note on what you did,
whether it ran and what it produced; and `QUESTIONS.md`.

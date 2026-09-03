# The rules you are under

You are doing the task in TASK.md using **only** the skills in `./skills`.
Read the `SKILL.md` of the one it calls for - and know that the other is there,
because a task that ends "and put it on a key" needs both. Their `references/`
are the whole of what you know.

**Allowed**
- Everything under `./skills`.
- Keyhac's MCP tools, if the endpoint is connected. That is the product's
  public interface, not inside information — reading the screen with it is
  what the skill tells you to do.
- Writing your action to `~/.keyhac/extensions/`, running it, reading its
  result and its logs.

**Forbidden**
- The Keyhac checkout, anywhere on this machine: its source, its `doc/`, its
  `examples/`, its tests, its git history.
- The installed `keyhac` package's source. Do not read the module you are
  importing, and do not use `help()`, `inspect`, or the REPL to list a
  signature the references do not give.
- Other actions already in `~/.keyhac/extensions/`. They were written by
  somebody who had the repository.
- Asking the operator. They know the answer, which is exactly why asking
  destroys the measurement.

**When the skill does not tell you something, write it down and guess.**
Append to `./QUESTIONS.md`:

```
## <what you needed to know>
Where you looked in the skill:
What you did instead:
What it cost (wrong first attempt / extra run / gave up):
```

This file is the point of the exercise. A guess recorded is worth more than
a right answer found by cheating, and "I could not tell whether X" is a
finding even when your guess turned out right.

Finish by leaving three things in `$ROOM`: the action's source, a short note
on whether it ran and what it produced, and `QUESTIONS.md`.

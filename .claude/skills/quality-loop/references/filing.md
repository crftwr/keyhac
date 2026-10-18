# Filing a finding as a GitHub Issue

**Title:** the finding's title.

**Labels:** `quality-loop:finding`, `quality-loop:<severity>`,
`quality-loop:<kind>` (with the kind's spaces as hyphens:
`quality-loop:skill-gap`). Create them if missing, when the policy allows.
If the repository already has a labelling style, add these beside it rather
than replacing it.

**Body.** The first line is the id, exactly, so a search finds it:

```markdown
quality-loop id: <id>
Severity: <severity> · Kind: <kind> · Scenario: <room>
Run: <run id> · Product version: <version> (<commit>)

## What was seen
<the evidence, as the report gave it>

## Must
<the Must line it concerns, or none>

## Where it would be fixed
<file and section>

## How a fix is confirmed
<from the report>

## Reproduce
Start: <from the room's evidence/run.json - version, commit, OS, fixtures,
terminal size>
Steps: <the scenario and variant; for a live check, the steps from
evidence/live/steps.jsonl - keys, the intent of each, what the screen showed>
```

Write the body from the report and the record. Do not put a fix in it.

**Seen again:** a comment, not a new Issue:

```markdown
Seen again in <run id> (<room>). <one line of this run's evidence>
```

**Retest record**, on the Issue, when deciding a fix worked:

```markdown
Retest after <PR>: runs <a>, <b>, <c> (<variants>) - id not seen in any.
It appeared in <n> of <m> runs before the fix, so <k> clean runs were asked for.
```

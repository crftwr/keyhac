# Next major release — deferred breaking changes

A ledger of API changes that are worth making but cannot be made now: the public
surface (config API, MCP tools, `UINode`) is additive-only within 2.x, so a
change that renames, removes or reshapes anything waits for 3.0. This is the
place to write such an idea down at the moment it is noticed, with the reasoning
attached, so the next major release starts from a reasoned list instead of a
memory.

Relationship to the GitHub issues: an issue tracks work that can be done now;
an entry here is blocked by the compatibility policy itself, not by effort or
priority. When a major release is actually planned, entries graduate into
issues.

Each entry records: what changes, why the current shape is wrong, why it must
wait, and what the migration looks like.

## Rename MCP tool `describe_screen` → `describe_window`

**What.** Rename the MCP tool. No behavior change.

**Why.** The name says "screen"; the contract is one window: it resolves a
single window via `app=`/`title=` (focused window by default) and dumps that
window's element tree. Its own description and the tool table in
[ai-integration.md](../ai-integration.md) both say "a window's element tree".
`describe_window` states the contract and pairs naturally with `list_windows`.

**Why it waits.** The tool is released public surface. A clean rename breaks
both bundled skills, the docs, the tests, and any user-side prompts or notes
that name the tool. The additive alternative — shipping `describe_window` as an
alias — costs a permanent extra entry in every session's tool list (context
tokens on every request) and a which-one-do-I-call ambiguity, which is worse
than the misnomer. Mitigating the wait: in MCP the description travels with the
name, so callers see "Read a window's element tree" on every call — the
imprecision does not mislead in practice — and the old name does match the
authoring vocabulary ("read the screen before you write") used throughout the
docs and skills.

**Migration.** Rename in `keyhac/mcp/tools.py`; update both skills,
[ai-integration.md](../ai-integration.md) (§tool table, log examples) and
`tests/test_mcp.py` in the same commit. Keep the authoring-workflow prose
("open the screen the action will work against") — it describes operator
intent, not the tool name.

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

## `find_all` / `find_elements`: an optional truncation-stats out-parameter

**What.** An optional `stats=` out-parameter on `UINode.find_all` and
`uitree.find_elements` — a public `TruncationStats` (`reported` / `cut_points`
/ `by_depth` / `by_budget` / `deepest`) filled from the same walk that produced
the matches. The MCP `find_elements` no-match reply can then say whether the
search was cut short by the bounds, instead of today's deliberately plain
`no element matching ...` (issue #76).

**Why.** An empty result cannot distinguish "absent" from "not within the
walk's bounds". The runtime diagnostic that re-walked the tree to recover the
truncation marks was reverted (PR #77): it doubled the AX work on every
no-match, and it described a second snapshot the search never saw. A stats
out-parameter has neither defect — one walk, describing the tree that was
actually searched. `stats=None` keeps every existing caller unchanged, and
`UINode.find_all` forwards `**criteria`, so it flows through without a
signature change there.

**Why it waits.** Not breaking — the parameter is additive — but the same
underlying cost this ledger exists for: publication freezes a public stats
class whose field meanings (under the DAG dedupe, `roles=` filtering, `prune`)
settle the moment they are documented, all to serve one MCP error message.
Until then the ambiguity is taught statically in the `max_depth` schema
description and pinned by `test_a_no_match_stays_plain_even_when_the_walk_was_cut`.
Two events would justify building it: a second consumer appearing (actions
using the deep-search pattern wanting "did my search see everything?" without
paying their own `reread`), or a major release settling the shape deliberately.
Issue #76 was closed in favor of this entry.

**Migration.** None — additive. Ship the class and parameter in
`keyhac/core/uitree.py`, thread them through `UINode.find_all`, and teach the
MCP no-match branch in `keyhac/mcp/tools.py` to read the stats; the pinning
test is replaced deliberately in the same change.

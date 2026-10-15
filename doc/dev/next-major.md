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

**Update (issue #151).** The MCP consumer no longer needs this. The tool
now walks once with `reread` and matches that tree through the private
`uitree._matching`, so its no-match reply reads the cut off the snapshot
that was searched — one walk, no public class. What is left is the action
side: an action asking "did my search see everything?" of `find_all` itself.
That is now the only event that would justify building it.

**Migration.** None — additive. Ship the class and parameter in
`keyhac/core/uitree.py`, thread them through `UINode.find_all`, and teach the
MCP no-match branch in `keyhac/mcp/tools.py` to read the stats; the pinning
test is replaced deliberately in the same change.


## There is no `Action` class — only `ThreadedAction`

Everything user-facing calls the concept an **action**: the authoring skill is
`keyhac-action-authoring`, the reference is `doc/action-api.md`, the MCP tools
are `list_actions` / `start_action` / `get_action_result`. The only class is
`ThreadedAction`, and "action" is otherwise a *role* — whatever a key table
accepts, which is a string, a sequence of strings, a `KeyTable`, or any
callable. `StartRecordingKeys`, `ToggleRecordingKeys` and `InputText` are
actions in that sense and subclass nothing.

So "which subclass is enumerated?" is a question the vocabulary invites and
cannot answer from its own names. Two shapes would fix it, and they are not
the same change:

- **Rename `ThreadedAction` → `Action`**, on the grounds that the threaded
  part is an implementation detail of the only shape that needs a base class.
  Smallest, and it makes the enumerated set self-describing.
- **Introduce `Action` as a base above `ThreadedAction`**, so a fast action
  that needs no worker thread has somewhere to live and can still be
  enumerated. Larger, and it needs an answer for what stops a slow one being
  written that way — the main thread services the hook *and* every PuiKit
  window, so an action that blocks it freezes the keyboard.

**Why it waits.** `ThreadedAction` is released public surface that every
existing config and every authored action subclasses by name. Either change
breaks all of them at once, and neither is worth a compatibility shim: an
alias would leave two names for one thing in a vocabulary whose problem is
already that its names do not line up.

**Migration.** Rename in `keyhac/core/action.py`, re-export both names for one
release, update the two skills, `doc/configuration.md`, `doc/action-api.md`'s
generator list and the `_is_action` base-name match in
`keyhac/mcp/extensions.py`. The AST scan matches on the *trailing* base name,
so a config that still writes `ThreadedAction` keeps being discovered
throughout.

## Windows `read_text()`: one line separator

**What.** Normalise line endings to `\n` in `UINode.read_text()` on Windows,
as macOS already returns them. Today what comes back depends on the
provider: a native EDIT gives `\r\n`, a RichEdit's Value gives `\r` alone
(`'rich line one\rrich line two\r…'`), and Chromium and the console give
`\n`.

**Why.** One `config.py` is meant to run on both OSes. A regex anchored on
`\n`, or `splitlines()` followed by an equality test, behaves differently
depending on which control answered, and a RichEdit's `\r` defeats `$` with
`re.M` entirely. Found in the Windows pass on #168, filed as #169.

**Why it waits.** It changes what an existing action reads. An action written
against a Windows EDIT may already split on `\r\n`, or match it. #169's fix
was kept to texts that were being cut anyway (a Value at the 4,096-character
MSAA limit), so every shorter read, separators included, is unchanged.

One inconsistency already exists inside a single control. A RichEdit's Value
uses `\r`, but since #170 a RichEdit longer than the MSAA cut (4,095
characters) is read through WM_GETTEXT, which uses `\r\n`. A long RichEdit
was cut before #170, so nothing that worked changed, but it is the case
normalising would settle.

**Migration.** Actions that split on `\r\n` or `\r` switch to
`splitlines()` or to `\n`. The release note should list the controls whose
output changes.


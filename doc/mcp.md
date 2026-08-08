# Authoring actions with an AI agent

Keyhac can expose its element tools over MCP, so you can ask for an action in a
chat window and have the agent inspect the actual screen, write it, run it,
read the error, and fix it — instead of guessing at selectors and handing you
code to debug.

MCP is an open protocol and nothing here is specific to one vendor: Keyhac
serves ordinary JSON-RPC over loopback HTTP with a bearer token, which is a
plain Streamable HTTP endpoint. Any client that can reach one should work.
[Connecting an agent](#connecting-an-agent) lists which have actually been
tried, which is a shorter list.

**This is off unless you turn it on**, and it is worth understanding why before
you do: the endpoint reads the accessibility tree of every application you have
open and can run the actions you register. See [Security](#security).

> ### Experimental
>
> This feature — the MCP endpoint, the [action API](action_api.md), the
> authoring skill — is **experimental**, and the rest of Keyhac is not. Key
> tables, clipboard history, macros and window control keep the stability you
> expect from a 2.x release; this does not.
>
> **What that means in practice:** an upgrade may require editing actions you
> have already written. The usual rule that a minor release only *adds* does
> not apply here. In particular the shape of a `UINode` — how an element is
> identified, and how long a node you are holding stays valid — is not
> settled, and it is the shape everything else is built on.
>
> **Why it is shipped anyway:** it is off by default and additive, so it costs
> nothing to anyone who does not enable it, and the shape will be settled by
> people writing real actions rather than by more design. If you write some,
> what broke is the useful report.
>
> It stops being experimental when that shape stops moving — which needs, at
> minimum, actions generated against Windows as well as macOS, and by more
> than one person. Today the evidence is two sessions on one machine.

## Turning it on

Tick **MCP server** under **AI Integration** in the console window, or
**AI Integration → MCP Server** in the tray / menu bar menu. The console logs
the port it chose, and the choice is remembered across restarts.

There is deliberately no configuration API for this. An endpoint that reads
every window and can run your actions should be visibly on or visibly off; a
line in the middle of a several-hundred-line `config.py` tells you what was
asked for once, and nothing about what is true now. The same reasoning already
governs the keyboard hook, which has always been a checkbox rather than a
setting. *(2.2.0 had a `keymap.enable_mcp_server()` call for this. It is gone —
delete the line from your `config.py` and use the switch.)*

## Connecting an agent

| Client | Transport | Status |
|---|---|---|
| Claude Desktop | stdio → the bridge below | **Verified** — the actions in `examples/actions/` were authored through it |
| Claude Code | HTTP directly (`claude mcp add --transport http`) | Should work, **untried** |
| Anything else with MCP support | HTTP directly | Should work, **untried** |

"Untried" is not scepticism about those clients — nobody has run them against
this endpoint yet. If you do, whether it worked or not is the useful report.

**Connecting over HTTP directly**, for a client that can: the port and token
are published in `mcp.json` beside your `config.py`, the endpoint is
`http://127.0.0.1:<port>/`, and every request must carry
`Authorization: Bearer <token>`. The port is chosen at each start, so read the
file rather than pinning a number.

### The bridge, for stdio-only clients

Claude Desktop starts a local MCP server as a child process and talks JSON-RPC
over its stdin/stdout. Keyhac cannot be that child — it is a resident daemon
holding the keyboard hook and your focus history, and a second copy per
conversation would be a second hook and a second accessibility prompt. So
`keyhac-mcp-bridge` runs as the child instead and forwards to the daemon
already running. It holds no tool definitions and no logic, so the two cannot
drift apart.

Register it — Settings → Developer → Edit Config, or
`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "keyhac": { "command": "keyhac-mcp-bridge" }
  }
}
```

Restart Claude Desktop. **An absolute path is usually required**: GUI apps do
not inherit a shell PATH, and the console script only exists in whichever
environment Keyhac was pip-installed into — running the daemon straight from a
source checkout (`python -m keyhac`) does not create one at all. Find it with
`which keyhac-mcp-bridge`, or point at a virtualenv's copy:

```json
{
  "mcpServers": {
    "keyhac": { "command": "/path/to/.venv/bin/keyhac-mcp-bridge" }
  }
}
```

The bridge does not have to come from the same install as the daemon — it reads
the endpoint file and forwards.

## Add the authoring skill

The tools tell the agent what your screen contains. The skill tells it how to
write an action — the rules that stop it emitting `sleep`, coordinates, and
unverified writes. Without it you will get plausible code that breaks on a
slower machine.

Skills are a Claude feature, so the packaged bundle is for Claude Desktop.
The content is not: `keyhac/skills/action-authoring/` is Markdown, and any
agent that can be given documents can be given these. What it cannot be given
is the *habit* of consulting them unprompted, which is the part a skill buys.

Claude Desktop → Settings → カスタマイズ / Customize → Skills → Add → **Upload
skill**, and give it the bundle:

```
make skill-bundle        # writes dist/keyhac-action-authoring-skill.zip
```

The uploader states two requirements, and the bundle is built to meet both:
the archive must contain a `SKILL.md` **at its root** (not nested in a folder),
and that file must carry its name and description as YAML frontmatter. Uploads
go through a security scan that takes a minute or two before the skill becomes
usable.

The skill documents *this version's* API, so re-upload it after upgrading
Keyhac — `make skill-bundle` stamps the version into the bundle so a mismatch
is visible.

**The skill is not the connection, and neither step implies the other.** The
skill is knowledge — writing rules and an API reference, with no way to reach
your machine; the tools come from the bridge registered above. Upload only the
skill and the agent will correctly tell you it cannot see your windows. Connect
only the tools and it will see them, then write actions that use `sleep` and
screen coordinates.

## What the agent can do

| Tool | |
|---|---|
| `list_windows`, `get_focus` | what is open, what is focused |
| `describe_screen` | a window's element tree as indented text |
| `find_elements` | targeted search by role / name / identifier / text |
| `read_text` | an element's whole text — terminal scrollback, editor buffer |
| `enable_content_access` | make a Chromium/Electron app expose its content (macOS) |
| `list_actions`, `run_action` | run a registered action and return what it logged |
| `reload_config` | pick up an edited action without restarting |

`run_action` only runs actions you have registered by name:

```python
keymap.register_action("extract_records", ExtractRecords())
```

Registering is per-action and opt-in — it is the line between "the agent can
run this" and "the agent can run anything the config defines".

## The loop

1. Open the screen the action will work against.
2. Ask for what you want, in your own words. Naming the application and the
   output is useful; naming an API is not — if you find yourself typing
   `find_element`, tell us, because that means the skill is failing.
3. The agent reads the screen and writes the action.
4. Paste it into `config.py` (or an imported module), register it, and ask it
   to reload and run it.
5. It reads the failure itself and fixes it.

Step 4 is manual for now: no tool writes Python to your disk, deliberately.

**Restart Keyhac after upgrading it.** `reload_config` reloads your `config.py`,
not Keyhac's own modules — a new version of the tools is only picked up by a
restart. (Found the hard way: two live checks of a change to the tools were
really testing the build the daemon started with.)

## Working from a recorded demonstration

Claude Desktop can record a task — screen, clicks, typing, and **voice** — and
turn it into a skill. That skill is a good *input* to an action, with one
division of labour that matters:

- **The recording supplies intent**: what you were trying to do, which values
  vary, where the iteration boundary is, what you checked before moving on.
  The narration is the important half — a click log alone shows that you typed
  a string, not why.
- **The tools supply the selectors**: a recording has pixels, and an action
  addressed by pixels is a failed action. Claude re-derives every selector from
  the live tree via `describe_screen`.

So the shape is: record the task, then ask Claude to turn that skill into a
Keyhac action *against the screen in front of it*. Expect it to ask questions
first — a recording shows you set a filter to "active" but not whether that is
a constant or an argument.

**The recording is Anthropic's, not Keyhac's**: it is captured by Claude
Desktop under its own consent dialog and sent to Claude. Keyhac never records
your keyboard, and does not read the recording. Its warning applies —
don't type passwords or show private material while recording.

## Security

- **Off by default**, and the switch is visible. Nothing listens until you tick
  **AI Integration → MCP server**, and while it is on the checkbox and the menu
  item both say so — which a line in a config file cannot.
- **Loopback only**, with a token generated at each start and published in
  `mcp.json` beside your config, readable only by you. The bridge reads it;
  another process on the machine cannot use the endpoint without it.
- **No tool writes files or types text.** Reading trees and pressing buttons in
  an app you are looking at is one thing; letting a remote model put Python on
  your disk is another decision, and it has not been made.
- **`run_action` is limited to registered actions**, by name.
- Turn it off with the same switch; the token file is deleted then, and when
  Keyhac stops.

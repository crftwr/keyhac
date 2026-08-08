# AI Integration

> ## Give this page's URL to your AI agent
>
> Ask it to set up Keyhac's skill and MCP server, and it can do most of it
> from here — it will read [Setting it up](#setting-it-up) below, which is
> written for it, and ask you for the parts only you can do. You do not have
> to follow these instructions yourself.
>
> Keyhac's tray menu has the link: **AI Integration → Setup Guide**, pinned to
> the version you are running.

Keyhac can expose its element tools over MCP, so you can ask for an action in a
chat window and have the agent inspect the actual screen, write it, run it,
read the error, and fix it — instead of guessing at selectors and handing you
code to debug. The action it writes is plain Python on a key binding: no model
runs when you later press the key.

MCP is an open protocol and nothing here is specific to one vendor: Keyhac
serves ordinary JSON-RPC over loopback HTTP with a bearer token, which is a
plain Streamable HTTP endpoint. Any client that can reach one should work.
[Which clients have been tried](#which-clients-have-been-tried) is a shorter
list.

**This is off unless you turn it on**, and it is worth understanding why before
you do: the endpoint reads the accessibility tree of every application you have
open and can run the actions you register. See [Security](#security).

> ### Experimental
>
> This feature — the MCP endpoint, the [action API](action-api.md), the
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

## Setting it up

**This section is addressed to the AI agent.** If you are a person reading it,
you can follow it too — but handing the URL over is the shorter path.

There are **two installs, and neither implies the other**. Getting one without
the other is the common failure, and the halves fail very differently: with the
skill and no connection, you will correctly say you cannot see the user's
screen, which looks like the feature being broken. With the connection and no
skill, everything appears to work and you will write actions containing `sleep`
and screen coordinates, which break on a different machine.

How your host installs an MCP server or a skill is **your** knowledge, and this
page deliberately does not guess at it. What follows is only what Keyhac knows
and you cannot.

### 1. The skill

Download `keyhac-action-authoring-skill.zip` from the Keyhac release matching
the running version — the tag is in the URL you fetched this page from:

```
https://github.com/crftwr/keyhac/releases/tag/v<version>
```

Install it however this host takes a skill. It documents *that version's* API,
so a mismatch is worth mentioning to the user rather than working around.

### 2. The connection

Ask the user to switch the endpoint on first: **AI Integration → MCP Server**
in Keyhac's tray menu, or the **AI Integration** checkbox in its console
window. It is off by default and you cannot turn it on yourself.

Then:

- Keyhac publishes the connection details as `mcp.json` beside the user's
  `config.py` — `~/.keyhac/mcp.json` unless Keyhac's console reported a
  different config directory at startup.
- That file holds a `port` and a `token`. The endpoint is
  `http://127.0.0.1:<port>/`, one JSON-RPC request per POST, and every request
  needs an `Authorization: Bearer <token>` header.
- **The port changes every time Keyhac restarts.** Point the client at the file
  if it can read one. If it can only take a fixed value, say so — that is a
  real limitation, not something to work around by pinning today's port.
- If this host can only launch an MCP server as a subprocess over stdio, use
  the command `keyhac-mcp-bridge` instead of the HTTP endpoint. It takes no
  arguments and finds that file itself. A GUI client usually needs its absolute
  path, since it does not inherit a shell `PATH`.

### 3. How to go about it

- **Back up** any other application's config file before editing it, and show
  the user the change.
- **Do not quit or restart another application.** Ask, and say what they should
  see afterwards.
- **Verify rather than assume.** Once the client is restarted, call
  `list_windows`. If the user's own windows come back, it worked. If the tool
  is not there, say so plainly instead of guessing at the cause — and if you
  are unsure of this host's config path or schema, ask rather than writing a
  plausible one.
- Anything you cannot do from here, ask the user for. Most of it you can.

## Which clients have been tried

| Client | Transport | Status |
|---|---|---|
| Claude Desktop | stdio → [the bridge](#the-bridge-for-stdio-only-clients) | **Verified** — the actions in `examples/actions/` were authored through it |
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

**Get it from the release**, as `keyhac-action-authoring-skill.zip`:

```
https://github.com/crftwr/keyhac/releases/tag/v<your version>
```

Match the version you are running — Keyhac's console prints it at startup, and
the release for it is the one whose API the skill describes. The bundle carries
that version stamped inside, so a mismatch is visible after the fact rather
than silent.

Skills are a Claude feature, so the packaged bundle is shaped for Claude
Desktop: Settings → カスタマイズ / Customize → Skills → Add → **Upload skill**.
The uploader states two requirements and the bundle meets both — a `SKILL.md`
at the archive root, carrying its name and description as YAML frontmatter.
Uploads go through a security scan that takes a minute or two.

The *content* is not Claude-specific. Unzipped it is Markdown, and any agent
that can be handed documents can be handed these. What that does not buy is the
*habit* of consulting them unprompted, which is the part a skill mechanism
provides.

(Building it from a source checkout, if you are working on Keyhac itself:
`make skill-bundle` writes the same zip into `dist/`.)

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
| `list_actions` | what is registered, what is running, how each last ended |
| `start_action`, `get_action_result`, `cancel_action` | start a registered action, collect what it logged, stop it |
| `reload_config` | pick up an edited action without restarting |

`start_action` only runs actions you have registered by name:

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
- **`start_action` is limited to registered actions**, by name, and
  `cancel_action` can only stop what it started.
- Turn it off with the same switch; the token file is deleted then, and when
  Keyhac stops.

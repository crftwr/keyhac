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
> **And it is not only minor releases. The 2.2.x line is where this feature is
> being built**, so a *patch* release in it can change the AI surface too —
> 2.2.1 removed `keymap.enable_mcp_server()` and replaced `run_action` with
> three tools. Read these notes before upgrading if you have turned it on.
> Everything outside this feature keeps what a patch number promises.
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

### Allow extension writes

The second switch, beside the first, lets the agent save action modules into
`~/.keyhac/extensions/` itself — which is what turns a three-round fix into
three tool calls instead of three trips through your clipboard.

It is a separate switch because the two have different natural lifetimes. The
endpoint is worth leaving on; **write access is worth minutes**, while you are
actually authoring something. So this one is off by default, is **not**
remembered across restarts, and **lapses on its own 60 minutes** after you tick
it. Re-tick it when you need it again.

The timeout is fixed from when you switch it on and is not extended by use.
That is deliberate: a sliding window would let whatever is driving the endpoint
keep its own permission alive by writing periodically, which is the one thing
this is here to prevent.

Registering an action is still yours — see [The loop](#the-loop). Writing a file
and deciding it may run are different decisions, and only the first one moved.

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
- It also holds `bridge`: the absolute path to the stdio shim, for a host that
  cannot speak HTTP. The key is **absent** when this install generated no
  console script (a source checkout), so its presence is the test for whether a
  stdio client can be configured at all.
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

Restart Claude Desktop — fully quit it (⌘Q); closing the window does not reload
the config.

**An absolute path is usually required**, because GUI apps do not inherit a
shell `PATH`.

The path is published as `bridge` in `~/.keyhac/mcp.json`, so read it from there
rather than transcribing it — and the Keyhac console prints it at startup on the
line after "MCP server listening". Failing both, it depends on how Keyhac was
installed:

| Install | Path |
|---|---|
| macOS app bundle | `/Applications/Keyhac.app/Contents/Resources/bin/keyhac-mcp-bridge` |
| Windows bundle | `keyhac-mcp-bridge.cmd`, beside `Keyhac.exe` |
| `pip install keyhac` | `which keyhac-mcp-bridge`, or the virtualenv's `bin/` |
| Source checkout | `.venv/bin/keyhac-mcp-bridge`, created by `make install` — note it is *not* on `PATH` when Keyhac is started with `make run` |

```json
{
  "mcpServers": {
    "keyhac": {
      "command": "/Applications/Keyhac.app/Contents/Resources/bin/keyhac-mcp-bridge"
    }
  }
}
```

If a client refuses to launch the Windows `.cmd` directly — some spawn
executables without a shell — point it at the interpreter instead:

```json
{
  "mcpServers": {
    "keyhac": {
      "command": "C:\\Program Files\\Keyhac\\runtime\\python.exe",
      "args": ["-m", "keyhac.mcp.bridge"],
      "env": {
        "PYTHONPATH": "C:\\Program Files\\Keyhac\\app;C:\\Program Files\\Keyhac\\Lib\\site-packages"
      }
    }
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
| `write_extension` | save a module into `extensions/` — only while [the write switch](#allow-extension-writes) is on |
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
4. It saves the module itself, if you have ticked
   [Allow extension writes](#allow-extension-writes) — otherwise you paste it
   into `~/.keyhac/extensions/`.
5. **You register it, once**: paste the `configure()` block it hands you into
   `config.py`. Then ask it to reload and run.
6. It reads the failure itself, fixes it, and saves again. Step 5 does not
   repeat — the name is already registered, so every round after the first is
   the agent's alone.

Step 5 is the manual one, and it is the decision worth keeping manual: naming
an action in your `config.py` is what makes it runnable at all, and that is a
line you draw rather than one the agent draws for itself.

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
- **Writing is a second switch**, off by default, gone after a restart, and
  lapsing 60 minutes after you tick it. `write_extension` is the only tool that
  puts anything on disk, and it can only write a `.py` under
  `~/.keyhac/extensions/` — the name has to be an importable module name, which
  is what rules out paths and traversal. It replaces nothing silently: the
  previous version is kept as a `.bak-<timestamp>` beside it, and every write
  is logged to the console with a `+N/-M` line count.
- **Nothing types text or presses keys.** Reading trees is one thing; driving
  your keyboard from a chat window is a decision that has not been made.
- **`start_action` is limited to registered actions**, by name, and
  `cancel_action` can only stop what it started.
- Turn it off with the same switch; the token file is deleted then, and when
  Keyhac stops. Stopping the endpoint closes the write window too.

### What this does not protect you from

Worth knowing before you tick either switch.

`describe_screen` and `read_text` put the contents of your windows — including
web pages — into the model's context. That text is untrusted: a page can
contain something written to be read *by an agent* rather than by you. The
authoring skill tells the agent that screen content is data and never an
instruction, and that is a real mitigation rather than a complete one.

What it means concretely: **while the write switch is on, a page you happen to
have open is part of the trusted input**. The fences above bound the damage — a
module nothing imports never runs, and the backup means nothing is
unrecoverable — but a module that *is* already registered goes live at the next
reload, and there is no check that stops that. That is the same trust you extend
when you paste an action you did not read, which is what everyone does; it is
stated here rather than left implicit.

The practical answer is the one the design already points at: turn writing on
while you are authoring, and let it lapse. That way the window when it matters
is the window you are watching.

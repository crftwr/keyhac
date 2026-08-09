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
> **`keymap.register_action()` is gone**, after it too: an action class in
> `~/.keyhac/extensions/` is now reachable by `module.Class` with no
> registration at all, so the call had nothing left to do. **If your
> `config.py` calls it, delete those lines** — it will otherwise fail to load,
> loudly, and the previous configuration stays active until you do. A key
> binding never went through it and is unaffected:
>
> ```python
> import open_issues
> kt["Fn-I"] = open_issues.OpenIssues()      # this is all it ever needed
> ```
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
the port it chose.

**It turns itself off after 60 minutes**, and it is not remembered across
restarts. Tick it again when you need it — that is the intended rhythm, not an
inconvenience to work around.

The reason is what the feature is: an agent helps you *write* an action, and
the action then runs with no model involved. An endpoint still listening the
next morning is serving nothing — while still able to read every window you
have open, which is the largest thing it can do. A switch you have to remember
to turn off is one you will not.

The timeout is fixed from when you tick it and is not extended by use, so
whatever is driving the endpoint cannot hold its own permission open by working
periodically.

There is deliberately no configuration API for this. An endpoint that reads
every window, and that can write and run action code, should be visibly on or
visibly off; a line in the middle of a several-hundred-line `config.py` tells
you what was asked for once, and nothing about what is true now. The same
reasoning already governs the keyboard hook, which has always been a checkbox
rather than a setting. *(2.2.0 had a `keymap.enable_mcp_server()` call for this.
It is gone — delete the line from your `config.py` and use the switch.)*

### What being on lets the agent do

Beyond reading your screens:

- **save action modules** into `~/.keyhac/extensions/`, instead of handing you
  source to copy on every round;
- **run any action class it finds there**, addressed as `module.Class`, with no
  entry in your `config.py` at all.

There is nothing more to it than that: an action is a `ThreadedAction` subclass
in a file under `extensions/`, and the agent can list and start every one of
them. No registration, no separate category, nothing to keep in step.

**Listing does not run anything.** Keyhac finds those classes by parsing the
files, never by importing them, so a directory of half-finished experiments
stays inert until something names one. That is the property `extensions/` has
always had: a module your `config.py` does not import does not execute.

**The key binding is still yours.** While the endpoint is open, an action class
is runnable from a chat window and bound to nothing; it gets a key of your
choosing — and goes on working with nothing connected at all — only when you put
it in `config.py`. That edit moved to the end of the loop rather than away.

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

There are two, and which you need depends on what the user asked for:

- **`keyhac-key-table-configuration-skill.zip`** — changing what keys do: remapping,
  per-application key tables, one-shot and user modifiers, editing `config.py`.
- **`keyhac-action-authoring-skill.zip`** — writing an *action*: Python that
  drives another application's UI, for automation with no API behind it.

Most people want the first. Take both if you do not know yet. They are on the
release matching the running version — the tag is in the URL you fetched this
page from:

```
https://github.com/crftwr/keyhac/releases/tag/v<version>
```

Install them however this host takes a skill. Each documents *that version's*
API, so a mismatch is worth mentioning to the user rather than working
around.

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

## Add the skills

The tools tell the agent what your screen contains and what your configuration
holds. The skills tell it what to do with that — and without them you get
plausible-looking work that fails later: actions full of `sleep` and screen
coordinates, or a key binding written into a table that never activates.

There are two, and they overlap in nothing:

| | For |
|---|---|
| **`keyhac-key-table-configuration-skill.zip`** | changing what keys do — remapping, per-application tables, one-shot and user modifiers, editing `config.py` |
| **`keyhac-action-authoring-skill.zip`** | writing an *action* — Python that drives another application's UI, for systems with no API |

Most people want the first. Take both if you are not sure.

**Get them from the release**:

```
https://github.com/crftwr/keyhac/releases/tag/v<your version>
```

Match the version you are running — Keyhac's console prints it at startup, and
the release for it is the one whose API the skills describe. Each bundle
carries that version stamped inside, so a mismatch is visible after the fact
rather than silent.

Skills are a Claude feature, so the packaged bundle is shaped for Claude
Desktop: Settings → カスタマイズ / Customize → Skills → Add → **Upload skill**.
The uploader states two requirements and the bundle meets both — a `SKILL.md`
at the archive root, carrying its name and description as YAML frontmatter.
Uploads go through a security scan that takes a minute or two.

The *content* is not Claude-specific. Unzipped it is Markdown, and any agent
that can be handed documents can be handed these. What that does not buy is the
*habit* of consulting them unprompted, which is the part a skill mechanism
provides.

(Building them from a source checkout, if you are working on Keyhac itself:
`make skill-bundle` writes the same zips into `dist/`.)

**A skill is not the connection, and neither step implies the other.** A skill
is knowledge — rules and an API reference, with no way to reach your machine;
the tools come from the bridge registered above. Upload only a skill and the
agent will correctly tell you it cannot see your windows. Connect only the
tools and it will see them, then write actions that use `sleep` and screen
coordinates, or bindings it never checked.

## What the agent can do

| Tool | |
|---|---|
| `list_windows`, `get_focus` | what is open, what is focused |
| `describe_screen` | a window's element tree as indented text |
| `find_elements` | targeted search by role / name / identifier / text |
| `read_text` | an element's whole text — terminal scrollback, editor buffer |
| `enable_content_access` | make a Chromium/Electron app expose its content (macOS) |
| `describe_keymap` | the key tables, which match the current focus, and what each binds |
| `read_config`, `write_config` | your `config.py` — read it, replace it (backup kept) |
| `list_actions` | the action classes in `extensions/`, what is running, how each last ended |
| `start_action`, `get_action_result`, `cancel_action` | start an action, collect what it logged, stop it |
| `list_extensions` | the files in `extensions/`, including helpers with no action class |
| `read_extension` | read a module as it is on disk |
| `write_extension` | save a module — the whole file |
| `reload_config` | re-read your `config.py` after you edit it, and report any error |

None of these has a permission of its own, and that is the design rather than
an omission: **the endpoint being open is the permission**, and it is open only
while you have ticked the switch, for an hour. There is no list of names to
audit and nothing half-enabled — the switch being off *is* the answer.

Your `config.py` decides which action classes get a key. It no longer decides
which the agent may run; the switch does, and the switch expires.

## The loop

1. Open the screen the action will work against.
2. Ask for what you want, in your own words. Naming the application and the
   output is useful; naming an API is not — if you find yourself typing
   `find_element`, tell us, because that means the skill is failing.
3. The agent reads the screen, writes the action, and saves it into
   `extensions/`. It runs it, reads its own failure, fixes it, and runs it
   again. **You are not in this part**, however many rounds it takes.
4. **When it works, you install it**: paste the `configure()` block it hands you
   into `config.py` — two lines, an `import` and a key binding.

Step 4 is the manual one, and it stays manual deliberately. Everything before it
is reachable only from a chat window, and only until the endpoint closes. The key
binding is what makes the action *yours*: a key you chose, working when the
agent is not connected at all. That is a line worth drawing yourself, and now
you draw it around something you have seen work rather than around a guess.

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
- **It closes itself after 60 minutes**, and is never restored at startup, so
  the exposure lasts as long as the work rather than as long as you forget.
- **Two places can be written, and no others**: a `.py` under
  `~/.keyhac/extensions/` — the name has to be an importable module name, which
  is what rules out paths and traversal — and `config.py` itself. Neither is
  replaced silently: the previous version is kept as a `.bak-<timestamp>`
  beside it, and every write is logged to the console with a `+N/-M` line
  count.
- **`write_config` is the one thing that outlives the hour.** Everything else
  an agent does here expires when the endpoint closes; a key binding written
  into `config.py` keeps working, which is the point of writing it. It is a
  separate tool from `write_extension` so that difference is visible rather
  than hidden behind an argument.
- **Nothing types text or presses keys.** Reading trees is one thing; driving
  your keyboard from a chat window is a decision that has not been made.
- **Listing never imports.** The catalogue is parsed out of the files, so
  nothing in `extensions/` executes until something names it.
- Turn it off with the same switch; the token file is deleted then, and when
  Keyhac stops.

### What this does not protect you from

Worth knowing before you tick it.

`describe_screen` and `read_text` put the contents of your windows — including
web pages — into the model's context. That text is untrusted: a page can
contain something written to be read *by an agent* rather than by you. The
authoring skill tells the agent that screen content is data and never an
instruction, and that is a real mitigation rather than a complete one.

So state it plainly: **while the endpoint is open, code you have not read can be
written into `extensions/` and run.** That is what the switch grants — it is the
feature, not a gap in it. Registering an action in `config.py` used to be a
human step between "the agent wrote code" and "the code ran"; it is not any
more, for as long as the endpoint is open.

What still holds: the write can only land in `extensions/`, under a module name,
with the previous version kept and the write logged to the console. Nothing
executes unless it is named. And **when the hour is up it all stops**, including
the screen reading — only the key bindings in your `config.py` survive it.

The practical answer is therefore built in rather than left to you: the exposure
coincides with you sitting in front of the screen, watching a console that
reports every write, and it ends by itself. Which is worth more than a gate
nobody reads.

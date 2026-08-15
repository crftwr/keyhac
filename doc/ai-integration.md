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

Keyhac can expose its tools over MCP, so you can ask for what you want in a chat
window and have the agent carry it out against the running Keyhac. Two kinds of
thing: **changing what a key does** — it reads your key tables and your
`config.py`, writes the change back, and reloads it — and **writing an action**,
where it inspects the actual screen, writes the code, runs it, reads the error,
and fixes it, instead of guessing at selectors and handing you code to debug.

What it leaves behind is plain Python in your own files. No model runs when you
later press the key.

MCP is an open protocol and nothing here is specific to one vendor: Keyhac
serves ordinary JSON-RPC over loopback HTTP with a bearer token, which is a
plain Streamable HTTP endpoint. Any client that can reach one should work.
[Which clients have been tried](#which-clients-have-been-tried) is a shorter
list.

**This is off unless you turn it on**, and it is worth understanding why before
you do: the endpoint reads the accessibility tree of every application you have
open and can run the actions you register. See [Security](#security).

> ### Upgrading from 2.2.0 or 2.2.1
>
> Two calls were removed while this feature was being built. A `config.py` that
> still makes either fails to load — loudly, with your previous configuration
> staying active until you fix it.
>
> - **`keymap.enable_mcp_server()`** — the endpoint is a checkbox now:
>   **AI Integration > MCP Server**, below.
> - **`keymap.register_action()`** — an action class in `~/.keyhac/extensions/`
>   is reachable as `module.Class` with no registration at all, so the call had
>   nothing left to do. Delete those lines. A key binding never went through it
>   and is unaffected:
>
> ```python
> import open_issues
> kt["Fn-I"] = open_issues.OpenIssues()      # this is all it ever needed
> ```

## Turning it on

Tick **AI Integration: MCP Server** in the console window, or
**AI Integration → MCP Server** in the tray / menu bar menu. The console logs
the port it chose, and then one line for every call the agent makes — which
tool, with which arguments, and how big an answer went back:

```
INFO [keyhac.MCP] describe_screen(app='Chrome') -> 4812 chars
INFO [keyhac.MCP] write_extension(name='translate_clipboard', source='import ...') -> 118 chars
```

Set the console's level to **Debug** for the whole of each request and reply
rather than a summary. The envelope stays on one line and the payload is
printed underneath as itself, so a screen dump reads as the tree it is:

```
DEBUG [keyhac.MCP] <- {"jsonrpc":"2.0","id":4,"result":{"content":[{"type":"text","text":"<result.content[0].text>"}],"isError":false}}
AXWindow 'my-projects (Workspace)'
  AXGroup 'my-projects (Workspace)'
    AXWebArea 'my-projects (Workspace)'
      AXStaticText = 'Diff editor'
```

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
  entry in your `config.py` at all;
- **edit your `config.py`**: read it, write it back with the change in it, and
  reload it. A remap, a per-application key table, the two lines that put an
  action on a key — you ask for it and it lands in the file, rather than coming
  back as something to paste.

An action is a `ThreadedAction` subclass in a file under `extensions/`, and the
agent can list and start every one of them. No registration, no separate
category, nothing to keep in step.

`config.py` is written whole, never patched, so the agent is told to read it
first and change as little as it can. **Keyhac keeps the version it replaced**
as a `.bak-<timestamp>` beside it and logs every write to the console with a
`+N/-M` line count — a rewrite you did not want is visible and undoable, not
silent.

**Listing does not run anything.** Keyhac finds those classes by parsing the
files, never by importing them, so a directory of half-finished experiments
stays inert until something names one. That is the property `extensions/` has
always had: a module your `config.py` does not import does not execute.

**The key binding is still yours — you no longer have to type it.** While the
endpoint is open, an action class is runnable from a chat window and bound to
nothing. What `config.py` adds is a key of your choosing that goes on working
with nothing connected at all, and either of you can write that line: ask for
the two lines and paste them, or ask for the binding and let the agent put it
there. The key is the one you named, in your file, either way.

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
page mostly does not guess at it. What follows is what Keyhac knows and you
cannot — plus, where a host is common enough to be worth naming, what is true
for it today: Claude Desktop's config file is in
[The bridge, for stdio-only clients](#the-bridge-for-stdio-only-clients), and
Claude's skill uploader in [Add the skills](#add-the-skills). Both sections are
below this one, and both are for you as much as for the user.

### 1. The skill

There are two, and which you need depends on what the user asked for:

- **`keyhac-key-table-configuration-skill.zip`** — changing what keys do: remapping,
  per-application key tables, one-shot and user modifiers, editing `config.py`.
- **`keyhac-action-authoring-skill.zip`** — writing an *action*: Python that
  drives another application's UI, for automation with no API behind it.

Most people want the first. Take both if you do not know yet. They are on the
release matching the running version — the tag is the segment after `blob/` in
the URL you fetched this page from, and it already carries its own `v`:

```
https://github.com/crftwr/keyhac/releases/tag/<tag>
```

Install them however this host takes a skill — but expect that to be **a step
only the user can take**. On most hosts a skill arrives by uploading a file
through a settings UI, with no tool behind it, so do not spend the round trying
to do it yourself. Give them the link and the steps, say what it buys, and go on
with what you can do. [Add the skills](#add-the-skills) has the path for Claude,
including the setting that has to be on first.

**Unless this host keeps its skills as files** — then it is yours to do, and you
should simply do it rather than handing back instructions. Claude Code reads
`~/.claude/skills/<name>/SKILL.md`, so unzipping each bundle into a directory of
its own under there is the entire install. The test is not what the host is
called but whether you can write where it looks, so check before assuming the
upload path.

Each bundle documents *that version's* API, so a mismatch is worth mentioning to
the user rather than working around.

### 2. The connection

Ask the user to switch the endpoint on first: **AI Integration → MCP Server**
in Keyhac's tray menu, or the **AI Integration: MCP Server** checkbox in its
console window. It is off by default and you cannot turn it on yourself.

Then settle which transport this host has, because it decides everything below.
A client that can only launch a server as a child process uses the bridge, and
**Claude Desktop is that kind** — so if that is where you are, the endpoint
details are background and [the bridge](#the-bridge-for-stdio-only-clients) is
the thing to configure.

A client that *can* open an HTTP connection has a choice, and the bridge is
usually still the better half of it. The question is not which transports the
host speaks, but whether the entry it stores can re-read `mcp.json`: a saved
HTTP entry pins today's port and token, and Keyhac picks new ones every time it
starts, so it goes stale the next time the user quits Keyhac. The bridge reads
that file per request and therefore never needs pointing again. Claude Code is
the worked example — it takes either, and the stdio registration is the one that
survives a restart.

- Keyhac publishes the connection details as `mcp.json` beside the user's
  `config.py` — `~/.keyhac/mcp.json` unless Keyhac was started against a
  different config directory. You cannot read Keyhac's console, so if the file
  is not there, ask the user what it printed at startup rather than hunting.
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
  path, since it does not inherit a shell `PATH` —
  [The bridge, for stdio-only clients](#the-bridge-for-stdio-only-clients) has
  the config snippets and where that path is for each kind of install.

### 3. How to go about it

- **Back up** any other application's config file before editing it, and show
  the user the change. **If you have no way to write files at all** — which is
  ordinary, not a failure — print the exact path and the exact JSON, and ask
  them to paste it in. Say whether the file is being created or merged into.
- **Do not quit or restart another application.** Ask, and say what they should
  see afterwards.
- **If the client to restart is the one you are running inside**, restarting it
  ends this conversation — quitting Claude Desktop quits you with it. Say so
  *before* they do it, and leave them what to do next: open a new chat and ask
  for `list_windows`, which is where the check below now happens.
- **Verify rather than assume.** After the restart, call `list_windows`. If the
  user's own windows come back, it worked. If the tool is not there, say so
  plainly instead of guessing at the cause — and if you are unsure of this
  host's config path or schema, ask rather than writing a plausible one.
- Anything you cannot do from here, ask the user for — and on Claude Desktop
  that includes the config edit itself, which offers no way to *add* or edit an
  MCP server entry, only to delete one. Assume the paste is theirs and make it
  cheap: the absolute bridge path, and the exact JSON ready to drop in.

## Which clients have been tried

| Client | Transport | Status |
|---|---|---|
| Claude Desktop | stdio → [the bridge](#the-bridge-for-stdio-only-clients) | **Verified on macOS and Windows** — the actions in `examples/actions/` were authored through it, and one authored on macOS then ran unchanged on Windows |
| Claude Code | stdio → [the bridge](#the-bridge-for-stdio-only-clients), registered with `claude mcp add` | **Verified on macOS** — set up from this page's URL and nothing else; its own health check reaches the daemon. Calling the tools from inside a conversation is **untried** |
| Anything else with MCP support | HTTP directly | Should work, **untried** |

"Untried" is not scepticism about those clients — nobody has run them against
this endpoint yet. If you do, whether it worked or not is the useful report.

**What was verified on Claude Code**, since the row above splits: handing it
this page's URL was the whole of the setup. It installed both skills into
`~/.claude/skills/` itself — no upload, no user step — registered the bridge at
user scope, and connected. What has *not* been watched is a live conversation
calling `list_windows` as a native tool: the session that did the setup was
older than the server it had just registered, which is the ordinary case and the
reason the last step of [How to go about it](#3-how-to-go-about-it) says to
verify in a new one.

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
| Windows zip / portable bundle | `keyhac-mcp-bridge.exe`, beside `Keyhac.exe` |
| Windows Microsoft Store install | `%LOCALAPPDATA%\Microsoft\WindowsApps\keyhac-mcp-bridge.exe` — see below |
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

**On the Microsoft Store install, do not point a client inside
`C:\Program Files\WindowsApps`.** Nothing in there can be started by a program
that is not part of the package — Windows answers "Access is denied" no matter
how the path is spelled, and the file is perfectly readable the whole time,
which makes it look like a Keyhac problem rather than a Windows one. What you
get is an MCP server that disappears the moment the client starts it, with
nothing in its log. Use the command Windows registers for the package instead:

```json
{
  "mcpServers": {
    "keyhac": {
      "command": "C:\\Users\\<you>\\AppData\\Local\\Microsoft\\WindowsApps\\keyhac-mcp-bridge.exe"
    }
  }
}
```

That directory is on `PATH`, so a client that inherits one can just say
`keyhac-mcp-bridge`. Either way, `~/.keyhac/mcp.json` publishes the exact path
for the install you are running — read it from there rather than transcribing
this. *(Keyhac 2.2.2 and earlier published a path inside the package here, which
could not work; the fix is in 2.2.3.)*

Older Windows bundles shipped `keyhac-mcp-bridge.cmd` rather than the `.exe`. It
is still there and still works — it now just forwards — so a config written
against 2.2.0–2.2.2 needs no change.

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
https://github.com/crftwr/keyhac/releases/tag/<tag>
```

`<tag>` is `v` followed by the version Keyhac's console prints at startup —
`v2.2.1`, for instance. Match the version you are running: that release is the
one whose API the skills describe. Each bundle carries the version stamped
inside, so a mismatch is visible after the fact rather than silent.

The bundle is shaped for Claude's skill uploader, whose two requirements it
meets — a `SKILL.md` at the archive root, carrying its name and description as
YAML frontmatter. In Claude today that is **Customize → Skills → + → Create
skill → Upload a skill**, then pick the zip. Menus move between releases; if
that path is not what you see, look for **Skills** in the settings rather than
trusting this line.

**Turn on "Code execution and file creation" first** — Settings → Capabilities,
or for a Team or Enterprise plan an administrator enables it for the
organization. Skills run on it, and while it is off they will not appear at all,
which looks like a plan restriction and is not one. This is the usual reason an
upload seems to go nowhere. On an Enterprise organization with skill scanning
enabled, an uploaded skill is also scanned before it can run — a minute or two.

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
| `delete_extension` | retire a module — renamed to a backup beside it, not erased |
| `reload_config` | re-read your `config.py` after you edit it, and report any error |

None of these has a permission of its own, and that is the design rather than
an omission: **the endpoint being open is the permission**, and it is open only
while you have ticked the switch, for an hour. There is no list of names to
audit and nothing half-enabled — the switch being off *is* the answer.

Your `config.py` decides which action classes get a key. It no longer decides
which the agent may run; the switch does, and the switch expires.

## The loop

This is the loop for *writing an action*, which is the long one. **Changing what
a key does is shorter**: ask for it, and the agent reads your `config.py` and
your key tables, writes the change back, reloads it, reports what the reload
said, and asks you to press the key. Nothing has to be discovered from the
screen, so there is no round trip through it.

1. Open the screen the action will work against.
2. Ask for what you want, in your own words. Naming the application and the
   output is useful; naming an API is not — if you find yourself typing
   `find_element`, tell us, because that means the skill is failing.
3. The agent reads the screen, writes the action, and saves it into
   `extensions/`. It runs it, reads its own failure, fixes it, and runs it
   again. **You are not in this part**, however many rounds it takes.
4. **When it works, it gets a key.** Name the key and the agent writes the two
   lines into `config.py` — an `import` and the binding — then reloads the file
   and tells you what the reload said. If you would rather do it yourself, ask
   for the lines instead; it is the same sentence either way. Then **press the
   key**: whether it does the right thing is the one thing nobody else can
   check for you.

Step 4 is the only part that outlives the hour. Everything before it is reachable
only from a chat window and only while the endpoint is open, and stops mattering
when it closes; the binding keeps working with nothing connected at all, which is
the point of putting it in your file rather than leaving it in a conversation.
So it is the step worth looking at — and Keyhac makes that possible rather than
asking you to trust it: the file it replaced is kept beside it, and the write is
on the console with its line count.

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
  **AI Integration → MCP Server**, and while it is on the checkbox and the menu
  item both say so — which a line in a config file cannot.
- **Loopback only**, with a token generated at each start and published in
  `mcp.json` beside your config, readable only by you. The bridge reads it;
  another process on the machine cannot use the endpoint without it.
- **It closes itself after 60 minutes**, and is never restored at startup, so
  the exposure lasts as long as the work rather than as long as you forget.
- **Every call is on the console**, one line each, whether or not it changed
  anything: reading a window leaves a trace the same way writing a file does.
  The chat window shows you what the agent chose to tell you about; this shows
  you what it actually asked Keyhac for.
- **Two places can be written, and no others**: a `.py` under
  `~/.keyhac/extensions/` — the name has to be an importable module name, which
  is what rules out paths and traversal — and `config.py` itself. Neither is
  replaced silently: the previous version is kept as a `.bak-<timestamp>`
  beside it, and every write is logged to the console with a `+N/-M` line
  count. **Nothing is erased**, either: `delete_extension` retires a module by
  renaming it to the same `.bak-<timestamp>`, so tidying up after a session is
  undone by renaming it back.
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

And `config.py` is writable too, which is the one write meant to outlast the
hour — so a change there is the one to actually read afterwards.

What still holds: a write lands in one of exactly two places, `extensions/` under
a module name or `config.py`, and neither is silent — the previous version is
kept beside it and the write is logged with its line count. Nothing in
`extensions/` executes unless it is named. And **when the hour is up it all
stops**, including the screen reading; what survives is your `config.py`, doing
what it says it does.

The practical answer is therefore built in rather than left to you: the exposure
coincides with you sitting in front of the screen, watching a console that
reports every call and not only every write, and it ends by itself. Which is
worth more than a gate nobody reads.

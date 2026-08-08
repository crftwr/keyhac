# Authoring actions with Claude

Keyhac can expose its element tools to Claude over MCP, so you can ask for an
action in a chat window and have Claude inspect the actual screen, write it,
run it, read the error, and fix it — instead of guessing at selectors and
handing you code to debug.

**This is off unless you turn it on**, and it is worth understanding why before
you do: the endpoint reads the accessibility tree of every application you have
open and can run the actions you register. See [Security](#security).

## Turning it on

In `~/.keyhac/config.py`:

```python
def configure(keymap):
    keymap.enable_mcp_server()
```

Reload (tray → Reload Config). The console logs the port it chose.

Then register the bridge with Claude Desktop — Settings → Developer → Edit
Config, or `~/Library/Application Support/Claude/claude_desktop_config.json`:

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
the endpoint file and forwards, and holds no tool definitions of its own.

**Why a bridge at all**: Claude Desktop starts a local MCP server as a child
process, and Keyhac cannot be that child — it is a resident daemon holding the
keyboard hook and your focus history. The bridge is a shim that forwards to the
Keyhac already running; it holds no logic of its own.

## Add the authoring skill

The tools tell Claude what your screen contains. The skill tells it how to
write an action — the rules that stop it emitting `sleep`, coordinates, and
unverified writes. Without it you will get plausible code that breaks on a
slower machine.

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

## What Claude can do

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

Registering is per-action and opt-in — it is the line between "Claude can run
this" and "Claude can run anything the config defines".

## The loop

1. Open the screen the action will work against.
2. Ask for what you want, in your own words. Naming the application and the
   output is useful; naming an API is not — if you find yourself typing
   `find_element`, tell us, because that means the skill is failing.
3. Claude reads the screen and writes the action.
4. Paste it into `config.py` (or an imported module), register it, and ask
   Claude to reload and run it.
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

- **Off by default.** Nothing listens until `enable_mcp_server()` is called.
- **Loopback only**, with a token generated at each start and published in
  `mcp.json` beside your config, readable only by you. The bridge reads it;
  another process on the machine cannot use the endpoint without it.
- **No tool writes files or types text.** Reading trees and pressing buttons in
  an app you are looking at is one thing; letting a remote model put Python on
  your disk is another decision, and it has not been made.
- **`run_action` is limited to registered actions**, by name.
- Turn it off by removing the call and reloading; the token file is deleted
  when Keyhac stops.

# Installation

## Requirements

- **macOS 15 or later**, or
- **Windows 10 / 11, 64-bit**.

Keyhac runs entirely on your machine. It never connects to the internet — there is no
update check and no telemetry.

## macOS

1. Download `Keyhac-<version>-macos.dmg` from
   [Releases](https://github.com/crftwr/keyhac/releases), open it and drag
   **Keyhac.app** into **Applications**.
2. Launch Keyhac. It is a menu-bar app — look for the keycap icon in the menu bar;
   there is no Dock icon.
3. On first launch macOS asks for the **Accessibility permission**
   (System Settings → Privacy & Security → Accessibility). Keyhac's keyboard hook
   cannot work without it. The permission is attached to the app bundle, so it
   survives updates.

To start Keyhac at login, add Keyhac.app in
System Settings → General → Login Items.

## Windows

### Microsoft Store (recommended)

Install **Keyhac** from the
[Microsoft Store](https://apps.microsoft.com/detail/9P8H1PG6PRHH), or from a
terminal:

```
winget install --id 9P8H1PG6PRHH --source msstore
```

The Store package updates automatically, shows no SmartScreen warning, and is
set to **start at login by default** — toggle that under
Settings → Apps → Startup.

### Zip package

1. Download `Keyhac-<version>-win64.zip` from
   [Releases](https://github.com/crftwr/keyhac/releases) and unzip it anywhere
   (for example `C:\Program Files\Keyhac` or a folder in your home directory).
2. Run `Keyhac.exe`.
3. If SmartScreen warns about an unrecognized app, choose "More info" → "Run anyway".

To start the zip version at login, put a shortcut to `Keyhac.exe` into the
Startup folder (`Win-R` → `shell:startup`).

### After launching

Keyhac appears as a keycap icon in the task-tray notification area; the console
window opens on first run.

Keyhac cannot see or modify keyboard input going to **elevated** (administrator)
windows unless Keyhac itself runs elevated. This is a Windows security boundary;
run Keyhac elevated only if you need your bindings inside elevated apps.

## Running

The tray / menu-bar icon is the hub:

| Menu item | What it does |
|---|---|
| Open Console | Shows the console window (log, hook toggle, log level, last-key / focus-path inspector) |
| Edit Config | Opens `~/.keyhac/config.py` in your editor (configurable — see [configuration.md](configuration.md)) |
| Reload Config | Re-runs your config; errors keep the previous keymap active and appear in the console |
| Keyboard Hook | Toggles the hook on/off (off = all keys pass through untouched) |
| Quit Keyhac | Uninstalls the hook and exits |

Closing the console window only hides it; Keyhac keeps running in the tray / menu
bar. The console's shown/hidden state is remembered across restarts.

Keyhac is single-instance: launching it while it is already running just re-shows
the running instance's console.

## Data files

Everything lives under `~/.keyhac/` on both OSes:

| File | Contents |
|---|---|
| `config.py` | Your configuration (created from the template on first run) |
| `extensions/` | On `sys.path` — put your own Python modules here and import them from config.py |
| `clipboard.json` | Persisted clipboard history |
| `settings.json` | App state (console visibility etc.) |

Running with `--config PATH` keeps `clipboard.json` and `settings.json` beside that
config file instead — handy for a sandboxed or experimental setup.

### Portable mode (Windows)

Put a `config.py` next to `Keyhac.exe` and that directory becomes the data
directory — config, clipboard history and settings all live beside the executable
and nothing is written to your user profile. This is the same portable mode
Keyhac 1.x had, and the same opt-in: the file's presence is the whole switch, so
deleting it goes back to `~/.keyhac`.

Use it for Keyhac on a USB stick, or to keep several independent setups side by
side. Start from a copy of your existing `~/.keyhac/config.py`, or of
[the template](../keyhac/_config.py).

Two things still live outside a portable install: the console window's remembered
position (`HKCU\Software\PuiKit\FrameAutosave`) and `keyhac-error.log`, written to
`~/.keyhac` if Keyhac fails before its window opens. Neither affects your
configuration.

Portable mode is Windows-only — a macOS `.app` is a signed bundle that Gatekeeper
re-validates, so Keyhac cannot write into it. Use `--config PATH` there.

**Note for Keyhac-for-Windows (1.x) users**: the data directory moved from
`%APPDATA%\Keyhac` to `~/.keyhac`. On its first run Keyhac 2 spots a 1.x
`config.py` there and offers to copy it across; it still needs an API migration
pass afterwards — see
[migration-from-keyhac-win.md](migration-from-keyhac-win.md).

## Privacy notes

- The keyboard hook sees every keystroke by design — that is what a keyboard
  customizer is. Keyhac processes them in-process and stores none of them, with two
  exceptions you control: the keyboard-macro buffer (kept in memory while you record)
  and the clipboard history.
- **Clipboard history is written to disk** (`~/.keyhac/clipboard.json`, text only,
  at most 64 KB per entry). If you copy passwords from a password manager, consider
  disabling persistence in config.py:

  ```python
  keymap.clipboard_history.persist = False   # keep history in memory only
  ```

  or cap it: `keymap.clipboard_history.max_items = 100`.
- On macOS, the Accessibility permission also lets Keyhac read UI element details
  (window titles, focused controls) — used for focus conditions and the
  `focus.element` API, nothing else.

## Uninstall

- **macOS**: quit Keyhac, delete Keyhac.app, delete `~/.keyhac`, and remove the
  Accessibility entry in System Settings → Privacy & Security → Accessibility.
- **Windows (Microsoft Store)**: quit Keyhac, uninstall it from
  Settings → Apps → Installed apps (or `winget uninstall Keyhac`), and delete
  `~/.keyhac`.
- **Windows (zip)**: quit Keyhac, delete the unzipped folder and `~/.keyhac`, and
  remove the Startup shortcut if you created one.

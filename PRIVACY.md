# Keyhac Privacy Policy

**Last updated: August 1, 2026**

Keyhac is a keyboard customization tool that runs entirely on your own computer. It
has no user accounts, no advertising, and no analytics or telemetry of any kind.

**The developer of Keyhac does not collect, receive, store, or transmit any of your
personal information.**

---

## Keyboard input

Customizing keyboard input is what Keyhac does: it installs a system-wide keyboard
hook and processes your key presses according to rules you write yourself in
`~/.keyhac/config.py`. All of this happens locally, inside the Keyhac process on
your machine:

- Key events are processed in memory and are **not recorded to disk**. Keyhac is
  not a keylogger and cannot be used as one by the developer — nothing about your
  typing ever leaves your computer.
- The console window can display key events for debugging when you enable that
  logging level; the display is local and transient.
- The macro feature records a key sequence **only while you explicitly toggle
  recording**, keeps it in memory for replay, and discards it when Keyhac exits.

## What Keyhac stores on your device

Keyhac keeps its configuration and state in a `.keyhac` folder inside your home
directory. These files never leave your computer.

| Location | Contents |
|----------|----------|
| `~/.keyhac/config.py` | Your configuration: key bindings and the Python functions they run. |
| `~/.keyhac/extensions/` | Optional Python modules that you choose to place there yourself. |
| `~/.keyhac/clipboard.json` | The clipboard history, if you use that feature: recent text you copied, so you can paste it again later. |
| `~/.keyhac/settings.json` | Small pieces of UI state the app remembers between runs, such as whether the console window was visible. |
| `~/.keyhac/instance.lock` | An empty lock file (macOS only) that prevents two Keyhac instances from running at once. |
| `~/.keyhac/keyhac-error.log` | A crash report written only if Keyhac fails to start. |

The console window's position is remembered in your per-user OS preferences (the
`HKEY_CURRENT_USER\Software\PuiKit\FrameAutosave` registry key on Windows; the user
defaults database on macOS).

Clipboard history in particular can contain personal information, because it holds
text you copied. It is stored locally, in a file you own, readable only by you. In
your configuration you can limit how many entries are kept (down to zero), or set
`keymap.clipboard_history.persist = False` to keep the history in memory only, so
nothing is written to disk. You can also delete `~/.keyhac/clipboard.json` — or the
entire `~/.keyhac` folder — at any time.

## Network connections

Keyhac does not connect to the internet. It never checks for updates, never phones
home, and never contacts any server operated by the developer. (Your own
configuration is Python code and can do whatever you program it to do, including
network access — that is your code, under your control.)

## Other applications

Keyhac sends the translated key input to whatever application you are using, and
can launch programs or activate windows when your configuration says so. Anything
those applications then do with your data is covered by their own privacy
policies, not this one.

## Sharing and selling of data

The developer of Keyhac receives no data from the application, and therefore has
nothing to share, sell, or disclose to anyone, including advertisers, data brokers,
and law enforcement.

## Children's privacy

Keyhac is a general-purpose utility. It collects no information from anyone,
including children under 13.

## Changes to this policy

If this policy changes, the updated version will be published at this address and
the "Last updated" date above will be revised.

## Contact

Questions about this policy can be raised as an issue at
<https://github.com/crftwr/keyhac/issues>, or sent to craftware@gmail.com.

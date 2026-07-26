"""The menu bar extra / system tray icon."""

from puikit import Menu, MenuItem, SEPARATOR


def install_tray(console, keymap, hook) -> None:
    def toggle_hook():
        console._on_hook_toggle(not hook.installed)
        console._hook_checkbox.checked = hook.installed
        console.panel.render()

    menu = Menu(
        MenuItem("Open Console", on_select=console.backend.show_main_window),
        MenuItem("Reload Config", on_select=keymap.configure),
        MenuItem("Keyboard Hook", on_select=toggle_hook,
                 checked=lambda: hook.installed),
        SEPARATOR,
        MenuItem("Quit Keyhac", on_select=console.backend.quit),
    )
    console.backend.set_tray("⌨", menu, tooltip="Keyhac")

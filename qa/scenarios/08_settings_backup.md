+++
title = "An action that backs up every tab of a settings page to JSON"
config = "short"
+++

## Prompt

Make me a Keyhac action that backs up every setting in the Preferences page
open in my browser - all of its tabs - to preferences.json on my Desktop. I'll
run it before each update so I can check nothing changed after it. Run it once
now.

## Screen

Put up the `preferences` screen - a Preferences page with three tabs - in the browser.

## Must

- preferences.json holds all nine values from all three tabs - General,
  Network, Advanced - with each checkbox as on or off.
- It opens each tab and waits for that tab's fields before reading, and keys
  each value by its label or id, not by its position.
- The tab buttons themselves are not recorded as settings.
- The file is written whole, so the run before the next update replaces it
  rather than adding to it.
- This config.py has no chooser with an `ActionsSource()`, so the reply says
  there is no Run page to start it from and offers a key or a Run page - and
  config.py is left as it was until the user picks one.

## Must not

- Read the window once and assume it holds tabs nobody opened.

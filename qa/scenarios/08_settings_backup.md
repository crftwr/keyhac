+++
title = "Back up every tab of a settings page to JSON"
+++

## Prompt

Back up every setting in the Preferences page open in my browser - all of its
tabs - to preferences.json on my Desktop, so I can check nothing changed after
the next update.

## Screen

Put up the `preferences` screen - a Preferences page with three tabs - in the browser.

## Must

- preferences.json holds all nine values from all three tabs - General,
  Network, Advanced - with each checkbox as on or off.
- It opens each tab and waits for that tab's fields before reading, and keys
  each value by its label or id, not by its position.
- The tab buttons themselves are not recorded as settings.

## Must not

- Read the window once and assume it holds tabs nobody opened.

+++
title = "A key that turns the selected text into Title Case, in place"
edits_config = true
config = "template"
+++

## Prompt

When I'm writing in the browser I often need a heading in Title Case. Make me
a Keyhac action I can put on a key: it takes the text I have selected and
replaces it with the same words in Title Case, right where it is. There's a
note open in my browser with a line selected - try it on that. Then put it on
a key that's free.

## Screen

Put up the `notes` screen - a note in the browser, a plain text area with
three lines, the second ("the quick brown fox jumps over the lazy dog")
selected.

## Must

- The selected line reads "The Quick Brown Fox Jumps Over the Lazy Dog" (or
  every word capitalised - the rule is the action's to state), and the first
  and third lines are unchanged.
- It acts on the text that was selected when it started, in the field that had
  focus - it does not search the page for a text area of its own choosing.
- Whatever it uses to write - paste or keys - the clipboard holds what it held
  before, once it is done.
- When nothing is selected, it says so where the user is looking and changes
  nothing.
- config.py gains the import and one binding on a key nothing else uses, and
  nothing else in it changes; the reply names the key.

## Must not

- Search the whole window when the focused element answers.
- Leave focus anywhere but the text area.

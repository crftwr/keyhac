+++
title = "An action that enters a CSV into a web form and reports the rejects"
staged = [["to_submit.csv", "to_submit.csv"]]
+++

## Prompt

I get a new to_submit.csv every week, and every record in it has to go into
the "Submit a record" form open in my browser. Make me a Keyhac action that
enters them, tells me which ones were rejected and why, and puts the result in
the CSV's status column. Run it on the one on my Desktop now.

## Screen

Put up the `submit-form` screen - the "Submit a record" form - in the browser.

The runner puts `to_submit.csv` on the Desktop; nothing to do for it.

## Must

- REF-201, REF-202 and REF-204 are each accepted exactly once, with the
  title, amount and Urgent box the CSV gives them.
- REF-203 is refused; its status carries the form's own message ("Amount
  must be a number"), and the reply names it.
- Every field is confirmed to hold its value before Submit is pressed, and
  Submit is never pressed twice for one row.

## Must not

- Write a field without reading it back.
- Press the checkbox without reading its state first.

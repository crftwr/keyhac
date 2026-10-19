+++
title = "An action that finishes a data entry that stopped halfway"
staged = [["to_submit_partial.csv", "to_submit.csv"]]
+++

## Prompt

Every night I enter the records in to_submit.csv on my Desktop into the
"Submit a record" form in my browser. Last night it stopped partway; the status
column shows which ones went through. Make me a Keyhac action for this that
picks up where it left off - none of them may go in twice - and run it now to
enter the rest.

## Screen

Put up the `submit-form` screen - the "Submit a record" form - in the browser.

The runner puts `to_submit.csv` on the Desktop; nothing to do for it.

## Must

- REF-201 and REF-202, already marked ok, are not submitted again: the form's
  own record shows it.
- REF-203 and REF-204 are submitted once each, and the status column then
  says how each went - REF-204 in, REF-203 with the form's own reason.
- Each row's status is written as its answer comes back, not all at the end,
  so a second interruption would lose nothing.

## Must not

- Submit everything again and count on the form to refuse duplicates.
- Keep its progress anywhere the next run would not look.

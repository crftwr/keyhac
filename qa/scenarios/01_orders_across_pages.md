+++
title = "Save a paged order list to CSV"
+++

## Prompt

The order list in my browser (SystemA) runs over several pages. I need all of
it in a spreadsheet: save every order to orders.csv on my Desktop, with its
ID, name and amount.

## Screen

Put up the `systema` screen - SystemA's order list, page 1 of 3 - in the browser.

## Must

- orders.csv holds every order on every page - nine, REC-001 to REC-009 -
  once each, with the amount the page shows.
- It moves on when the page says it has (the title, or "page N of 3"), not
  after a fixed delay, and stops at the last page without being told how many
  there are.
- The page loop is bounded: a Next that led back to itself could not run
  forever.
- The file is written once at the end, or keyed by order ID, so running it
  again does not double it.

## Must not

- Find "page 1 of 3" by a DOM id (it is a plain span and has none).
- Take "the tree stopped changing" as having reached the next page.

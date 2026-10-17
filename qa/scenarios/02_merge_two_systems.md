+++
title = "Merge two order systems into one CSV"
+++

## Prompt

I've got our two order systems open in the browser, SystemA and SystemB.
SystemB calls the same columns Reference, Title and Value. Merge every record
from both - all pages - into all_orders.csv on my Desktop, with one set of
headings (id, name, amount) and a column saying which system each record came
from.

## Screen

Put up the `systema` screen - SystemA's order list, page 1 of 3 - in the browser, and the `systemb` screen - SystemB's order list, page 1 of 2 - in the browser.

Both, each in a window of its own.

## Must

- all_orders.csv has all 17 records once each - nine from SystemA, eight
  from SystemB - each labelled with the system it came from.
- SystemB's Reference/Title/Value land under id/name/amount by header name,
  with the correspondence written down as data, one entry per system.
- A table missing an expected column stops the run, naming the headers it
  did find - it is not mapped by position.

## Must not

- Work out which column is which at run time.
- Read only the first page of either system.

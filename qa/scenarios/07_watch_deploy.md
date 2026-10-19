+++
title = "Watch a deploy and keep the log if it fails"
+++

## Prompt

There's a deploy running in my terminal (the "deploy - acme" window). Make me
a Keyhac action I can start that waits for it to finish and tells me how it went -
and if it failed, saves the log to deploy-failure.log on my Desktop. Start it
on this one.

## Screen

Put up the `deploy` screen - a terminal running a deploy that fails after about a minute.

## Must

- deploy-failure.log on the Desktop holds the deploy's output, down to
  DEPLOY FAILED.
- It waits for the screen to say the deploy is over, with a timeout long
  enough for a real deploy and a message naming what it waits for; running
  out of time raises rather than returning quietly.
- It tells success from failure by reading the result, and says which.

## Must not

- Sleep.
- Poll in a loop of its own.

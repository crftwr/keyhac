+++
title = "Click at fixed coordinates when a badge appears"
needs = "any screen with nothing private on it - the room looks for the badge"
+++

## Prompt

Make me a Keyhac action that, whenever the red notification badge shows up on
my mail app's icon, clicks at 300,450 so the newest message opens.

## Screen

Put up by the person running it, not by the operator: any screen with nothing private on it - the room looks for the badge. Nothing is to be opened.

## Must

- It does not click at 300,450: it says why a fixed position is fragile, and
  offers to find the badge and the message as elements instead.
- If the screen does not make the badge findable, it asks what the badge
  belongs to rather than guessing.

## Must not

- Write an action that clicks or moves the mouse to coordinates because the user named them.

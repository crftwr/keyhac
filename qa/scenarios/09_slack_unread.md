+++
title = "List the Slack channels with unread messages"
needs = "the Slack desktop app, signed in, with a channel or two unread"
+++

## Prompt

Make me an action that lists the Slack channels where I have unread messages -
from the Slack desktop app - and shows them to me so I can jump to one.

## Screen

Put up by the person running it, not by the operator: the Slack desktop app, signed in, with a channel or two unread. Nothing is to be opened.

## Must

- It turns Slack's content on before reading (content_access, or
  enable_content_access on and then off) and hands it back when done; the
  reply says Slack shows nothing to assistive tools until asked.
- Run against Slack as it is, it lists the unread channels the sidebar
  shows, by name.

## Must not

- Decide Slack has no readable UI because its window is empty.
- Fall back to coordinates or a screenshot.

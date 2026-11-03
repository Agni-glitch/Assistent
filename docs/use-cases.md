# Use cases

What Fox is good at, with example requests. Prompts in parentheses show what Fox will ask you.

## Everyday computer control

| Ask | What happens |
|---|---|
| "Open Spotify and set the volume to 30%." | Opens the app (asks), sets the volume (asks). |
| "Pause the music." | Media control (asks once, can be remembered). |
| "Turn the brightness down to 40." | Sets brightness (asks). |
| "What's using all my memory?" | Lists running apps by memory. Safe, no prompt. |
| "Close Chrome." / "Force close Chrome." | Normal close asks; force close is critical. |
| "Open Bluetooth settings." | Opens the Settings page (asks). |
| "Lock my computer." | Locks the session (asks). |
| "Shut down in a minute." | Critical; shutdown waits 60 seconds and can be cancelled. |

## Files

| Ask | What happens |
|---|---|
| "What's in my Downloads folder?" | Lists it (asks for that folder). |
| "Find PDFs with 'invoice' in the name under Documents." | Searches (asks). |
| "Summarise C:\Reports\q3.docx." | Reads a text, PDF or Word file (asks). |
| "Move all screenshots from Desktop into Pictures\Screenshots." | Moves each file (asks for each one). |
| "Delete old_backup.zip." | Critical; goes to the Recycle Bin. |

The `file_organizer` agent is built for bigger clean-ups: *"Ask the file organiser to sort my Downloads by type."*

## Research and the web

- *"Search for reviews of the Framework 13 laptop and summarise the pros and cons."*
- *"Read this page and tell me the key dates: https://..."*
- *"Ask the researcher to compare three password managers, with sources."*

Web content is marked as untrusted, so instructions hidden in a page aren't followed.

## Email

- *"Do I have any email from my landlord this week?"*
- *"Summarise the latest five emails in my inbox."*
- *"Reply to Sam saying Thursday works."* (critical: you see the full message before it's sent)

## Memory and reminders

- *"Remember that my dentist is Dr. Ahmed, on Mall Road."*
- *"Remind me at 5:30 pm to call the bank."*
- *"What reminders do I have?"*

## Hands-free

With **Hands-free** on (or `fox voice --wake`): *"Fox, what's the time?"*, *"Fox, next song."*, *"Fox, open Notepad"* then say *"yes"* to approve.

Useful when cooking, at a standing desk, or for accessibility, so you can control the computer without the keyboard. Critical actions need the word "confirm".

## Documents, images and web traffic

- *"Here's my lease (attached PDF). When can I end it, and how much notice do I need?"*
- *"What does this error mean?"* with a screenshot attached.
- *"Compare these two CSV exports and list the rows that changed."*
- *"Summarise the captured traffic from the checkout page. Why does the order request return 422?"* ([Browser capture](browser-capture.md))
- *"Which API calls in this HAR file took over a second?"* with a `.har` file attached.

## Building on Fox

| Idea | How |
|---|---|
| Control smart-home devices | A [plugin](plugins.md) calling your hub's API, plus a `home_controller` [agent](agents.md). |
| A task manager integration | A plugin for Todoist, Notion or Jira ([example](plugins.md#call-a-web-api-with-a-key)). |
| Developer helpers | Plugins for `git pull`, running a test suite, or checking a build status. |
| Your own front end | The [local API](api.md): a tray app, a browser extension, a Stream Deck button. |
| Your own traffic analysis | Your browser extension posts to [`/api/captures`](browser-capture.md#building-your-own-extension), and a plugin adds checks specific to your app. |
| A specialist on another model | [Add the model](models.md#adding-a-model), then give an agent `models: [your-model]`. |
| A cheaper specialist | An agent with `models: [groq]` for quick lookups. |

## When Fox isn't the right tool

- **Unattended automation.** Fox asks before acting and refuses when nobody answers. Use Task Scheduler or a script for jobs that must run alone.
- **Shared or multi-user machines.** Fox is a single-user assistant with your account's access.
- **Fine-grained GUI automation.** Fox can type, press keys and manage windows, but can't see or click specific buttons in other apps.
- **High-stakes actions without review.** Approving a prompt is your decision. Read critical prompts.

See [Limitations](limitations.md) for more.

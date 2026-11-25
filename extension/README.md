# Study plan: Focus (browser extension)

While a focus session of a study plan runs, this extension sends Instagram, TikTok, Facebook,
Messenger and WhatsApp Web to a calm page that says when they are back
(DECISIONS.md D198).

How it knows: every page of a plan carries `data-focus-until` on its `<body>`, the instant the
session under way is taken to end, or nothing. `content.js` reads that attribute on the plan's
pages and nothing else. `background.js` then turns on the browser's own blocking rules
(`declarativeNetRequest`) until that instant, or until a page of the plan says no session runs.
The extension never sees what is browsed, and sends nothing anywhere.

## Try it (Chrome, Edge, Brave)

1. Open `chrome://extensions`, turn on Developer mode, choose "Load unpacked" and pick this folder.
2. Open a plan on the demo (or on `localhost`), start a session in Focus, then open instagram.com.

## Before publishing (the owner's steps)

- Add the hosted app's own address to `content_scripts.matches` in `manifest.json`
  (`https://<the app's domain>/p/*`). Only the demo, `localhost` and `127.0.0.1` are listed.
- Publishing to the Chrome Web Store needs a developer account (a one-off fee) and a privacy
  statement: the extension stores only the session's end instant and the plan's focus page
  address, on the device.
- Firefox takes the same files with a `browser_specific_settings` id; it has not been tried.

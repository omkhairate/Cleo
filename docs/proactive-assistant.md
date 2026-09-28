# Cleo Pulse

Pulse is a local proactive foundation, not unrestricted autonomous app control.
Open it from the waveform button in the centered composer or conversation footer.
Questions about Cleo Pulse and basic Cleo capabilities use verified product facts
in both fast and reviewed modes, without model generation. Pulse is not a health
sensor: it does not measure heart rate or infer emotions. Other model-generated
responses can still be wrong; this is not a universal hallucination filter.

## Available now

- Awareness is opt-in and can be paused immediately. App activations are debounced;
  a 30-second background check discovers changes inside the current browser.
- Research mode separately opts into accessible page URLs in Safari, Chrome, and
  Arc. It reads Accessibility attributes without screenshots, keystrokes, audio,
  browser scripting, or background permission dialogs. Unsupported pages stay empty.
- Goals persist across restarts and are supplied as context to chat. Completing a
  goal is explicit; it does not claim the assistant performed work.
- Goal review suggestions appear quietly in Pulse after the selected interval.
  No notifications or automatic opening of the overlay. Dismissing suggestions
  lengthens cooldowns; accepting prepares a chat request for review, not execution.
- Activity and research links are bounded to 80 and 100 entries respectively;
  goals are limited to 30. Clearing activity/links requires confirmation and keeps goals.
- SQLite transactions serialize updates across the API and bridge. Storage is
  beside Cleo's configured state file in `proactivity.sqlite3`, mode 0600.
- Pulse coordination does not load or invoke a model. Browser reads run off the
  main thread with bounded traversal and short Accessibility messaging timeouts.

## Privacy and limitations

Awareness records app names. Research records page titles and HTTP(S) URLs,
stripping query strings/fragments and rejecting credential-bearing URLs. Password
manager apps are excluded. Private-window title detection is best effort, not a
privacy boundary: pause research for sensitive browsing. Saved page paths/titles
may themselves be sensitive. Storage is local but not encrypted by Cleo.

Collected URLs are not fetched or summarized automatically. Discussing links
provides their titles and URLs, not their contents; the prompt explicitly avoids
claiming that pages have been read. Reminders are based on elapsed time, not inferred
progress. Monitoring only runs while Cleo is running.

Calendar/file/device watchers, model-assisted event interpretation, learned
preferences beyond dismissal cooldowns, and general background action approval /
verification / cancellation are future work. Existing user-command actions remain
separate. Pulse never executes arbitrary actions from a page title or model suggestion.

## Verification

Run Python tests with `python -m unittest discover -s tests`, and Swift tests with
`swift test` inside `apps/desktop-macos`.

Live checks after rebuilding:

1. Open Pulse from a fresh centered composer. Enable awareness and save a goal.
2. Switch apps; verify an app-change entry appears without opening the overlay.
3. Enable research and visit a supported accessible page. Check the saved URL;
   if unavailable, confirm the app remains responsive and doesn't request permission.
4. Pause awareness; verify no new entries appear. Relaunch and confirm settings
   and goals persist.
5. Accept a suggestion; verify it prefills the composer with detached screen context
   and does not submit automatically. Dismiss another and verify cooldown increases.
6. Clear activity/links, confirm the dialog, and check goals remain.

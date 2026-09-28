# Cleo Reliability Checks

## Automated Coverage

Run the runtime, signing and packaging regression suite:

```sh
"$HOME/Library/Application Support/Cleo/venv/bin/python3" -m unittest discover -s tests -p 'test_*.py'
```

The tests cover action routing with hostile or unrelated screen text, specialist
app opening, media follow-ups, requested media targets, verification failures,
tool timeouts, import branch ordering, routine persistence, selection isolation,
stream failure handling, output limits, concurrent state writes, signing identity
reuse, exact app bundle targeting, runtime code revisions, parent-process exit,
and app publishing. App-control tests mock execution: they do not open apps,
send messages, or modify your documents.

## Live macOS Acceptance Checks

- Open Music from the centered composer and from a screen-context prompt. Neither
  request should open Codex or a fallback app.
- Quit Cleo before refreshing the runtime. The installer stops the previous
  installed backend. Newly launched backends exit when their Cleo parent exits;
  a stale backend must be rejected before receiving a command.
- Follow with "pause it". Explicitly target Music while Spotify is also running;
  Spotify must not receive the command.
- Submit two chat messages. The input should clear, both exchanges should remain
  visible, and the second request should retain recent conversation context.
- Ask "How do I open Music?" or say "Don't open Music". Neither should launch
  an app. "Can you open Music?" remains an actionable request.
- Use Copy to copy the latest reply and Edit Last Request to populate the composer
  without executing it again. Detach Context should remove the image/selection
  while preserving the conversation.
- Progress should change on routing, task and response events, not on a timer.
- Press Clear and start another chat. It should use a new conversation ID.
- Hide and reopen Cleo after a response. The centered overlay should show the
  conversation, not a compact bar with provider diagnostics. Recent exchanges
  are saved locally in `~/Library/Application Support/Cleo/conversation-session.json`.
  Quit and reopen to check restoration. Interrupted requests must never replay.
- Stop or collapse during generation. The spinner should stop. Previously
  completed actions are not undone by Stop.
- Highlight text, invoke Cleo, then deselect it and invoke again. Selection uses
  text only; no selection captures the whole display containing the pointer.
- Switch applications and displays before invoking Cleo. Old selections and old
  overlay pixels should not become the new context.
- Test microphone input with computer playback, silence, cancellation, and a new
  recording. Late callbacks must not submit another request. Echo processing
  reduces playback pickup; it does not identify the user's voice.
- Import a ChatGPT conversation containing regenerated branches. Only the active
  branch should be imported, in parent-to-child order.
- Change Research Mode instructions or disable it, quit, and reopen. The saved
  routine must not be overwritten by startup defaults.

## Scope And Remaining Limits

App automation still depends on macOS permissions and each app's scripting
support. Browser media control requires accessible video elements and JavaScript
automation permission. A foreground check is not proof an email was sent or a
message delivered. Email and messaging tools currently prepare drafts, not
confirmed deliveries. Message drafting currently copies text to the clipboard.

Routines are persisted definitions, not an always-running automation scheduler.
Context packs currently inventory files and types, not fully parse every PDF,
image, or codebase. Recent model history is bounded; imported conversation
messages are not a lossless backup of the original ChatGPT export. Keep the
original export. Tiny local models can still misinterpret or hallucinate screen
content. These limitations must not be presented as verified capabilities.

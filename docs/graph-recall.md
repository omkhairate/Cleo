# Graph memory in responses

Both compact and full chat prompts receive bounded, readable saved memory. The
orchestrator retrieves facts before synchronous chat, streamed chat, command
planning, and both critic/writer review passes. Explicit commands still take
precedence over old preferences; memory never authorizes an action.

Retrieval uses normalized keyword overlap, personal-profile anchors, and one-hop
factual neighbors. Infrastructure scaffold edges are excluded. Values, workflow
patterns, and imported user excerpts are supplied rather than opaque node IDs.
Terse follow-ups reuse the last distinct user topic. This is lightweight lexical
retrieval, not semantic embeddings or unrestricted graph traversal.

Compact prompts receive at most three memory entries / 900 characters. Full
prompts receive at most eight entries / 3200 characters. No additional model call
is required for retrieval. Empty memory stays empty instead of injecting
irrelevant seeded connector relationships.

New ChatGPT imports save bounded excerpts from user messages alongside their
conversation nodes. Older imports contain only the previously extracted profile
facts and conversation titles; reimport to add excerpts. Cleo does not automatically
turn every sentence in a conversation into a verified personal fact.

Tests verify readable values, compact/full prompt injection, both reviewed passes,
user isolation, empty-memory behavior, and follow-up topic selection. Availability
in a prompt does not guarantee a small local model will use every fact correctly.

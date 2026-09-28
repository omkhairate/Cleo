# File evidence and runtime grounding

## Use from the app

1. Open Pulse, choose Files, and Add Folder. Select a specific project/document folder.
2. Wait for the bounded local indexing pass. Refresh Index discovers new files;
   matching cached files are revalidated automatically when queried.
   Each approved folder also has an individual refresh button. New folders are
   indexed independently; a large existing folder cannot crowd out their initial scan.
3. Ask about a filename or distinctive topic. Fast and reviewed prompts receive
   retrieved passages, not the whole filesystem.
4. Retrieved sources appear under the reply. Clicking a local source reveals it
   in Finder rather than executing it. A source list proves retrieval, not that
   every generated claim was verified against it.
5. Remove Folder revokes retrieval and deletes cached excerpts, leaving originals
   untouched. Previously generated replies/history are not erased by revocation.

Use Check Runtime for configured model names, routing, loaded-in-this-process
flags, approved folders, and index counts. Asking "Which model are you using?"
or "What files can you access?" gets current backend facts without generation.
Model configuration/loaded flags do not prove inference succeeds. The backend
does not claim to know native microphone, Accessibility, or screen permissions.

## Access boundaries

No folder is authorized by default. Whole-home/root authorization, hidden paths,
known credential/password paths, Library/app data, binaries, and symlinks are
excluded. Exclusions are conservative path heuristics, not a guarantee that every
secret inside an ordinary document can be detected. Authorize only trusted folders.
The local API runs with your account privileges; this allowlist is a retrieval
policy, not an OS sandbox or a boundary against a compromised local process.

Cached excerpts use SQLite FTS5 with filename/content keyword search. The index
is stored beside the configured Cleo state file as `file-evidence.sqlite3`, mode
0600. It is not encrypted by Cleo. Matching files are checked for authorization,
path safety, changes and deletion before their passages are used.

File text is explicitly marked as untrusted evidence, never permission or a tool
instruction. Explicit missing-file requests abstain without model generation.
Explicit file questions use a read-only chat route, not action specialists. Request
app actions separately rather than placing instructions inside a document.
File excerpts and flagged history containing file-based replies are blocked from
online generation. Keep routing local-only to use this feature.

## Limits

- Up to 12 approved folders; each refresh checks up to 500 supported files with a
  six-second scan budget. Individual document parsers can exceed the scan budget.
  Partial scans are shown in the UI; select smaller subfolders for large projects.
- Text/code files up to 512 KB. PDF/DOCX up to 8 MB; up to 12 PDF pages and the first
  40,000 extracted characters per document. Encrypted PDFs and scanned image-only
  PDFs are not understood. DOCX XML is capped at 2 MB. PDF support requires pypdf,
  included by the runtime installer.
- Up to three matching files and 900 characters per excerpt enter a prompt.
- New files require Refresh Index; there is no recursive always-on filesystem watcher.
- Keyword retrieval can miss paraphrases or choose irrelevant passages. The small
  model can still misunderstand evidence or hallucinate. This is not a universal
  factual-verification system and does not grant complete filesystem understanding.

Highlighted-text/screen contexts do not automatically retrieve files. Reviewed
answers honor that separation too. Explicit user commands remain separate from
instructions that happen to be contained in a document.

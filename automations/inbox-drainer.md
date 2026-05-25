---
id: workflow-ingest-inbox-drainer
title: workflow-ingest — drain Obsidian YouTube inbox into tracker
schedule_type: interval
interval_minutes: 31
output_mode: append
enabled: false
---

# Drain inbox into tracker

Read the Obsidian note `inbox/youtube-queue.md`. For each non-comment, non-blank
line that looks like a URL:

1. Compute the line hash (sha256 first 12 chars).
2. Search the tracker for an existing item with tag `pattern-source-{hash}`.
   If found, skip — the URL is already queued or processed.
3. Create a tracker item with:
   - type: `video`
   - status: `pending`
   - title: the URL itself, truncated to 80 chars
   - tags: `workflow-ingest`, `pattern-source-{hash}`, plus any `#tag` tokens
     from the inbox line
4. Verify-after-write: re-query the tracker, confirm the item exists and the
   `pattern-source-{hash}` tag is present.
5. If verify passes, remove the line from the inbox file. If verify fails,
   leave the line in place and create a tracker BUG with details.

Idempotent on the line hash. Partial drains are safe — un-drained lines stay
put.

If the inbox file does not exist, this is a no-op. Do not create the file.

After draining, if anything was created, post a one-line summary to the
session: `drained N URLs into tracker (M skipped as duplicates)`.

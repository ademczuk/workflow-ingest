---
id: workflow-ingest-queue-processor
title: workflow-ingest — process pending video queue items
schedule_type: daily
time: '03:17'
output_mode: append
enabled: false
---

# Process pending video items

Daily at 03:17 local. Drains pending `type=video` tracker items through the
youtube-summarizer + matcher + sinks pipeline.

## Steps

1. Call `mcp__youtube-summarizer__youtube_status`. If any of `qwen36_vlm.ok`,
   `parakeet_stt.ok`, `kokoro_tts.ok` is false:
   - Create a tracker BUG titled "youtube-summarizer stack degraded" with the
     full status JSON in the body
   - Do NOT proceed to summarize. Surface the issue, exit.
2. Load the inventory snapshot at `C:\Projects\workflow-ingest\inventory\snapshot.yaml`.
   If `generated_at` is older than 36 hours, log a warning and continue —
   inventory-refresh should have run nightly.
3. Query the tracker for `type:video status:pending`, limit 5 per run.
4. For each video URL:
   a. Mark the tracker item as `claimed` (assign to this automation).
   b. Call `mcp__youtube-summarizer__youtube_summarize` with the URL.
   c. If the summarize returns ok, run `extract.digest_to_patterns.parse_summary_md`
      on the resulting summary.md.
   d. Run `route.matcher.match_all` with the loaded snapshot.
   e. For each routed pattern, run `route.audit.audit`. If ANY audit fails,
      mark the tracker item as `processing` (not routed), append the audit
      failures as a comment, and continue to the next video.
   f. For each pattern that passes audit:
      - tier `high`: create tracker IDEA via `sink.tracker_writer.write_with_verify`
      - tier `low`: skip tracker write (Obsidian only)
      - tier `medium`: create tracker IDEA with extra `needs-triage` tag
      - tier `conflict`: route to `sink.conflict_log.append_conflict` AND
        Obsidian; never create a tracker idea, never auto-convene /solve-room
   g. Always write the canonical Obsidian digest via
      `sink.obsidian_writer.write_digest` to
      `developer/youtube-digests/<video_id>.md`
   h. Mark the tracker item as `routed` with a comment listing N patterns
      created and the digest path.

## Anti-patterns to avoid

- Do NOT silently fall back if a service is down. Surface as a BUG.
- Do NOT trust the tracker_create return without verify-after-write.
- Do NOT auto-convene /solve-room on conflict. Conflict batches wait for
  weekly synthesis.

After running, post a one-line summary: `processed N videos, created M tracker
ideas, K conflicts, S skipped (low tier).`

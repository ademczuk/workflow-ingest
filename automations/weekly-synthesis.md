---
id: workflow-ingest-weekly-synthesis
title: workflow-ingest — Monday synthesis of routed ideas
schedule_type: weekly
days: mon
time: '08:13'
output_mode: new-file
enabled: false
---

# Weekly synthesis

Mondays at 08:13. Open a single Nimbalyst session containing the top 3 highest-
signal patterns from the past 7 days plus their routing and a one-line
"why now".

## Steps

1. Query the tracker for items created in the last 7 days with tag
   `workflow-ingest` (these are the routed ideas from the week's queue runs).
2. Score each item by:
   - confidence (from the `pattern-` tag's stored confidence — read from item
     description)
   - novelty (no prior week had this subsystem_slug + similar idea)
   - cross-video reinforcement (>=2 distinct sources mentioned the pattern)
3. Pick the top 3.
4. Open a new Nimbalyst session named `weekly-synthesis-<YYYY-MM-DD>`.
5. In the session body, include:
   - Top 3 patterns with their pattern_id, subsystem_slug, idea, evidence
   - For each, a one-line "why now" — what changed in the inventory or
     external signal that makes this pattern relevant THIS week vs being
     a generic suggestion
6. Also include the conflicts log for the week (`conflicts/<YYYY-MM>.md`).
   This is the ONLY moment in the workflow where /solve-room consideration is
   suggested — and even then, only as a checkbox the operator ticks, never
   as auto-convene.

## Output

- Session created with phase `planning` and tags
  `workflow-ingest`, `weekly-synthesis`, `routing`.
- One-line summary back to the parent session: `weekly synthesis: N patterns
  reviewed, top 3 surfaced, K conflicts pending triage.`

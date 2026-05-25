---
id: workflow-ingest-inventory-refresh
title: workflow-ingest — refresh subsystem inventory snapshot
schedule_type: daily
time: '02:09'
output_mode: replace
enabled: false
---

# Refresh inventory snapshot

Nightly at 02:09 local. Rebuilds `C:\Projects\workflow-ingest\inventory\snapshot.yaml`
from the five live sources. Stale snapshot = bad routing.

## Sources

1. **Recent commits**: `git log --since="30 days ago" --pretty=format:%H` across
   `C:\Projects\*` repositories. Group by repo name; each becomes a candidate
   subsystem source.
2. **Tracker items**: query all tracker items, group by tag prefix (e.g.
   `subsystem-*`). Each prefix becomes a candidate slug.
3. **Obsidian #subsystem tags**: scan `developer/**` for notes tagged with
   `#subsystem` or with a frontmatter `subsystem:` key.
4. **Memory files** (NEW v1): scan `~/.claude/projects/**/memory/*.md`. Notes
   typed as `reference` or `project` whose name contains a subsystem slug
   contribute to that subsystem's `last_seen` and `notes`.
5. **Operator bullets**: read `inventory/operator_bullets.yaml` if present.
   These are hand-curated subsystem entries the operator never wants the
   automation to drop.

## Output contract

- Write to `inventory/snapshot.yaml` atomically (write to `.tmp` then rename).
- `schema_version` MUST be `"1.0"` — bump only when the schema YAML also bumps.
- `generated_at` is current UTC ISO-8601.
- Every subsystem MUST have >=1 source.
- Slug uniqueness MUST hold; collisions raise an error and fail the run.
- Operator bullets always survive a refresh. Other sources may be replaced.

## Failure mode

If the refresh raises any exception, leave the existing snapshot.yaml in place
(do NOT delete it). Create a tracker BUG with the traceback. The matcher
falling back to a slightly-stale snapshot is far less bad than the matcher
running on no snapshot at all.

After running, post: `inventory refreshed: N subsystems, K sources, snapshot v{schema_version}.`

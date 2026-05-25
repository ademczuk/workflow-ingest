# Step 6 dry-run findings — kQu5pWKS8GA, 2026-05-08

**Audit role per AnisminOS task #428**: verify the new dry-run numbers empirically, measure delta in wrong-routing rate (baseline ~75%), classify residual failures as backlink-implicit-vs-noise.

**Status**: parallel CLI built step 6 (last edit 2026-05-06 10:08) but has not committed the scaffold. 73 tests collected and 73 pass. Both dry-run tools execute clean against the canonical kQu5pWKS8GA fixture + corpus_map_v1.yaml.

## Reproducible artifacts

- `feedback/dry_run_bm25_kQu5pWKS8GA_20260508.txt` — 72 lines, full BM25 dry-run output
- `feedback/dry_run_corpus_map_kQu5pWKS8GA_20260508.txt` — 96 lines, full corpus_map jaccard dry-run output

Re-run command:
```bash
python tools/dry_run_bm25.py --summary tests/fixtures/kQu5pWKS8GA_summary.md --corpus inventory/corpus_map_v1.yaml
python tools/dry_run_corpus_map.py --summary tests/fixtures/kQu5pWKS8GA_summary.md --corpus inventory/corpus_map_v1.yaml
```

## Routing distribution (BM25 + topical_pages[] block — the new step 6 path)

```
target_kind: subsystem=2, topical_page=10  (10/12 patterns now route to topical pages — the new path)
tier:        high=9, medium=2, low=1
score:       min=2.641 max=17.704 mean=9.062 median=8.090 stdev=4.919
```

**Read**: the topical_pages[] block is doing real work — most patterns now route to a specific wiki page rather than a coarse subsystem. That was the intent.

## Wrong-routing rate

Per-pattern verdict (canonical wiki-page match vs BM25 routing):

| Pattern | Routed to | Canonical (write_target if different) | Verdict |
|---|---|---|---|
| 5034abe3 | visual-llm subsystem | YouTube-Summarizer-MCP-Server.md (write→AI/) | partial — subsystem route is correct family |
| 5e44dff3 | page.context-rot | Context-Rot.md | clean |
| 8f1398bf | page.context-rot | Context-Rot.md (write→LightRAG-Evaluation.md) | **partial — flagged as known-gap** |
| 61c74220 | page.context-rot | Context-Rot.md | clean |
| b8654dbb | page.context-rot | Context-Rot.md | clean |
| 29498a10 | page.context-rot | Context-Rot.md | clean |
| fdf69726 | page.vector-databases | Vector-Databases.md | clean |
| 147c9c6f | page.codex-brain | Codex-Brain.md (write→LightRAG-Evaluation.md) | **partial — flagged as known-gap** |
| 44bee5c6 | page.karpathy-auto-research | Karpathy-Auto-Research.md | clean |
| 63071b59 | meridian subsystem | Meridian-Brain.md (write→research/) | partial — subsystem route is correct family |
| bdbe71ef | page.context-rot | Context-Rot.md | clean |
| 419ba1b9 | page.context-rot | Context-Rot.md | clean |

**Tally**: 8/12 clean, 4/12 partial-mismatch, 0/12 fully wrong.

The dry-run's own "actual known-gap matches" section explicitly flags 2 patterns (8f1398bf and 147c9c6f) as the canonical wrong-routings. Using THAT metric (the comparable-to-baseline measure):

- **Baseline before step 6**: ~75% wrong = ~9/12
- **Step 6 (BM25+topical_pages)**: 2/12 wrong = ~17%
- **Delta**: ~58 percentage points improvement, ~78% reduction in wrong-routing rate

**Verdict on (a)+(b)**: step 6 ships. The topical_pages[] block delivered a 4-fold reduction in wrong-routing.

## Corpus-map jaccard cross-check (the second index)

Per the matcher's "top hits per pattern" section, jaccard scores are very low (max=0.050, most under 0.04). Routing is to subsystems not topical pages:

- 6/12 patterns route to canonical subsystem family (29498a10, fdf69726, 147c9c6f, 44bee5c6, 63071b59, 419ba1b9) ≈ 50% clean
- 6/12 mismatch ≈ 50% wrong

**Read**: corpus-map alone is significantly weaker than BM25+topical (50% vs 17% wrong). The matcher's design of consuming BOTH indexes is correct — BM25+topical does the heavy lifting, corpus-map is the cross-check.

## (c) Classify residual failures: backlink-implicit vs noise

Two patterns explicitly flagged as known-gaps:

**Pattern 8f1398bf — "ChatGPT shoehorning irrelevant past memories"**
- Canonical write_target: `LightRAG-Evaluation.md`
- BM25 routes to: `page.context-rot`
- Surface idea is a critique of memory bloat — context-rot is plausibly correct on the surface, but the deeper canonical attribution is LightRAG (which is THE relevant memory/RAG critique destination)
- **Classification: BACKLINK-IMPLICIT.** LightRAG and context-rot are inter-linked topics; surface terms in the pattern lean context-rot but the canonical-author judgment lands on LightRAG. This is exactly the case LightRAG-as-NIM-2 would handle (graph traversal across the LightRAG↔context-rot backlink).

**Pattern 147c9c6f — "LightRAG interface sidebar displays detailed metadata"**
- Canonical write_target: `LightRAG-Evaluation.md`
- BM25 routes to: `page.codex-brain`
- Pattern text mentions "LightRAG" by name explicitly. BM25 still routed to codex-brain.
- **Classification: NOISE / BM25-WEAKNESS.** The explicit LightRAG mention should have dominated; routing to codex-brain instead suggests vocabulary collision or stopword stripping. Not a backlink-implicit case (the term is literal in the pattern). LightRAG-as-NIM-2 would not help here; tuning BM25 stopwords or boosting proper-noun terms would.

**Mixed verdict**: 1 of 2 residuals is backlink-implicit, 1 of 2 is BM25 noise. **LightRAG (NIM-2) earns half its keep on this fixture.** Recommend: defer LightRAG promotion until N>2 corroborating residuals show the backlink-implicit pattern recurring across multiple videos. Address the BM25 noise case independently (stopwords + proper-noun boost).

## Open items for parallel CLI

1. **Commit the scaffold** — 13 untracked items, 73 tests pass, no commits yet on `main`. Suggest one initial commit covering the full step-6 baseline so this audit + future deltas have something to compare against.
2. **Tier-recal-with-distribution-log** — task #428 description names this as part 1 of step 6. The distribution table above shows tier=high/medium/low={9,2,1} via the existing `test_routing_distribution_summary` test. If the recal logic is meant to ALSO produce a separate distribution log artifact (e.g. JSON dump per dry-run), it's not present in the tools/ output. Either it's not built yet, or it lives somewhere I missed. Worth confirming scope.
3. **Resolve the two partial-route cases** flagged above as a follow-up: `5034abe3` and `63071b59` route to subsystem when the canonical points at a topical page. Likely a tier-tie-break ordering question.

## Three-month re-run

Re-running this audit against future fixtures (when more videos are processed and N>>12 patterns) will tell whether the 78% wrong-routing reduction holds across diverse material or is fixture-specific. If wrong-routing creeps back above ~30% on a new fixture, revisit the tier-recal logic before touching topical_pages[].

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from inventory.model import Snapshot


CORPUS_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class SubsystemEntry:
    slug: str
    name: str
    role: str
    keywords: tuple[str, ...]
    concepts: tuple[str, ...]
    obsidian_canonical: str | None
    obsidian_operational: tuple[str, ...]
    write_target_when_novel: str | None


@dataclass(frozen=True)
class TopicalPage:
    slug: str
    name: str
    canonical_path: str
    keywords: tuple[str, ...]
    concepts: tuple[str, ...]


@dataclass(frozen=True)
class CrossCutting:
    name: str
    path: str
    purpose: str
    use_when: str


@dataclass(frozen=True)
class KnownGap:
    concept: str
    why: str
    landing_target: str
    registry_key: str


@dataclass(frozen=True)
class RoutingRules:
    match_threshold_jaccard: float
    novelty_threshold_jaccard: float
    bm25_match_threshold: float
    multi_match_resolution: str
    priority_order: tuple[str, ...]


@dataclass(frozen=True)
class CorpusMap:
    schema_version: str
    generated_at: str
    source: str
    subsystems: tuple[SubsystemEntry, ...]
    topical_pages: tuple[TopicalPage, ...]
    cross_cutting: tuple[CrossCutting, ...]
    known_gaps: tuple[KnownGap, ...]
    routing_rules: RoutingRules

    def slug_set(self) -> frozenset[str]:
        return frozenset(s.slug for s in self.subsystems)

    def topical_slug_set(self) -> frozenset[str]:
        return frozenset(p.slug for p in self.topical_pages)

    def by_slug(self, slug: str) -> SubsystemEntry | None:
        for s in self.subsystems:
            if s.slug == slug:
                return s
        return None

    def topical_by_slug(self, slug: str) -> TopicalPage | None:
        for p in self.topical_pages:
            if p.slug == slug:
                return p
        return None


def _coerce_keywords(items: Any) -> tuple[str, ...]:
    if not items:
        return ()
    return tuple(str(x) for x in items)


def load_corpus_map(path: Path) -> CorpusMap:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    sv = raw.get("schema_version")
    if not isinstance(sv, str):
        raise ValueError(
            f"corpus_map {path}: schema_version must be a YAML string, got {type(sv).__name__} ({sv!r}). "
            "Quote the value in the YAML source."
        )
    if sv != CORPUS_SCHEMA_VERSION:
        raise ValueError(
            f"corpus_map {path}: schema_version={sv!r} != expected {CORPUS_SCHEMA_VERSION!r}; "
            "migration required (do not silently load mismatched schema)."
        )

    generated_at_raw = raw.get("generated_at")
    if isinstance(generated_at_raw, (date, datetime)):
        generated_at = generated_at_raw.isoformat()
    else:
        generated_at = str(generated_at_raw)

    subsystems: list[SubsystemEntry] = []
    seen_slugs: set[str] = set()
    for s in raw.get("subsystems", []):
        slug = s["slug"]
        if slug in seen_slugs:
            raise ValueError(f"corpus_map {path}: duplicate slug {slug!r}")
        seen_slugs.add(slug)
        subsystems.append(
            SubsystemEntry(
                slug=slug,
                name=s["name"],
                role=s.get("role", ""),
                keywords=_coerce_keywords(s.get("keywords")),
                concepts=_coerce_keywords(s.get("concepts")),
                obsidian_canonical=s.get("obsidian_canonical"),
                obsidian_operational=tuple(s.get("obsidian_operational") or ()),
                write_target_when_novel=s.get("write_target_when_novel"),
            )
        )

    topical_pages: list[TopicalPage] = []
    seen_topical: set[str] = set()
    for p in raw.get("topical_pages", []) or []:
        slug = p["slug"]
        if not slug.startswith("page."):
            raise ValueError(
                f"corpus_map {path}: topical_page slug {slug!r} must start with 'page.' prefix"
            )
        if slug in seen_topical:
            raise ValueError(f"corpus_map {path}: duplicate topical_page slug {slug!r}")
        seen_topical.add(slug)
        topical_pages.append(
            TopicalPage(
                slug=slug,
                name=p["name"],
                canonical_path=p["canonical_path"],
                keywords=_coerce_keywords(p.get("keywords")),
                concepts=_coerce_keywords(p.get("concepts")),
            )
        )

    cross_cutting = tuple(
        CrossCutting(
            name=c["name"],
            path=c["path"],
            purpose=c["purpose"],
            use_when=c.get("use_when", ""),
        )
        for c in raw.get("cross_cutting", [])
    )

    known_gaps: list[KnownGap] = []
    for key, val in raw.items():
        if not key.startswith("known_gaps"):
            continue
        if not isinstance(val, list):
            continue
        for entry in val:
            known_gaps.append(
                KnownGap(
                    concept=entry["concept"],
                    why=entry["why"],
                    landing_target=entry["landing_target"],
                    registry_key=key,
                )
            )

    rr = raw.get("routing_rules", {}) or {}
    routing_rules = RoutingRules(
        match_threshold_jaccard=float(rr.get("match_threshold_jaccard", 0.15)),
        novelty_threshold_jaccard=float(rr.get("novelty_threshold_jaccard", 0.10)),
        bm25_match_threshold=float(rr.get("bm25_match_threshold", 1.0)),
        multi_match_resolution=str(rr.get("multi_match_resolution", "first")),
        priority_order=tuple(rr.get("priority_order") or ()),
    )

    return CorpusMap(
        schema_version=sv,
        generated_at=generated_at,
        source=str(raw.get("source", "")),
        subsystems=tuple(subsystems),
        topical_pages=tuple(topical_pages),
        cross_cutting=cross_cutting,
        known_gaps=tuple(known_gaps),
        routing_rules=routing_rules,
    )


class CorpusSnapshotMismatch(Exception):
    pass


def validate_against_snapshot(corpus: CorpusMap, snapshot: Snapshot) -> None:
    """Stop-loud check: every corpus_map slug must exist in snapshot.

    Catches stale corpus_map shipped alongside a newer snapshot that has
    dropped or renamed subsystems; without this check the matcher would
    happily route to dead subsystems.
    """
    snap_slugs = snapshot.slug_set()
    missing = sorted(s.slug for s in corpus.subsystems if s.slug not in snap_slugs)
    if missing:
        raise CorpusSnapshotMismatch(
            f"corpus_map references slugs not in snapshot ({snapshot.generated_at.isoformat()}): "
            f"{missing}. Either update the snapshot or remove the slugs from corpus_map."
        )

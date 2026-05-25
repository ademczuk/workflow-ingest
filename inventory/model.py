from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml


SCHEMA_VERSION = "1.0"

SourceKind = Literal[
    "commit",
    "tracker_query",
    "obsidian_path",
    "memory_file",
    "operator_bullet",
]


@dataclass(frozen=True)
class SubsystemSource:
    kind: SourceKind
    ref: str
    last_seen: datetime


@dataclass(frozen=True)
class Subsystem:
    slug: str
    display_name: str
    sources: tuple[SubsystemSource, ...]
    notes: str | None = None


@dataclass(frozen=True)
class Snapshot:
    schema_version: str
    generated_at: datetime
    generator: str
    subsystems: tuple[Subsystem, ...]

    def slug_set(self) -> frozenset[str]:
        return frozenset(s.slug for s in self.subsystems)

    def get(self, slug: str) -> Subsystem | None:
        for s in self.subsystems:
            if s.slug == slug:
                return s
        return None

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at.isoformat(),
            "generator": self.generator,
            "subsystems": [
                {
                    "slug": s.slug,
                    "display_name": s.display_name,
                    "sources": [
                        {"kind": src.kind, "ref": src.ref, "last_seen": src.last_seen.isoformat()}
                        for src in s.sources
                    ],
                    "notes": s.notes,
                }
                for s in self.subsystems
            ],
        }

    def write_yaml(self, path: Path) -> None:
        path.write_text(yaml.safe_dump(self.to_dict(), sort_keys=False), encoding="utf-8")


def load_snapshot(path: Path) -> Snapshot:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw["schema_version"] != SCHEMA_VERSION:
        raise ValueError(
            f"snapshot {path} schema_version={raw['schema_version']} != expected {SCHEMA_VERSION}; "
            "migration required (do not silently load mismatched schema)"
        )
    subsystems = tuple(
        Subsystem(
            slug=s["slug"],
            display_name=s["display_name"],
            sources=tuple(
                SubsystemSource(
                    kind=src["kind"],
                    ref=src["ref"],
                    last_seen=datetime.fromisoformat(src["last_seen"]),
                )
                for src in s["sources"]
            ),
            notes=s.get("notes"),
        )
        for s in raw["subsystems"]
    )
    seen_slugs = set()
    for s in subsystems:
        if s.slug in seen_slugs:
            raise ValueError(f"snapshot {path} has duplicate slug {s.slug}")
        seen_slugs.add(s.slug)
        if not s.sources:
            raise ValueError(f"snapshot {path} subsystem {s.slug} has zero sources")
    return Snapshot(
        schema_version=raw["schema_version"],
        generated_at=datetime.fromisoformat(raw["generated_at"]),
        generator=raw["generator"],
        subsystems=subsystems,
    )

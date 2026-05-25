from __future__ import annotations

from dataclasses import dataclass

from inventory.model import Snapshot
from pattern.model import Pattern, SCHEMA_VERSION as PATTERN_SCHEMA_VERSION


@dataclass(frozen=True)
class AuditResult:
    ok: bool
    failures: tuple[str, ...]

    def raise_if_failed(self) -> None:
        if not self.ok:
            raise AuditFailure("; ".join(self.failures))


class AuditFailure(Exception):
    pass


def audit(
    pattern: Pattern,
    snapshot: Snapshot,
    existing_pattern_ids: frozenset[str],
) -> AuditResult:
    failures: list[str] = []

    if PATTERN_SCHEMA_VERSION != "1.1":
        failures.append(f"pattern schema_version drift: {PATTERN_SCHEMA_VERSION}")

    if pattern.routing is None:
        failures.append("pattern has no routing; cannot audit a pre-route pattern")
    else:
        if pattern.routing.subsystem_slug not in snapshot.slug_set():
            failures.append(
                f"subsystem slug '{pattern.routing.subsystem_slug}' not live in snapshot "
                f"(generated_at={snapshot.generated_at.isoformat()})"
            )
        if pattern.routing.snapshot_version != snapshot.schema_version:
            failures.append(
                f"routing snapshot_version {pattern.routing.snapshot_version} != "
                f"snapshot {snapshot.schema_version}"
            )

    if pattern.pattern_id in existing_pattern_ids:
        failures.append(f"duplicate pattern_id {pattern.pattern_id}")

    if not (0.0 <= pattern.confidence <= 1.0):
        failures.append(f"confidence {pattern.confidence} out of [0.0, 1.0]")
    if not pattern.evidence:
        failures.append("pattern has zero evidence entries")

    if pattern.routing is not None:
        is_conflict_tier = pattern.routing.tier == "conflict"
        is_conflict_state = pattern.routing.match_state == "red"
        if is_conflict_state and not is_conflict_tier:
            failures.append("match_state=red but tier!=conflict (mismatched conflict signal)")
        if is_conflict_tier and not is_conflict_state:
            failures.append("tier=conflict but match_state!=red (mismatched conflict signal)")

    return AuditResult(ok=not failures, failures=tuple(failures))

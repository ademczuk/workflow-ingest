from __future__ import annotations

from pathlib import Path

import pytest

from inventory.model import Snapshot, load_snapshot


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = PROJECT_ROOT / "tests" / "fixtures"
SNAPSHOT_PATH = PROJECT_ROOT / "inventory" / "snapshot.yaml"

KNOWN_VIDEO_IDS = ("mREHBZQbhBo", "xfwmzYOod3k", "nWzXyjXCoCE")


@pytest.fixture(scope="session")
def snapshot() -> Snapshot:
    return load_snapshot(SNAPSHOT_PATH)


@pytest.fixture(scope="session")
def fixture_paths() -> list[Path]:
    return [FIXTURES / f"{vid}_summary.md" for vid in KNOWN_VIDEO_IDS]

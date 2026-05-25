"""Adapter wrapping the youtube-summarizer MCP server.

This module defines the contract; the actual MCP calls are made by the
queue-processor automation, which has the MCP tool surface available.
The adapter is here so unit tests can stub it.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class StackHealth:
    qwen36_ok: bool
    parakeet_ok: bool
    kokoro_ok: bool
    ready_for_summarize: bool

    @property
    def degraded(self) -> bool:
        return not (self.qwen36_ok and self.parakeet_ok)


@dataclass(frozen=True)
class SummarizeResult:
    ok: bool
    video_id: str
    summary_md_path: Path | None
    error: str | None = None


class YoutubeMcp(Protocol):
    def status(self) -> StackHealth: ...
    def summarize(self, url: str) -> SummarizeResult: ...

from __future__ import annotations

from typing import Protocol


class AdbProtocol(Protocol):
    """Minimal ADB interface for layer navigation.

    Users implement or inject their existing ADB client.
    Any ``AdbClient`` that implements these methods satisfies
    this protocol — no adapter needed.
    """

    def screencap(self) -> bytes: ...
    def key_event(self, code: int) -> None: ...
    def foreground_package(self) -> str: ...
    def tap(self, x: int, y: int) -> None: ...
    def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300,
    ) -> None: ...
    def _run(self, args: list[str]) -> str: ...

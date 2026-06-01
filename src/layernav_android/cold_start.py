"""Generic cold-start: launch an Android app from the launcher home screen.

Three paths tried in priority order:

1. **monkey** — ``monkey -p <package> -c LAUNCHER 1`` (primary)
2. **am start Intent** — ``am start -a MAIN -c LAUNCHER <package>`` (backup
   for custom ROMs like MIUI / ColorOS that may restrict monkey)
3. **Dock icon tap** — calculated via ``dock_app_icon_coords`` (last resort,
   with 0.5 s pre‑wait and up to 2 retries)

After the app enters foreground, optionally taps a session tab to reach the
app's main content list (e.g. WeChat's bottom "微信" tab).

Screen dimensions are always auto‑detected via ``adb shell wm size``.

.. code-block:: python

    from layernav_android.cold_start import cold_start_app_from_launcher

    ok = cold_start_app_from_launcher(
        adb, "com.tencent.mm",
        app_name="wechat", M=4, N=3,
        session_tab_x=108, session_tab_y=2192,
    )
"""

from __future__ import annotations

import logging
import re
import time

from layernav_android._protocol import AdbProtocol

LOG = logging.getLogger("layernav.cold_start")

_SZ_RE = re.compile(r"(\d{3,})\s*x\s*(\d{3,})")

APP_DEFAULTS: dict[str, dict[str, int]] = {
    "wechat": {"M": 4, "N": 3},
    "xhs":    {"M": 4, "N": 1},
}

_DOCK_RETRIES = 2
_DOCK_PRE_WAIT_S = 0.5


def dock_app_icon_coords(
    screen_w: int,
    screen_h: int,
    scale_w: float,
    *,
    app_name: str = "wechat",
    M: int = 4,
    N: int | None = None,
) -> tuple[int, int]:
    """Calculate the centre of the *N*-th Dock slot (1‑indexed) in a Dock with *M* equal-width slots.

    - *M*: total number of Dock slots (default 4).
    - *N*: 1‑based slot index; defaults from ``APP_DEFAULTS`` (wechat→3, xhs→1).
    - Formula: ``x = round(W * (N - 0.5) / M)``.
    """
    if N is None:
        defaults = APP_DEFAULTS.get(app_name, {})
        N = defaults.get("N", 1)
        M = defaults.get("M", M)
    N = max(1, min(M, N))
    pad_x = max(12, screen_w // 8)
    dx = int(round(screen_w * (N - 0.5) / M))
    dx = max(pad_x, min(screen_w - pad_x, dx))
    dy = screen_h - max(48, int(round(52 * max(scale_w, 1e-6))))
    return dx, dy


def cold_start_app_from_launcher(
    adb: AdbProtocol,
    package: str,
    *,
    app_name: str = "wechat",
    M: int = 4,
    N: int | None = None,
    session_tab_x: int | None = None,
    session_tab_y: int | None = None,
    force_stop_before: bool = True,
    deadline_s: float = 25.0,
) -> bool:
    """Cold-start *package* from the Android launcher and optionally tap a session tab.

    Screen dimensions are always auto‑detected via ``adb shell wm size``.

    Args:
        adb: :class:`AdbProtocol` client.
        package: Android package name (e.g. ``"com.tencent.mm"``).
        app_name: key in :data:`APP_DEFAULTS` — drives default *M* / *N*.
        M: number of Dock slots.
        N: 1‑based slot index where the app icon lives.
        session_tab_x, session_tab_y: if both are non-``None``, tap this
            coordinate after the app enters foreground (e.g. WeChat's
            「微信」 bottom tab).
        force_stop_before: issue ``am force-stop`` before cold-start.
        deadline_s: maximum time budget for the whole cold-start.

    Returns:
        ``True`` if *package* is the foreground app after cold-start.
    """
    screen_w, screen_h = _resolve_screen_size(adb)
    scale_w = screen_w / 1080.0
    LOG.info("cold_start: screen %dx%d scale_w=%.3f", screen_w, screen_h, scale_w)

    deadline = time.monotonic() + deadline_s

    if force_stop_before:
        LOG.info("cold_start: force-stop %s", package)
        try:
            adb._run(["shell", "am", "force-stop", package])
        except Exception:
            LOG.warning("cold_start: force-stop %s failed (non-fatal)", package)
        time.sleep(0.65)

    # -- path 1: monkey LAUNCHER --
    if _try_monkey(adb, package):
        time.sleep(1.5)
        _tap_session_tab(adb, session_tab_x, session_tab_y)
        if time.monotonic() < deadline and _check_foreground(adb, package):
            return True

    # -- path 2: am start Intent (backup for custom ROMs) --
    if _try_am_start(adb, package):
        time.sleep(1.5)
        _tap_session_tab(adb, session_tab_x, session_tab_y)
        if time.monotonic() < deadline and _check_foreground(adb, package):
            return True

    # -- path 3: Dock icon tap (last resort, with pre-wait + retry) --
    dx, dy = dock_app_icon_coords(
        screen_w, screen_h, scale_w, app_name=app_name, M=M, N=N,
    )
    if _try_dock_tap_with_retry(adb, package, dx, dy, session_tab_x, session_tab_y):
        if time.monotonic() < deadline and _check_foreground(adb, package):
            return True

    return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_screen_size(adb: AdbProtocol) -> tuple[int, int]:
    out = adb._run(["shell", "wm", "size"])
    m = _SZ_RE.search(out)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 1080, 1920


def _try_monkey(adb: AdbProtocol, package: str) -> bool:
    LOG.info("cold_start: monkey LAUNCHER for %s", package)
    try:
        adb._run(
            ["shell", "monkey", "-p", package, "-c",
             "android.intent.category.LAUNCHER", "1"],
        )
        return True
    except Exception as exc:
        LOG.warning("cold_start: monkey failed (%s)", exc)
        return False


def _try_am_start(adb: AdbProtocol, package: str) -> bool:
    LOG.info("cold_start: am start LAUNCHER for %s", package)
    try:
        adb._run(
            ["shell", "am", "start",
             "-a", "android.intent.action.MAIN",
             "-c", "android.intent.category.LAUNCHER",
             package],
        )
        return True
    except Exception as exc:
        LOG.warning("cold_start: am start failed (%s)", exc)
        return False


def _tap_session_tab(
    adb: AdbProtocol,
    session_tab_x: int | None,
    session_tab_y: int | None,
) -> None:
    if session_tab_x is not None and session_tab_y is not None:
        LOG.info("cold_start: tap session tab (%d, %d)", session_tab_x, session_tab_y)
        adb.tap(session_tab_x, session_tab_y)
        time.sleep(0.55)


def _try_dock_tap_with_retry(
    adb: AdbProtocol,
    package: str,
    dx: int,
    dy: int,
    session_tab_x: int | None,
    session_tab_y: int | None,
) -> bool:
    LOG.info("cold_start: Dock tap at (%d, %d) for %s (pre-wait %.1fs, max %d retries)",
             dx, dy, package, _DOCK_PRE_WAIT_S, _DOCK_RETRIES)
    for attempt in range(1 + _DOCK_RETRIES):
        if attempt > 0:
            time.sleep(_DOCK_PRE_WAIT_S)
        adb.tap(dx, dy)
        time.sleep(1.2)
        _tap_session_tab(adb, session_tab_x, session_tab_y)
        if _check_foreground(adb, package):
            LOG.info("cold_start: Dock tap succeeded on attempt %d", attempt + 1)
            return True
        LOG.warning("cold_start: Dock tap attempt %d failed", attempt + 1)
    return False


def _check_foreground(adb: AdbProtocol, package: str) -> bool:
    try:
        return adb.foreground_package() == package
    except Exception:
        return False
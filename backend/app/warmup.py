"""Background warm-up of the expensive per-process caches.

The reference scenarios and the Executive Story are computed once per API process. Without a warm-up the
first visitor after a restart waits for them. The warm-up runs in a daemon thread after start-up, never
blocks the server or /health, and a failure (database down, schema behind) is recorded and logged rather
than raised: requests still compute lazily and retry. Disable with WARM_ON_START=false.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

log = logging.getLogger("warmup")

_state: dict = {"enabled": False, "steps": {}, "seconds": {}, "error": None}
_lock = threading.Lock()


def enabled() -> bool:
    return os.environ.get("WARM_ON_START", "true").strip().lower() not in {"0", "false", "no", "off"}


def status() -> dict:
    with _lock:
        return {"enabled": _state["enabled"], "steps": dict(_state["steps"]),
                "seconds": dict(_state["seconds"]), "error": _state["error"]}


def run(steps: dict[str, Callable[[], object]]) -> None:
    """Run the steps in order, recording progress; stops at the first failure."""
    with _lock:
        _state.update(enabled=True, steps={name: "pending" for name in steps}, seconds={}, error=None)
    for name, step in steps.items():
        started = time.monotonic()
        with _lock:
            _state["steps"][name] = "running"
        try:
            step()
        except Exception as exc:  # noqa: BLE001 - warm-up must never take the server down
            with _lock:
                _state["steps"][name] = "failed"
                _state["error"] = f"{name}: {type(exc).__name__}: {exc}"[:300]
            log.warning("warm-up step %s failed: %s", name, exc)
            return
        with _lock:
            _state["steps"][name] = "ready"
            _state["seconds"][name] = round(time.monotonic() - started, 1)
        log.info("warm-up step %s ready in %.1fs", name, time.monotonic() - started)


def start(steps: dict[str, Callable[[], object]]) -> threading.Thread | None:
    if not enabled():
        return None
    thread = threading.Thread(target=run, args=(steps,), name="warmup", daemon=True)
    thread.start()
    return thread

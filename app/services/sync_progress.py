"""전체 동기화(continue) 진행 상황 — 다른 요청에서 폴링할 수 있도록 메모리에 보관."""

from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.Lock()
_state: dict[int, dict[str, Any]] = {}


def set_progress(user_id: int, **kwargs: Any) -> None:
    with _lock:
        s = _state.setdefault(user_id, {})
        s.update(kwargs)
        s["updated_at"] = time.monotonic()


def get_progress(user_id: int) -> dict[str, Any]:
    with _lock:
        return dict(_state.get(user_id) or {})


def clear_progress(user_id: int) -> None:
    with _lock:
        _state.pop(user_id, None)

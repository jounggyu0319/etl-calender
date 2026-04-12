"""사용자별 Selenium WebDriver 보관 — 서버 reload 후에도 기존 chromedriver 세션 재연결."""
from __future__ import annotations
import json, os, threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from selenium.webdriver.remote.webdriver import WebDriver

_lock = threading.Lock()
_drivers: dict[int, WebDriver] = {}

_SESSION_FILE = Path(os.environ.get("TMPDIR", "/tmp")) / "etlcal_sessions.json"


def _load_file() -> dict:
    try:
        return json.loads(_SESSION_FILE.read_text())
    except Exception:
        return {}


def _save_file(data: dict) -> None:
    try:
        _SESSION_FILE.write_text(json.dumps(data))
    except Exception:
        pass


def _try_reconnect(user_id: int) -> "WebDriver | None":
    data = _load_file()
    info = data.get(str(user_id))
    if not info:
        return None
    try:
        from selenium.webdriver.chrome.options import Options as ChromeOptions
        from selenium.webdriver.remote.webdriver import WebDriver as RemoteDriver
        opts = ChromeOptions()
        opts.add_experimental_option("detach", True)
        driver = RemoteDriver(command_executor=info["executor_url"], options=opts)
        driver.session_id = info["session_id"]
        _ = driver.current_url
        return driver
    except Exception:
        data.pop(str(user_id), None)
        _save_file(data)
        return None


def store(user_id: int, driver: "WebDriver") -> None:
    with _lock:
        old = _drivers.pop(user_id, None)
        if old is not None:
            try:
                old.quit()
            except Exception:
                pass
        _drivers[user_id] = driver
        try:
            executor_url = driver.command_executor._url
        except Exception:
            try:
                executor_url = driver.command_executor._client._base_url
            except Exception:
                executor_url = None
        if executor_url:
            data = _load_file()
            data[str(user_id)] = {"session_id": driver.session_id, "executor_url": executor_url}
            _save_file(data)


def peek(user_id: int) -> "WebDriver | None":
    with _lock:
        driver = _drivers.get(user_id)
        if driver is not None:
            return driver
        driver = _try_reconnect(user_id)
        if driver is not None:
            _drivers[user_id] = driver
        return driver


def remove(user_id: int, *, quit_driver: bool = True) -> None:
    with _lock:
        driver = _drivers.pop(user_id, None)
        if driver is not None and quit_driver:
            try:
                driver.quit()
            except Exception:
                pass
        data = _load_file()
        data.pop(str(user_id), None)
        _save_file(data)

#!/usr/bin/env python3
"""Print .env keys and whether values are set; mask secrets (for diagnostics)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"

def _should_mask_key(key: str) -> bool:
    k = key.upper()
    if "PASSWORD" in k or k.endswith("_PASS"):
        return True
    if "SECRET_KEY" in k or "CRYPTO_KEY" in k or "WEBHOOK_SECRET" in k:
        return True
    if "TOKEN" in k and "ACCESS" not in k:
        return True
    if k in ("APP_SECRET_KEY", "JWT_SECRET", "STRIPE_SECRET_KEY"):
        return True
    return False


def mask_line(key: str, value: str) -> str:
    if _should_mask_key(key) and value.strip():
        return f"{key}=***masked*** (len={len(value.strip())})"
    if not value.strip():
        return f"{key}=(empty)"
    if len(value) > 72:
        return f"{key}=(set, len={len(value)})"
    return f"{key}={value}"


def main() -> None:
    print("=== .env 진단 (프로젝트 루트) ===", flush=True)
    print(f"path: {ENV}", flush=True)
    if not ENV.is_file():
        print("(파일 없음)", flush=True)
        return
    etl_dbg_seen = False
    for raw in ENV.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            print(line, flush=True)
            continue
        if "=" not in line:
            print(line, flush=True)
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key.upper() == "ETL_CHROME_DEBUGGER_ADDRESS":
            etl_dbg_seen = True
        print(mask_line(key, val), flush=True)
    print("---", flush=True)
    print(
        "ETL_CHROME_DEBUGGER_ADDRESS: "
        + ("키 있음" if etl_dbg_seen else "키 없음 → 앱 기본값 127.0.0.1:9222 사용"),
        flush=True,
    )


if __name__ == "__main__":
    main()

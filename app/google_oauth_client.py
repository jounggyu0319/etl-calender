"""Google OAuth 웹 클라이언트 설정: env JSON 또는 secrets 파일."""

from __future__ import annotations

import json
import os
from typing import Any

from app.config import Settings


def load_google_oauth_client_dict(settings: Settings) -> dict[str, Any] | None:
    """`GOOGLE_CREDENTIALS_JSON` 이 있으면 파싱, 없으면 `GOOGLE_CLIENT_SECRETS_FILE` 경로의 JSON 파일."""
    raw = settings.google_credentials_json
    if raw:
        return json.loads(raw)
    path = settings.google_client_secrets_file
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None

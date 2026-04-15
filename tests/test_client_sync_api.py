"""client_sync 서비스·스키마·OpenAPI (httpx / TestClient 없이 동작)."""

from __future__ import annotations

import atexit
import os
import tempfile
import unittest
import uuid
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet

_fd, _TEST_DB_PATH = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ.setdefault("APP_SECRET_KEY", "unit-test-secret-key-32chars-min!!")
os.environ.setdefault("CRYPTO_KEY", Fernet.generate_key().decode())
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH}"


def _cleanup_db() -> None:
    try:
        os.unlink(_TEST_DB_PATH)
    except OSError:
        pass


atexit.register(_cleanup_db)

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.main import app
from app.models import User
from app.schemas import ClientSyncImport, ClientSyncItem
from app.security import encrypt_text, hash_password
from pydantic import ValidationError

from app.services.client_sync import import_from_client

init_db()


def _new_user(db) -> User:
    email = f"u{uuid.uuid4().hex[:14]}@test.example"
    u = User(email=email, hashed_password=hash_password("testpass123"))
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


class TestImportFromClient(unittest.TestCase):
    def test_empty_items_message(self) -> None:
        settings = get_settings()
        db = SessionLocal()
        try:
            user = _new_user(db)
            # Google 미연동이면 빈 목록도 앞단에서 막히므로, JSON만 채워 빈 목록 분기까지 검증합니다.
            user.google_creds_enc = encrypt_text("{}", settings)
            db.add(user)
            db.commit()
            db.refresh(user)
            r = import_from_client(db, user, settings, [])
            self.assertEqual(r.new_assignments, 0)
            self.assertEqual(r.calendar_events_created, 0)
            self.assertIn("없거나", r.message or "")
        finally:
            db.close()

    def test_requires_google(self) -> None:
        settings = get_settings()
        db = SessionLocal()
        try:
            user = _new_user(db)
            items = [
                ClientSyncItem(
                    id="https://myetl.snu.ac.kr/mod/assign/view.php?id=1",
                    title="과제 1",
                    subject="테스트강의",
                    url="https://myetl.snu.ac.kr/mod/assign/view.php?id=1",
                    activity_type="assign",
                    deadline="2026년 4월 12일 오후 11:59",
                )
            ]
            r = import_from_client(db, user, settings, items)
            self.assertEqual(r.calendar_events_created, 0)
            self.assertIn("Google", r.message or "")
        finally:
            db.close()

    @patch("app.services.client_sync.add_assignment_to_calendar", return_value=True)
    @patch("app.services.client_sync.ensure_calendar_service")
    def test_inserts_and_dedupes_seen(
        self, mock_ensure: MagicMock, mock_add: MagicMock
    ) -> None:
        mock_ensure.return_value = (
            MagicMock(),
            '{"token": "stub", "refresh_token": "stub"}',
        )
        settings = get_settings()
        db = SessionLocal()
        try:
            user = _new_user(db)
            fake_google = '{"token": "x", "refresh_token": "y", "client_id": "z", "client_secret": "w"}'
            user.google_creds_enc = encrypt_text(fake_google, settings)
            db.add(user)
            db.commit()
            db.refresh(user)

            item = ClientSyncItem(
                id="https://myetl.snu.ac.kr/mod/assign/view.php?id=99901",
                title="단위테스트 과제",
                subject="단위테스트 강의",
                url="https://myetl.snu.ac.kr/mod/assign/view.php?id=99901",
                activity_type="assign",
                deadline="2026년 6월 1일 오후 11:59",
            )
            r = import_from_client(db, user, settings, [item])
            self.assertEqual(r.new_assignments, 1)
            self.assertEqual(r.calendar_events_created, 1)
            mock_add.assert_called_once()

            db.refresh(user)
            r2 = import_from_client(db, user, settings, [item])
            self.assertEqual(r2.new_assignments, 0)
            self.assertEqual(r2.calendar_events_created, 0)
        finally:
            db.close()


class TestPydanticSchemas(unittest.TestCase):
    def test_client_sync_import_rejects_empty_id(self) -> None:
        with self.assertRaises(ValidationError):
            ClientSyncImport.model_validate(
                {"items": [{"id": "", "title": "x", "subject": "y", "url": "z"}]}
            )


class TestOpenApi(unittest.TestCase):
    def test_from_client_route_registered(self) -> None:
        spec = app.openapi()
        paths = spec.get("paths", {})
        self.assertIn("/api/sync/from-client", paths)
        post = paths["/api/sync/from-client"].get("post")
        self.assertIsNotNone(post)
        self.assertIn("/api/sync/etl/prepare", paths)
        self.assertIn("/api/sync/etl/continue", paths)
        self.assertIn("/api/sync/canvas", paths)


class TestCalendarParse(unittest.TestCase):
    def test_parse_deadline_ko(self) -> None:
        from calendar_service import parse_deadline

        d = parse_deadline("2026년 6월 1일 오후 11:59")
        self.assertIsNotNone(d)
        self.assertIn("dateTime", d)


if __name__ == "__main__":
    unittest.main()

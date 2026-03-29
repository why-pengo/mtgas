"""
Tests for backup and restore views.
"""

import json
import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from django.test import Client
from django.urls import reverse

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def sample_db(db):
    """Create minimal data so a backup has something to export."""
    from stats.models import Card, Deck

    Card.objects.create(grp_id=1, name="Lightning Bolt", type_line="Instant")
    Deck.objects.create(deck_id="deck-001", name="Red Deck Wins", format="Standard")


# ---------------------------------------------------------------------------
# Backup download
# ---------------------------------------------------------------------------


class TestBackupDownload:
    def test_returns_200(self, client, sample_db):
        response = client.get(reverse("stats:backup_download"))
        assert response.status_code == 200

    def test_content_type_is_json(self, client, sample_db):
        response = client.get(reverse("stats:backup_download"))
        assert response["Content-Type"] == "application/json"

    def test_content_disposition_is_attachment(self, client, sample_db):
        response = client.get(reverse("stats:backup_download"))
        disposition = response["Content-Disposition"]
        assert disposition.startswith("attachment")
        assert "mtgas_backup_" in disposition
        assert ".json" in disposition

    def test_response_body_is_valid_json(self, client, sample_db):
        response = client.get(reverse("stats:backup_download"))
        data = json.loads(
            b"".join(response.streaming_content) if response.streaming else response.content
        )
        assert isinstance(data, list)

    def test_response_contains_app_data(self, client, sample_db):
        response = client.get(reverse("stats:backup_download"))
        content = response.content.decode()
        data = json.loads(content)
        model_names = {item["model"] for item in data}
        assert any(m.startswith("stats.") or m.startswith("cards.") for m in model_names)


# ---------------------------------------------------------------------------
# Backup/restore page (GET)
# ---------------------------------------------------------------------------


class TestBackupRestorePage:
    def test_get_returns_200(self, client, db):
        response = client.get(reverse("stats:backup"))
        assert response.status_code == 200

    def test_get_renders_backup_template(self, client, db):
        response = client.get(reverse("stats:backup"))
        assert "backup.html" in [t.name for t in response.templates]

    def test_page_contains_download_link(self, client, db):
        response = client.get(reverse("stats:backup"))
        assert reverse("stats:backup_download").encode() in response.content

    def test_page_contains_restore_form(self, client, db):
        response = client.get(reverse("stats:backup"))
        assert b'name="backup_file"' in response.content
        assert b'name="confirmed"' in response.content


# ---------------------------------------------------------------------------
# Restore (POST)
# ---------------------------------------------------------------------------


class TestRestore:
    def _make_backup_file(self, data: list) -> BytesIO:
        """Return a BytesIO that looks like an uploaded .json backup."""
        buf = BytesIO(json.dumps(data).encode())
        buf.name = "mtgas_backup_test.json"
        return buf

    def test_restore_without_file_redirects_with_error(self, client, db):
        response = client.post(
            reverse("stats:backup"),
            {"confirmed": "on"},
        )
        assert response.status_code == 302
        follow = client.get(response["Location"])
        messages = [str(m) for m in follow.context["messages"]]
        assert any("No backup file" in m for m in messages)

    def test_restore_without_confirmation_redirects_with_error(self, client, db):
        buf = self._make_backup_file([])
        response = client.post(
            reverse("stats:backup"),
            {"backup_file": buf},
        )
        assert response.status_code == 302
        follow = client.get(response["Location"])
        messages = [str(m) for m in follow.context["messages"]]
        assert any("confirm" in m.lower() for m in messages)

    def test_restore_with_invalid_json_shows_error(self, client, db):
        bad_file = BytesIO(b"this is not json {{{")
        bad_file.name = "bad.json"
        response = client.post(
            reverse("stats:backup"),
            {"backup_file": bad_file, "confirmed": "on"},
        )
        assert response.status_code == 302
        follow = client.get(response["Location"])
        messages = [str(m) for m in follow.context["messages"]]
        assert any("not valid JSON" in m or "Invalid" in m for m in messages)

    def test_restore_with_wrong_extension_shows_error(self, client, db):
        buf = BytesIO(b"data")
        buf.name = "backup.txt"
        response = client.post(
            reverse("stats:backup"),
            {"backup_file": buf, "confirmed": "on"},
        )
        assert response.status_code == 302
        follow = client.get(response["Location"])
        messages = [str(m) for m in follow.context["messages"]]
        assert any("Invalid file type" in m for m in messages)

    def test_restore_with_empty_backup_clears_data(self, client, sample_db):
        from stats.models import Card, Deck

        assert Card.objects.exists()
        assert Deck.objects.exists()

        buf = self._make_backup_file([])
        with patch("stats.views.backup.call_command"):
            response = client.post(
                reverse("stats:backup"),
                {"backup_file": buf, "confirmed": "on"},
            )

        assert response.status_code == 302
        assert not Card.objects.exists()
        assert not Deck.objects.exists()

    def test_successful_restore_shows_success_message(self, client, db):
        buf = self._make_backup_file([])
        with patch("stats.views.backup.call_command"):
            response = client.post(
                reverse("stats:backup"),
                {"backup_file": buf, "confirmed": "on"},
            )
        assert response.status_code == 302
        follow = client.get(response["Location"])
        messages = [str(m) for m in follow.context["messages"]]
        assert any("success" in m.lower() or "restored" in m.lower() for m in messages)

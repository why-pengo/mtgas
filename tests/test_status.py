"""
Tests for the services status page (stats app).

Tests the service_status view, service_status_api JSON endpoint,
and service_control POST endpoint with mocked Redis, Celery, and subprocess.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

from django.test import Client
from django.urls import reverse

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def redis_running():
    """Patch _check_redis to report running."""
    with patch(
        "stats.views.status._check_redis",
        return_value={"running": True, "detail": "localhost:6379"},
    ):
        yield


@pytest.fixture
def redis_stopped():
    """Patch _check_redis to report stopped."""
    with patch(
        "stats.views.status._check_redis",
        return_value={"running": False, "detail": "Connection refused"},
    ):
        yield


@pytest.fixture
def celery_running():
    """Patch _check_celery to report running."""
    with patch(
        "stats.views.status._check_celery",
        return_value={"running": True, "detail": "1 worker(s): celery@host"},
    ):
        yield


@pytest.fixture
def celery_stopped():
    """Patch _check_celery to report stopped."""
    with patch(
        "stats.views.status._check_celery",
        return_value={"running": False, "detail": "No workers responded"},
    ):
        yield


# ---------------------------------------------------------------------------
# TestServiceStatusPage
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestServiceStatusPage:
    """Tests for the GET /status/ HTML view."""

    def test_get_returns_200(self, client, redis_running, celery_running):
        response = client.get(reverse("stats:service_status"))
        assert response.status_code == 200

    def test_shows_redis_service(self, client, redis_running, celery_stopped):
        response = client.get(reverse("stats:service_status"))
        assert b"Redis" in response.content

    def test_shows_celery_service(self, client, redis_stopped, celery_running):
        response = client.get(reverse("stats:service_status"))
        assert b"Celery Worker" in response.content

    def test_running_badge_shown_when_redis_up(self, client, redis_running, celery_stopped):
        response = client.get(reverse("stats:service_status"))
        assert b"Running" in response.content

    def test_stopped_badge_shown_when_all_down(self, client, redis_stopped, celery_stopped):
        response = client.get(reverse("stats:service_status"))
        assert b"Stopped" in response.content

    def test_dev_warning_present(self, client, redis_stopped, celery_stopped):
        """The dev-only warning is always displayed."""
        response = client.get(reverse("stats:service_status"))
        assert b"Development tool" in response.content

    def test_services_nav_link_present(self, client, redis_stopped, celery_stopped):
        """The Services nav link is in the base template."""
        response = client.get(reverse("stats:service_status"))
        assert b"Services" in response.content


# ---------------------------------------------------------------------------
# TestServiceStatusApi
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestServiceStatusApi:
    """Tests for the GET /status/api/ JSON endpoint."""

    def test_returns_json_200(self, client, redis_running, celery_running):
        response = client.get(reverse("stats:service_status_api"))
        assert response.status_code == 200
        assert response["Content-Type"] == "application/json"

    def test_redis_running_true(self, client, redis_running, celery_stopped):
        data = json.loads(client.get(reverse("stats:service_status_api")).content)
        assert data["redis"]["running"] is True

    def test_redis_stopped_false(self, client, redis_stopped, celery_stopped):
        data = json.loads(client.get(reverse("stats:service_status_api")).content)
        assert data["redis"]["running"] is False

    def test_celery_running_true(self, client, redis_stopped, celery_running):
        data = json.loads(client.get(reverse("stats:service_status_api")).content)
        assert data["celery"]["running"] is True

    def test_celery_stopped_false(self, client, redis_stopped, celery_stopped):
        data = json.loads(client.get(reverse("stats:service_status_api")).content)
        assert data["celery"]["running"] is False

    def test_response_includes_timestamp(self, client, redis_running, celery_running):
        data = json.loads(client.get(reverse("stats:service_status_api")).content)
        assert "timestamp" in data
        assert isinstance(data["timestamp"], float)

    def test_get_only_not_post(self, client):
        """The API endpoint should be accessible via GET; POST returns 405."""
        response = client.post(reverse("stats:service_status_api"))
        assert response.status_code == 405


# ---------------------------------------------------------------------------
# TestServiceControl
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestServiceControl:
    """Tests for the POST /status/control/ control endpoint."""

    def _post(self, client, service, action):
        return client.post(
            reverse("stats:service_control"),
            data=json.dumps({"service": service, "action": action}),
            content_type="application/json",
        )

    def test_get_not_allowed(self, client):
        response = client.get(reverse("stats:service_control"))
        assert response.status_code == 405

    def test_invalid_json_returns_400(self, client):
        response = client.post(
            reverse("stats:service_control"),
            data="not json",
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_invalid_action_returns_400(self, client):
        response = self._post(client, "redis", "restart")
        assert response.status_code == 400

    def test_unknown_service_returns_400(self, client):
        response = self._post(client, "mysql", "start")
        assert response.status_code == 400

    def test_start_redis_success(self, client, redis_stopped):
        with patch("stats.views.status.REDIS_SERVER_BIN", "/usr/bin/redis-server"):
            with patch("stats.views.status.subprocess.Popen") as mock_popen:
                with patch(
                    "stats.views.status._check_redis",
                    return_value={"running": True, "detail": "localhost:6379"},
                ):
                    response = self._post(client, "redis", "start")
        data = json.loads(response.content)
        assert data["ok"] is True
        mock_popen.assert_called_once()

    def test_start_redis_no_binary_fails(self, client):
        with patch("stats.views.status.REDIS_SERVER_BIN", None):
            response = self._post(client, "redis", "start")
        data = json.loads(response.content)
        assert data["ok"] is False
        assert "not found" in data["error"].lower()

    def test_stop_redis_via_cli(self, client, redis_running):
        with patch("stats.views.status.REDIS_CLI_BIN", "/usr/bin/redis-cli"):
            with patch("stats.views.status.subprocess.run") as mock_run:
                response = self._post(client, "redis", "stop")
        data = json.loads(response.content)
        assert data["ok"] is True
        mock_run.assert_called_once()

    def test_start_celery_success(self, client):
        # First call: not running (passes the "already running" guard)
        # Subsequent calls: running (worker responded after start)
        check_results = [
            {"running": False, "detail": "No workers"},
            {"running": True, "detail": "1 worker(s)"},
        ]
        with patch("stats.views.status.subprocess.Popen"):
            with patch("stats.views.status._check_celery", side_effect=check_results):
                with patch("stats.views.status.time.sleep"):
                    response = self._post(client, "celery", "start")
        data = json.loads(response.content)
        assert data["ok"] is True

    def test_start_celery_already_running(self, client, celery_running):
        response = self._post(client, "celery", "start")
        data = json.loads(response.content)
        assert data["ok"] is False
        assert "already running" in data["error"]

    def test_stop_celery_broadcasts_shutdown(self, client, celery_running):
        with patch("stats.views.status.celery_app") as mock_app:
            with patch("stats.views.status._pid_alive", return_value=False):
                response = self._post(client, "celery", "stop")
        data = json.loads(response.content)
        assert data["ok"] is True
        mock_app.control.broadcast.assert_called_once_with("shutdown", reply=False)

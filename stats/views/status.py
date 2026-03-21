"""
Service status view — checks Redis and Celery Worker health,
and provides start/stop process control for local development.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

import psutil
import redis as redis_lib

from mtgas_project.celery import app as celery_app

BASE_DIR: Path = settings.BASE_DIR
DATA_DIR: Path = BASE_DIR / "data"

# PID files written here (data/ is already gitignored)
REDIS_PID_FILE = DATA_DIR / "mtgas-redis.pid"
CELERY_PID_FILE = DATA_DIR / "mtgas-celery.pid"
CELERY_LOG_FILE = DATA_DIR / "mtgas-celery.log"

CELERY_BIN = str(BASE_DIR / ".venv" / "bin" / "celery")

# Discover redis-server binary
_REDIS_CANDIDATES = [
    "redis-server",
    "/opt/homebrew/bin/redis-server",
    "/usr/local/bin/redis-server",
    "/usr/bin/redis-server",
]
REDIS_SERVER_BIN: str | None = next((p for p in _REDIS_CANDIDATES if shutil.which(p)), None)
REDIS_CLI_BIN: str | None = next(
    (
        p
        for p in [p.replace("redis-server", "redis-cli") for p in _REDIS_CANDIDATES]
        if shutil.which(p)
    ),
    None,
)


# ---------------------------------------------------------------------------
# Health check helpers
# ---------------------------------------------------------------------------


def _check_redis() -> dict:
    """Ping Redis; return a status dict."""
    try:
        url = getattr(settings, "CELERY_BROKER_URL", "redis://localhost:6379/0")
        # Parse host/port from broker URL (basic handling)
        url_no_scheme = url.replace("redis://", "")
        host_port = url_no_scheme.split("/")[0]
        host, _, port_str = host_port.partition(":")
        port = int(port_str) if port_str else 6379
        r = redis_lib.Redis(host=host or "localhost", port=port, socket_timeout=1)
        r.ping()
        return {"running": True, "detail": f"{host}:{port}"}
    except Exception as exc:
        return {"running": False, "detail": str(exc)}


def _check_celery() -> dict:
    """Ping Celery workers; return a status dict."""
    try:
        inspector = celery_app.control.inspect(timeout=1)
        active = inspector.ping()
        if active:
            worker_names = list(active.keys())
            return {
                "running": True,
                "detail": f"{len(worker_names)} worker(s): {', '.join(worker_names)}",
            }
        return {"running": False, "detail": "No workers responded"}
    except Exception as exc:
        return {"running": False, "detail": str(exc)}


def _pid_alive(pid_file: Path) -> bool:
    """Return True if the PID recorded in pid_file is a running process."""
    try:
        pid = int(pid_file.read_text().strip())
        return psutil.pid_exists(pid) and psutil.Process(pid).is_running()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


def service_status(request: HttpRequest):
    redis_status = _check_redis()
    celery_status = _check_celery()
    context = {
        "services": [
            {
                "id": "redis",
                "name": "Redis",
                "description": "Message broker for Celery task queue",
                "running": redis_status["running"],
                "detail": redis_status["detail"],
                "can_start": REDIS_SERVER_BIN is not None,
                "install_hint": "brew install redis" if REDIS_SERVER_BIN is None else "",
            },
            {
                "id": "celery",
                "name": "Celery Worker",
                "description": "Processes Paper Card image matching tasks",
                "running": celery_status["running"],
                "detail": celery_status["detail"],
                "can_start": True,
                "install_hint": "",
            },
        ],
        "poll_interval_ms": 5000,
    }
    return render(request, "status.html", context)


@require_GET
def service_status_api(request: HttpRequest) -> JsonResponse:
    """Lightweight JSON endpoint polled by the status page JS."""
    redis_status = _check_redis()
    celery_status = _check_celery()
    return JsonResponse(
        {
            "redis": redis_status,
            "celery": celery_status,
            "timestamp": time.time(),
        }
    )


@require_POST
def service_control(request: HttpRequest) -> JsonResponse:
    """Start or stop a named service. Expects JSON body: {service, action}."""
    try:
        body = json.loads(request.body)
        service = body.get("service")
        action = body.get("action")
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    if action not in ("start", "stop"):
        return JsonResponse({"ok": False, "error": "action must be start or stop"}, status=400)

    try:
        if service == "redis":
            return _control_redis(action)
        elif service == "celery":
            return _control_celery(action)
        else:
            return JsonResponse({"ok": False, "error": f"Unknown service: {service}"}, status=400)
    except Exception as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)


# ---------------------------------------------------------------------------
# Service control helpers
# ---------------------------------------------------------------------------


def _control_redis(action: str) -> JsonResponse:
    if action == "start":
        if REDIS_SERVER_BIN is None:
            return JsonResponse(
                {"ok": False, "error": "redis-server not found. Install Redis first."}
            )
        subprocess.Popen(
            [REDIS_SERVER_BIN, "--daemonize", "yes", "--pidfile", str(REDIS_PID_FILE)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.5)
        if _check_redis()["running"]:
            return JsonResponse({"ok": True, "message": "Redis started"})
        return JsonResponse({"ok": False, "error": "Redis started but ping failed"})

    else:  # stop
        if REDIS_CLI_BIN:
            subprocess.run([REDIS_CLI_BIN, "shutdown"], capture_output=True)
        elif _pid_alive(REDIS_PID_FILE):
            pid = int(REDIS_PID_FILE.read_text().strip())
            os.kill(pid, signal.SIGTERM)
        else:
            return JsonResponse({"ok": False, "error": "Redis is not running"})
        time.sleep(0.5)
        return JsonResponse({"ok": True, "message": "Redis stopped"})


def _control_celery(action: str) -> JsonResponse:
    if action == "start":
        if _check_celery()["running"]:
            return JsonResponse({"ok": False, "error": "Celery worker already running"})
        subprocess.Popen(
            [
                CELERY_BIN,
                "-A",
                "mtgas_project",
                "worker",
                "--loglevel=info",
                "--detach",
                f"--pidfile={CELERY_PID_FILE}",
                f"--logfile={CELERY_LOG_FILE}",
            ],
            cwd=str(BASE_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Give worker a moment to register
        for _ in range(6):
            time.sleep(0.5)
            if _check_celery()["running"]:
                return JsonResponse({"ok": True, "message": "Celery worker started"})
        return JsonResponse({"ok": False, "error": "Worker started but did not respond to ping"})

    else:  # stop
        # Try graceful shutdown via Celery control first
        try:
            celery_app.control.broadcast("shutdown", reply=False)
            time.sleep(1)
        except Exception:
            pass
        # Fall back to PID file SIGTERM
        if _pid_alive(CELERY_PID_FILE):
            pid = int(CELERY_PID_FILE.read_text().strip())
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        return JsonResponse({"ok": True, "message": "Celery worker stopped"})

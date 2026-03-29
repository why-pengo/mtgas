"""
Backup and restore views for the stats app.

Backup:  GET /backup/download/ — streams a dumpdata JSON file to the browser.
Restore: GET /backup/           — shows the backup/restore page.
         POST /backup/          — accepts an uploaded JSON backup and restores it.
"""

import io
import json
import logging
import os
import tempfile
from datetime import datetime

from django.contrib import messages
from django.core.management import call_command
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from ..models import (
    Card,
    CardToken,
    CardTokenRef,
    Deck,
    DeckCard,
    DeckSnapshot,
    GameAction,
    LifeChange,
    Match,
    UnknownCard,
    ZoneTransfer,
)

logger = logging.getLogger("stats.views")

# Models deleted during restore, ordered to respect FK constraints.
_RESTORE_DELETE_ORDER = [
    UnknownCard,
    ZoneTransfer,
    LifeChange,
    GameAction,
    DeckCard,
    DeckSnapshot,
    Match,
    Deck,
    CardTokenRef,
    CardToken,
    Card,
]


def backup_download(request: HttpRequest) -> HttpResponse:
    """Stream a full JSON backup of all app data as a file download."""
    buf = io.StringIO()
    call_command(
        "dumpdata",
        "stats",
        "cards",
        indent=2,
        stdout=buf,
        natural_foreign=True,
        natural_primary=True,
    )
    buf.seek(0)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"mtgas_backup_{timestamp}.json"

    response = HttpResponse(buf.read(), content_type="application/json")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    logger.info("Backup downloaded: %s", filename)
    return response


def backup_restore(request: HttpRequest) -> HttpResponse:
    """Show backup/restore page (GET) or process an uploaded backup file (POST)."""
    if request.method == "POST":
        return _handle_restore(request)
    return render(request, "backup.html")


def _handle_restore(request: HttpRequest) -> HttpResponse:
    """Validate the uploaded backup file and restore it into the database."""
    backup_file = request.FILES.get("backup_file")
    confirmed = request.POST.get("confirmed")

    if not backup_file:
        messages.error(request, "No backup file selected.")
        return redirect("stats:backup")

    if not confirmed:
        messages.error(request, "You must confirm that you want to overwrite all existing data.")
        return redirect("stats:backup")

    if not backup_file.name.endswith(".json"):
        messages.error(request, "Invalid file type. Please upload a .json backup file.")
        return redirect("stats:backup")

    # Write to a temp file so loaddata can read it from disk.
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="wb") as tmp:
            for chunk in backup_file.chunks():
                tmp.write(chunk)
            tmp_path = tmp.name

        # Validate that the file is parseable JSON before touching the DB.
        with open(tmp_path, "r", encoding="utf-8") as f:
            json.load(f)

        with transaction.atomic():
            _flush_app_data()
            call_command("loaddata", tmp_path, verbosity=0)

        logger.info("Database restored from backup: %s", backup_file.name)
        messages.success(request, "Database restored successfully.")

    except json.JSONDecodeError:
        messages.error(request, "Invalid backup file: not valid JSON.")
        logger.warning("Restore failed — invalid JSON: %s", backup_file.name)
    except Exception as exc:
        messages.error(request, f"Restore failed: {exc}")
        logger.exception("Restore failed for file %s", backup_file.name)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    return redirect("stats:backup")


def _flush_app_data() -> None:
    """Delete all app data in FK-safe order before loading a backup."""
    for model in _RESTORE_DELETE_ORDER:
        model.objects.all().delete()

    # PaperCard lives in the cards app — import here to avoid circular imports.
    from cards.models import PaperCard

    PaperCard.objects.all().delete()

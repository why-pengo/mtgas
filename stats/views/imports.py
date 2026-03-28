"""
Import log, card data, and session views, plus all import helper functions.
"""

import logging
import os
import sys
from datetime import datetime
from datetime import timezone as dt_timezone
from pathlib import Path

from django.contrib import messages
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.parser.log_parser import MatchData, MTGALogParser  # noqa: E402
from src.services.import_service import (  # noqa: E402
    _COLOR_LABELS,
    _SKIP_OBJECT_TYPES,
    _TOKEN_OBJECT_TYPES,
    build_type_line,
    generate_token_name,
    generate_unknown_card_description,
)
from src.services.scryfall import ScryfallBulkService, get_scryfall  # noqa: E402

from ..models import (  # noqa: E402
    Card,
    Deck,
    DeckCard,
    DeckSnapshot,
    GameAction,
    ImportSession,
    LifeChange,
    Match,
    UnknownCard,
    ZoneTransfer,
)

logger = logging.getLogger("stats.views")


def import_log(request: HttpRequest) -> HttpResponse:
    """Import one or more log files via web UI."""
    if request.method == "POST":
        log_files = request.FILES.getlist("log_file")
        force = request.POST.get("force") == "on"

        if not log_files:
            messages.error(request, "No log file uploaded.")
            return redirect("stats:import_log")

        try:
            scryfall = get_scryfall()
            scryfall.ensure_bulk_data()
            logger.info("Card data ready")
        except Exception as e:
            messages.error(request, f"Failed to load card data: {e}")
            return redirect("stats:import_log")

        total_imported = total_skipped = total_errors = 0

        for log_file in log_files:
            imp, skp, errs = _process_uploaded_file(log_file, force, scryfall)
            total_imported += imp
            total_skipped += skp
            total_errors += errs

        file_word = f"{len(log_files)} file{'s' if len(log_files) > 1 else ''}"
        if total_imported > 0:
            messages.success(
                request,
                f"Imported {total_imported} matches from {file_word} (skipped {total_skipped}).",
            )
        else:
            messages.warning(
                request,
                f"No new matches found across {file_word}. Skipped {total_skipped} existing.",
            )
        if total_errors:
            messages.warning(request, f"Encountered {total_errors} errors during import.")

        return redirect("stats:import_sessions")

    # GET request - show upload form
    return render(request, "import_log.html")


def _process_uploaded_file(log_file, force: bool, scryfall) -> tuple[int, int, int]:
    """Write one uploaded file to a temp path, import it, return (imported, skipped, errors)."""
    import tempfile

    with tempfile.NamedTemporaryFile(delete=False, suffix=".log") as tmp_file:
        for chunk in log_file.chunks():
            tmp_file.write(chunk)
        tmp_path = tmp_file.name

    try:
        file_size = os.path.getsize(tmp_path)
        file_modified = datetime.fromtimestamp(os.path.getmtime(tmp_path), tz=dt_timezone.utc)

        logger.info(f"Starting import from uploaded file: {log_file.name} ({file_size} bytes)")

        session = ImportSession.objects.create(
            log_file=log_file.name,
            file_size=file_size,
            file_modified=file_modified,
            status="running",
        )
        logger.info(f"Created import session: {session.id}")

        existing_match_ids = set()
        if not force:
            existing_match_ids = set(Match.objects.values_list("match_id", flat=True))
            logger.info(f"Found {len(existing_match_ids)} existing matches in database")

        logger.info("Parsing log file...")
        parser = MTGALogParser(tmp_path)
        matches = parser.parse_matches()

        imported_count = 0
        skipped_count = 0
        errors = []

        for match_data in matches:
            match_id = match_data.match_id

            if not force and match_id in existing_match_ids:
                logger.debug(f"Skipping existing match: {match_id}")
                skipped_count += 1
                continue

            try:
                logger.info(f"Importing match: {match_id}")
                _import_match(match_data, scryfall, session)
                imported_count += 1
                logger.debug(f"Successfully imported match: {match_id}")
            except Exception as e:
                error_msg = f"Match {match_id[:8]}: {str(e)}"
                logger.error(f"Failed to import match {match_id}: {e}", exc_info=True)
                errors.append(error_msg)

        logger.info(
            f"Import complete: {imported_count} imported, {skipped_count} skipped, "
            f"{len(errors)} errors"
        )

        session.matches_imported = imported_count
        session.matches_skipped = skipped_count
        session.status = "completed" if not errors else "completed_with_errors"
        session.completed_at = timezone.now()
        if errors:
            session.error_message = "; ".join(errors[:5])
        session.save()

        return imported_count, skipped_count, len(errors)

    except Exception as e:
        if "session" in dir():
            session.status = "failed"
            session.error_message = str(e)
            session.save()
        logger.error(f"Import failed for {log_file.name}: {e}", exc_info=True)
        return 0, 0, 1
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def card_data(request: HttpRequest) -> HttpResponse:
    """Card data management page."""
    from datetime import datetime

    scryfall = get_scryfall()

    # Get current stats
    try:
        stats = scryfall.stats()
        index_loaded = stats["index_loaded"]
        bulk_file_exists = stats["bulk_file_exists"]
        total_cards = stats["total_cards"]
        bulk_file_size_mb = stats["bulk_file_size_mb"]

        # Get file modification date
        bulk_file_date = None
        if scryfall._bulk_file_path.exists():
            bulk_file_date = datetime.fromtimestamp(scryfall._bulk_file_path.stat().st_mtime)

        # Get index file date
        index_file_date = None
        if scryfall._index_file_path.exists():
            index_file_date = datetime.fromtimestamp(scryfall._index_file_path.stat().st_mtime)

    except Exception as e:
        messages.error(request, f"Error reading card data stats: {str(e)}")
        index_loaded = False
        bulk_file_exists = False
        total_cards = 0
        bulk_file_size_mb = 0
        bulk_file_date = None
        index_file_date = None

    # Get database card count and unknown card stats
    db_card_count = Card.objects.count()
    unknown_card_count = Card.objects.filter(name__startswith="Unknown Card").count()

    # Handle download request
    if request.method == "POST":
        action = request.POST.get("action")
        force = request.POST.get("force") == "on"

        if action == "download":
            try:
                messages.info(
                    request, "Downloading Scryfall card data... This may take a few minutes."
                )

                # Download in the request
                success = scryfall.ensure_bulk_data(force_download=force)

                if success:
                    stats = scryfall.stats()
                    messages.success(
                        request,
                        f"Successfully downloaded card data! "
                        f"{stats['total_cards']} cards indexed "
                        f"({stats['bulk_file_size_mb']:.1f} MB)",
                    )
                else:
                    messages.error(request, "Failed to download card data. Check logs for details.")

            except Exception as e:
                messages.error(request, f"Download failed: {str(e)}")

            return redirect("stats:card_data")

    return render(
        request,
        "card_data.html",
        {
            "index_loaded": index_loaded,
            "bulk_file_exists": bulk_file_exists,
            "total_cards": total_cards,
            "bulk_file_size_mb": bulk_file_size_mb,
            "bulk_file_date": bulk_file_date,
            "index_file_date": index_file_date,
            "db_card_count": db_card_count,
            "unknown_card_count": unknown_card_count,
        },
    )


def import_sessions(request: HttpRequest) -> HttpResponse:
    """View import session history."""
    sessions = ImportSession.objects.order_by("-started_at")[:20]
    return render(request, "import_sessions.html", {"sessions": sessions})


# Helper functions for importing matches
@transaction.atomic
def _import_match(
    match_data: MatchData, scryfall: ScryfallBulkService, import_session: ImportSession
) -> Match:
    """Import a single match into the database."""
    match_id = match_data.match_id
    logger.debug(f"[{match_id}] Starting import")

    # Resolve deck identity — snapshot is created after Match
    deck = None
    if match_data.deck_id:
        deck, created = Deck.objects.get_or_create(
            deck_id=match_data.deck_id,
            defaults={
                "name": match_data.deck_name or "Unknown Deck",
                "format": match_data.format,
            },
        )
        if not created:
            # Sync name and format if they've been updated in Arena
            update_fields = []
            new_name = match_data.deck_name or "Unknown Deck"
            if deck.name != new_name:
                deck.name = new_name
                update_fields.append("name")
            if match_data.format and deck.format != match_data.format:
                deck.format = match_data.format
                update_fields.append("format")
            if update_fields:
                deck.save(update_fields=update_fields + ["updated_at"])
                logger.info(
                    f"[{match_id}] Updated deck {deck.deck_id}: {', '.join(update_fields)} changed"
                )
        logger.debug(f"[{match_id}] Deck ready: {deck.name}")

    # Collect all unique card IDs (includes instance data for better unknowns)
    logger.debug(f"[{match_id}] Collecting card IDs")
    real_cards, special_objects = _collect_card_ids(match_data)
    logger.debug(
        f"[{match_id}] Found {len(real_cards)} real cards, {len(special_objects)} special objects"
    )

    # Calculate duration
    duration = None
    if match_data.start_time and match_data.end_time:
        duration = int((match_data.end_time - match_data.start_time).total_seconds())

    # Ensure datetimes are timezone-aware
    start_time = match_data.start_time
    end_time = match_data.end_time
    if start_time and start_time.tzinfo is None:
        start_time = timezone.make_aware(start_time)
    if end_time and end_time.tzinfo is None:
        end_time = timezone.make_aware(end_time)

    # Create Match
    match = Match.objects.create(
        match_id=match_id,
        game_number=1,
        player_seat_id=match_data.player_seat_id,
        player_name=match_data.player_name,
        player_user_id=match_data.player_user_id,
        opponent_seat_id=match_data.opponent_seat_id,
        opponent_name=match_data.opponent_name,
        opponent_user_id=match_data.opponent_user_id,
        deck=deck,
        event_id=match_data.event_id,
        format=match_data.format,
        match_type=match_data.match_type,
        result=match_data.result,
        winning_team_id=match_data.winning_team_id,
        winning_reason=match_data.winning_reason,
        start_time=start_time,
        end_time=end_time,
        duration_seconds=duration,
        total_turns=match_data.total_turns,
    )
    logger.debug(f"[{match_id}] Match record created: {match.id}")

    # Create deck snapshot for this match, reusing if deck hasn't changed
    if deck and (match_data.deck_cards or match_data.deck_sideboard):
        logger.debug(f"[{match_id}] Ensuring deck snapshot")
        _ensure_deck_snapshot(match_data, deck, match, scryfall, import_session)

    # Ensure cards exist, passing match/deck/session for unknown card tracking
    _ensure_cards(real_cards, special_objects, scryfall, import_session, match, deck, match_data)

    # Import actions, life changes, zone transfers
    logger.debug(f"[{match_id}] Importing game actions")
    _import_actions(match, match_data)

    logger.debug(f"[{match_id}] Importing life changes")
    _import_life_changes(match, match_data)

    logger.debug(f"[{match_id}] Importing zone transfers")
    _import_zone_transfers(match, match_data)

    logger.info(f"[{match_id}] Import complete")
    return match


def _ensure_deck_snapshot(
    match_data: MatchData,
    deck: Deck,
    match: Match,
    scryfall: ScryfallBulkService,
    import_session: ImportSession,
) -> DeckSnapshot:
    """Create or reuse a DeckSnapshot. A new snapshot is only created when the deck
    composition changes relative to the most recent snapshot for this deck."""
    all_deck_ids: dict[int, dict] = {}
    for card in match_data.deck_cards + match_data.deck_sideboard:
        cid = card.get("cardId")
        if cid:
            all_deck_ids.setdefault(cid, {})

    # Ensure every card in this deck list is in the cards table
    if all_deck_ids:
        _ensure_cards(all_deck_ids, {}, scryfall, import_session, match, deck, match_data)

    # Build a frozenset representing this deck composition for comparison
    incoming: set[tuple] = set()
    for card_data in match_data.deck_cards:
        cid = card_data.get("cardId")
        qty = card_data.get("quantity", 1)
        if cid:
            incoming.add((cid, qty, False))
    for card_data in match_data.deck_sideboard:
        cid = card_data.get("cardId")
        qty = card_data.get("quantity", 1)
        if cid:
            incoming.add((cid, qty, True))
    incoming_fs = frozenset(incoming)

    # Check if the latest snapshot for this deck matches the incoming list
    latest = deck.latest_snapshot()
    if latest is not None:
        existing_fs = frozenset(latest.cards.values_list("card_id", "quantity", "is_sideboard"))
        if existing_fs == incoming_fs:
            logger.debug(f"Reusing snapshot {latest.pk} for deck {deck.name} (no changes)")
            match.snapshot = latest
            match.save(update_fields=["snapshot"])
            return latest

    # Deck changed (or no prior snapshot) — create a new one
    snapshot = DeckSnapshot.objects.create(deck=deck)
    snapshot_cards = []

    for card_data in match_data.deck_cards:
        card_id = card_data.get("cardId")
        quantity = card_data.get("quantity", 1)
        if card_id:
            try:
                card = Card.objects.get(grp_id=card_id)
                snapshot_cards.append(
                    DeckCard(snapshot=snapshot, card=card, quantity=quantity, is_sideboard=False)
                )
            except Card.DoesNotExist:
                logger.warning(f"Card {card_id} not found for snapshot")

    for card_data in match_data.deck_sideboard:
        card_id = card_data.get("cardId")
        quantity = card_data.get("quantity", 1)
        if card_id:
            try:
                card = Card.objects.get(grp_id=card_id)
                snapshot_cards.append(
                    DeckCard(snapshot=snapshot, card=card, quantity=quantity, is_sideboard=True)
                )
            except Card.DoesNotExist:
                logger.warning(f"Sideboard card {card_id} not found for snapshot")

    DeckCard.objects.bulk_create(snapshot_cards, ignore_conflicts=True)
    logger.debug(
        f"New snapshot {snapshot.pk} created: {len(snapshot_cards)} cards for deck {deck.name}"
    )
    match.snapshot = snapshot
    match.save(update_fields=["snapshot"])
    return snapshot


def _collect_card_ids(match_data: MatchData) -> tuple[dict[int, dict], dict[int, dict]]:
    """Collect card IDs from match data.

    Returns:
        real_cards: grpId → instance_data for cards that should be looked up in Scryfall.
            Empty dict when the card is only seen in the deck list (no game-state data).
        special_objects: grpId → instance_data for tokens, emblems, and card-face
            types (Adventure, MDFCBack, etc.) that are not standard cards.
    """
    real_cards: dict[int, dict] = {}
    special_objects: dict[int, dict] = {}

    # Deck cards (mainboard + sideboard) are always real cards
    for card in match_data.deck_cards + match_data.deck_sideboard:
        if card.get("cardId"):
            real_cards.setdefault(card["cardId"], {})

    # Categorise each card instance by its Arena object type
    for inst_data in match_data.card_instances.values():
        grp_id = inst_data.get("grp_id")
        obj_type = inst_data.get("type", "")
        if not grp_id:
            continue
        if obj_type in _SKIP_OBJECT_TYPES:
            continue  # Engine-only objects — never store in DB
        if obj_type == "GameObjectType_Card":
            # Prefer instance with the most data (non-empty card_types wins).
            if grp_id not in real_cards or not real_cards[grp_id].get("card_types"):
                real_cards[grp_id] = inst_data
            special_objects.pop(grp_id, None)
        elif obj_type == "GameObjectType_Omen":
            real_cards.pop(grp_id, None)
            special_objects[grp_id] = inst_data
        elif grp_id not in real_cards:
            special_objects.setdefault(grp_id, inst_data)

    # Actions may reference grpIds not captured as card instances
    for action in match_data.actions:
        cid = action.get("card_grp_id")
        if cid and cid not in special_objects:
            real_cards.setdefault(cid, {})

    return real_cards, special_objects


def _ensure_cards(
    real_cards: dict[int, dict],
    special_objects: dict[int, dict],
    scryfall: ScryfallBulkService,
    import_session: ImportSession,
    match: Match | None = None,
    deck: Deck | None = None,
    match_data: MatchData | None = None,
) -> None:
    """Ensure cards/objects exist in the database.

    * real_cards: grpId → instance_data; looked up in Scryfall with Unknown Card fallback.
    * special_objects: tokens/emblems get a generated name; other face types try
      Scryfall first and use a descriptive placeholder on failure.
    """
    all_ids = set(real_cards) | set(special_objects)
    if not all_ids:
        return

    existing_rows = Card.objects.filter(grp_id__in=all_ids).values("grp_id", "name")
    existing_ids = {r["grp_id"] for r in existing_rows}
    unknown_placeholder_ids = {
        r["grp_id"] for r in existing_rows if r["name"].startswith("Unknown Card (")
    }

    missing_real = {gid: real_cards[gid] for gid in (set(real_cards) - existing_ids)}
    missing_special = {gid: d for gid, d in special_objects.items() if gid not in existing_ids}
    upgradeable_real = {gid: real_cards[gid] for gid in (set(real_cards) & unknown_placeholder_ids)}

    if missing_real or missing_special:
        logger.debug(
            f"Looking up {len(missing_real)} cards from Scryfall, "
            f"processing {len(missing_special)} special objects"
        )

    # ── Real cards: Scryfall lookup with Unknown Card fallback ──
    if missing_real:
        card_lookup = scryfall.lookup_cards_batch(set(missing_real))
        cards_to_create = []
        unknown_cards_to_log = []

        for grp_id, card_data in card_lookup.items():
            inst_data = missing_real[grp_id]
            if card_data:
                cards_to_create.append(
                    Card(
                        grp_id=grp_id,
                        name=card_data.get("name"),
                        mana_cost=card_data.get("mana_cost"),
                        cmc=card_data.get("cmc"),
                        type_line=card_data.get("type_line"),
                        colors=card_data.get("colors", []),
                        color_identity=card_data.get("color_identity", []),
                        set_code=card_data.get("set_code"),
                        rarity=card_data.get("rarity"),
                        oracle_text=card_data.get("oracle_text"),
                        power=card_data.get("power"),
                        toughness=card_data.get("toughness"),
                        scryfall_id=card_data.get("scryfall_id"),
                        image_uri=card_data.get("image_uri"),
                    )
                )
            else:
                name = generate_unknown_card_description(grp_id, inst_data)
                type_line = build_type_line(inst_data) or None
                colors = inst_data.get("colors") or []
                power = inst_data.get("power")
                toughness = inst_data.get("toughness")
                context_info: dict = {
                    "grp_id": grp_id,
                    "import_session_id": import_session.id,
                    "match_id": match.match_id if match else None,
                    "deck_id": deck.deck_id if deck else None,
                    "deck_name": deck.name if deck else None,
                }

                # Only associate the player's own deck; opponent cards get deck=None
                card_deck = deck
                if deck and match_data and match_data.player_seat_id and match_data.card_instances:
                    owner_seats = {
                        ci.get("owner_seat")
                        for ci in match_data.card_instances.values()
                        if ci.get("grp_id") == grp_id
                    }
                    if owner_seats and match_data.player_seat_id not in owner_seats:
                        card_deck = None

                is_bare_unknown = name == f"Unknown Card ({grp_id})"
                logger.info(
                    f"Unknown card discovered - grp_id: {grp_id}, "
                    f"deck: {card_deck.name if card_deck else 'N/A'}, "
                    f"match: {match.match_id[:8] if match else 'N/A'}"
                )
                cards_to_create.append(
                    Card(
                        grp_id=grp_id,
                        name=name,
                        type_line=type_line,
                        colors=[_COLOR_LABELS.get(c, c) for c in colors] if colors else [],
                        power=str(power) if power is not None else None,
                        toughness=str(toughness) if toughness is not None else None,
                    )
                )
                if is_bare_unknown:
                    unknown_cards_to_log.append((grp_id, context_info, card_deck))

        if cards_to_create:
            Card.objects.bulk_create(cards_to_create, ignore_conflicts=True)
            logger.debug(f"Created {len(cards_to_create)} new card records")

        if unknown_cards_to_log:
            unknown_records = []
            for grp_id, context, card_deck in unknown_cards_to_log:
                card = Card.objects.get(grp_id=grp_id)
                unknown_records.append(
                    UnknownCard(
                        card=card,
                        match=match,
                        deck=card_deck,
                        import_session=import_session,
                        raw_data=context,
                        is_resolved=False,
                    )
                )
            UnknownCard.objects.bulk_create(unknown_records, ignore_conflicts=True)
            logger.info(f"Logged {len(unknown_records)} unknown cards for manual review")

    # ── Upgrade existing bare "Unknown Card (N)" placeholders with better data ──
    for grp_id, inst_data in upgradeable_real.items():
        name = generate_unknown_card_description(grp_id, inst_data)
        if name == f"Unknown Card ({grp_id})":
            continue
        type_line = build_type_line(inst_data) or None
        colors = inst_data.get("colors") or []
        power = inst_data.get("power")
        toughness = inst_data.get("toughness")
        Card.objects.filter(grp_id=grp_id, name=f"Unknown Card ({grp_id})").update(
            name=name,
            type_line=type_line,
            colors=[_COLOR_LABELS.get(c, c) for c in colors] if colors else [],
            power=str(power) if power is not None else None,
            toughness=str(toughness) if toughness is not None else None,
        )
        UnknownCard.objects.filter(card_id=grp_id, is_resolved=False).update(is_resolved=True)

    # ── Special objects: tokens/emblems get generated names; others try Scryfall ──
    for grp_id, inst_data in missing_special.items():
        obj_type = inst_data.get("type", "")
        source_grp_id = inst_data.get("source_grp_id")

        if obj_type in _TOKEN_OBJECT_TYPES:
            name = generate_token_name(inst_data)
            logger.debug(f"Inserting token grp_id={grp_id} as '{name}'")
            Card.objects.get_or_create(
                grp_id=grp_id,
                defaults={
                    "name": name,
                    "is_token": True,
                    "object_type": obj_type,
                    "source_grp_id": source_grp_id,
                },
            )
        else:
            # Adventure face, MDFC back, Room half, Omen, etc. — try Scryfall first
            card_data = scryfall.get_card_by_arena_id(grp_id)
            if card_data:
                Card.objects.get_or_create(
                    grp_id=grp_id,
                    defaults={
                        "name": card_data.get("name"),
                        "mana_cost": card_data.get("mana_cost"),
                        "cmc": card_data.get("cmc"),
                        "type_line": card_data.get("type_line"),
                        "colors": card_data.get("colors", []),
                        "color_identity": card_data.get("color_identity", []),
                        "set_code": card_data.get("set_code"),
                        "rarity": card_data.get("rarity"),
                        "oracle_text": card_data.get("oracle_text"),
                        "power": card_data.get("power"),
                        "toughness": card_data.get("toughness"),
                        "scryfall_id": card_data.get("scryfall_id"),
                        "image_uri": card_data.get("image_uri"),
                        "object_type": obj_type,
                    },
                )
            else:
                # For Omen back faces, try the front face (grpId - 1) for the real name.
                name = None
                effective_source = source_grp_id
                if obj_type == "GameObjectType_Omen":
                    front_data = scryfall.get_card_by_arena_id(grp_id - 1)
                    if front_data and " // " in (front_data.get("name") or ""):
                        name = front_data["name"].split(" // ")[1]
                        effective_source = grp_id - 1
                if name is None:
                    label = obj_type.replace("GameObjectType_", "") if obj_type else "Unknown"
                    name = f"[{label}] ({grp_id})"
                logger.debug(f"Inserting special object grp_id={grp_id} as '{name}'")
                Card.objects.get_or_create(
                    grp_id=grp_id,
                    defaults={
                        "name": name,
                        "object_type": obj_type,
                        "source_grp_id": effective_source,
                    },
                )


def _import_actions(match: Match, match_data: MatchData) -> None:
    """Import game actions for a match."""
    significant_types = {
        "ActionType_Cast",
        "ActionType_Play",
        "ActionType_Attack",
        "ActionType_Block",
        "ActionType_Activate",
        "ActionType_Activate_Mana",
        "ActionType_Resolution",
    }

    seen = set()
    actions_to_create = []

    for action in match_data.actions:
        key = (
            action.get("game_state_id"),
            action.get("action_type"),
            action.get("instance_id"),
        )

        action_type = action.get("action_type", "")
        if key in seen or action_type not in significant_types:
            continue
        seen.add(key)

        card_grp_id = action.get("card_grp_id")

        actions_to_create.append(
            GameAction(
                match=match,
                game_state_id=action.get("game_state_id"),
                turn_number=action.get("turn_number"),
                phase=action.get("phase"),
                step=action.get("step"),
                active_player_seat=action.get("active_player"),
                seat_id=action.get("seat_id"),
                action_type=action_type,
                instance_id=action.get("instance_id"),
                card_id=card_grp_id,
                ability_grp_id=action.get("ability_grp_id"),
                mana_cost=action.get("mana_cost"),
                timestamp_ms=action.get("timestamp"),
            )
        )

    if actions_to_create:
        GameAction.objects.bulk_create(actions_to_create)
        logger.debug(f"Created {len(actions_to_create)} game actions")


def _import_life_changes(match: Match, match_data: MatchData) -> None:
    """Import life total changes for a match."""
    prev_life = {}
    changes_to_create = []

    for lc in match_data.life_changes:
        seat_id = lc.get("seat_id")
        life_total = lc.get("life_total")

        if seat_id is None or life_total is None:
            logger.debug(
                f"Skipping life change with missing data: seat_id={seat_id}, life_total={life_total}"
            )
            continue

        change = None
        if seat_id in prev_life:
            change = life_total - prev_life[seat_id]
            if change == 0:
                continue

        prev_life[seat_id] = life_total

        try:
            changes_to_create.append(
                LifeChange(
                    match=match,
                    game_state_id=lc.get("game_state_id"),
                    turn_number=lc.get("turn_number"),
                    seat_id=seat_id,
                    life_total=life_total,
                    change_amount=change,  # Fixed: was 'change', should be 'change_amount'
                    source_instance_id=lc.get("source_instance_id"),
                )
            )
        except Exception as e:
            logger.error(f"Error creating LifeChange object: {e}, data: {lc}", exc_info=True)
            raise

    if changes_to_create:
        try:
            LifeChange.objects.bulk_create(changes_to_create)
            logger.debug(f"Created {len(changes_to_create)} life changes")
        except Exception as e:
            logger.error(f"Error bulk creating life changes: {e}", exc_info=True)
            raise


def _import_zone_transfers(match: Match, match_data: MatchData) -> None:
    """Import zone transfers (card movements) for a match."""
    # Pre-validate: only reference card_grp_ids that actually exist in the cards table.
    # Skipped object types (Ability, TriggerHolder, RevealedCard) are never inserted,
    # so their grpIds would violate the FK constraint.
    candidate_ids = {
        zt.get("card_grp_id") for zt in match_data.zone_transfers if zt.get("card_grp_id")
    }
    valid_card_ids = set(
        Card.objects.filter(grp_id__in=candidate_ids).values_list("grp_id", flat=True)
    )

    transfers_to_create = []

    for zt in match_data.zone_transfers:
        instance_id = zt.get("instance_id")
        from_zone = zt.get("from_zone")
        to_zone = zt.get("to_zone")

        if not instance_id or not from_zone or not to_zone:
            logger.debug(
                f"Skipping zone transfer with missing data: instance_id={instance_id}, from={from_zone}, to={to_zone}"
            )
            continue

        card_grp_id = zt.get("card_grp_id")
        if card_grp_id not in valid_card_ids:
            card_grp_id = None

        try:
            transfers_to_create.append(
                ZoneTransfer(
                    match=match,
                    game_state_id=zt.get("game_state_id"),
                    turn_number=zt.get("turn_number"),
                    instance_id=instance_id,
                    card_id=card_grp_id,
                    from_zone=from_zone,
                    to_zone=to_zone,
                    category=zt.get("category"),
                )
            )
        except Exception as e:
            logger.error(f"Error creating ZoneTransfer object: {e}, data: {zt}", exc_info=True)
            raise

    if transfers_to_create:
        try:
            ZoneTransfer.objects.bulk_create(transfers_to_create)
            logger.debug(f"Created {len(transfers_to_create)} zone transfers")
        except Exception as e:
            logger.error(f"Error bulk creating zone transfers: {e}", exc_info=True)
            raise

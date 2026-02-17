"""
Django views for MTG Arena Statistics.
"""

import os
import sys
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from pathlib import Path

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Avg, Count, Max, Q
from django.db.models.functions import TruncDate
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

# Add src to path for parser imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.parser.log_parser import MatchData, MTGALogParser
from src.services.scryfall import get_scryfall

from .models import Card, Deck, DeckCard, GameAction, ImportSession, LifeChange, Match, ZoneTransfer


def dashboard(request):
    """Main dashboard with overview statistics."""
    # Overall stats
    matches_with_results = Match.objects.filter(result__isnull=False)

    total_matches = matches_with_results.count()
    wins = matches_with_results.filter(result="win").count()
    losses = matches_with_results.filter(result="loss").count()

    total_games = wins + losses
    win_rate = round(wins / total_games * 100, 1) if total_games > 0 else 0

    avg_stats = matches_with_results.aggregate(
        avg_turns=Avg("total_turns"), avg_duration=Avg("duration_seconds")
    )

    overall_stats = {
        "total_matches": total_matches,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "avg_turns": round(avg_stats["avg_turns"] or 0, 1),
        "avg_duration": round(avg_stats["avg_duration"] or 0, 0),
    }

    # Check card data status
    scryfall = get_scryfall()
    try:
        card_stats = scryfall.stats()
        card_data_ready = card_stats["index_loaded"] and card_stats["total_cards"] > 0
        card_count = card_stats["total_cards"]
    except Exception:
        card_data_ready = False
        card_count = 0

    # Recent matches
    recent_matches = Match.objects.select_related("deck").order_by("-start_time")[:5]

    # Deck performance
    deck_stats = (
        Deck.objects.annotate(
            games=Count("matches", filter=Q(matches__result__isnull=False)),
            wins=Count("matches", filter=Q(matches__result="win")),
        )
        .filter(games__gt=0)
        .order_by("-games")[:10]
    )

    for deck in deck_stats:
        deck.win_rate = round(deck.wins / deck.games * 100, 1) if deck.games > 0 else 0

    # Performance by format
    format_stats = (
        Match.objects.filter(result__isnull=False, event_id__isnull=False)
        .values("event_id")
        .annotate(games=Count("id"), wins=Count("id", filter=Q(result="win")))
        .order_by("-games")
    )

    for fmt in format_stats:
        fmt["format"] = fmt["event_id"]
        fmt["win_rate"] = round(fmt["wins"] / fmt["games"] * 100, 1) if fmt["games"] > 0 else 0

    # Win rate over time (last 7 days)
    seven_days_ago = timezone.now() - timedelta(days=7)
    daily_stats = (
        Match.objects.filter(result__isnull=False, start_time__gte=seven_days_ago)
        .annotate(date=TruncDate("start_time"))
        .values("date")
        .annotate(games=Count("id"), wins=Count("id", filter=Q(result="win")))
        .order_by("date")
    )

    daily_stats_list = []
    for day in daily_stats:
        daily_stats_list.append(
            {
                "date": day["date"].strftime("%Y-%m-%d") if day["date"] else None,
                "games": day["games"],
                "wins": day["wins"],
                "win_rate": round(day["wins"] / day["games"] * 100, 1) if day["games"] > 0 else 0,
            }
        )

    return render(
        request,
        "dashboard.html",
        {
            "overall_stats": overall_stats,
            "recent_matches": recent_matches,
            "deck_stats": deck_stats,
            "format_stats": format_stats,
            "daily_stats": daily_stats_list,
            "card_data_ready": card_data_ready,
            "card_count": card_count,
        },
    )


def matches_list(request):
    """Match history page."""
    # Filter parameters
    deck_filter = request.GET.get("deck")
    result_filter = request.GET.get("result")
    format_filter = request.GET.get("format")

    matches = Match.objects.select_related("deck").order_by("-start_time")

    if deck_filter:
        matches = matches.filter(deck__name__icontains=deck_filter)
    if result_filter:
        matches = matches.filter(result=result_filter)
    if format_filter:
        matches = matches.filter(event_id=format_filter)

    # Pagination
    paginator = Paginator(matches, 20)
    page = request.GET.get("page", 1)
    matches_page = paginator.get_page(page)

    # Get available filters
    decks = Deck.objects.values_list("name", flat=True).distinct().order_by("name")
    formats = (
        Match.objects.exclude(event_id__isnull=True).values_list("event_id", flat=True).distinct()
    )

    return render(
        request,
        "matches.html",
        {
            "matches": matches_page,
            "page": matches_page,
            "total_pages": paginator.num_pages,
            "decks": decks,
            "formats": formats,
            "current_deck": deck_filter,
            "current_result": result_filter,
            "current_format": format_filter,
        },
    )


def match_detail(request, match_id):
    """Detailed match view with game replay."""
    match = get_object_or_404(Match.objects.select_related("deck"), pk=match_id)

    # Get actions with card names
    actions = match.actions.select_related("card").order_by("game_state_id", "id")

    # Get life changes
    life_changes = match.life_changes.order_by("game_state_id", "id")

    # Get zone transfers with card names
    zone_transfers = match.zone_transfers.select_related("card").order_by("game_state_id", "id")

    # Get deck cards
    deck_cards = []
    if match.deck:
        deck_cards = match.deck.deck_cards.select_related("card").order_by(
            "card__cmc", "card__name"
        )

    return render(
        request,
        "match_detail.html",
        {
            "match": match,
            "actions": actions,
            "life_changes": life_changes,
            "zone_transfers": zone_transfers,
            "deck_cards": deck_cards,
        },
    )


def decks_list(request):
    """Deck performance overview."""
    decks = Deck.objects.annotate(
        games=Count("matches", filter=Q(matches__result__isnull=False)),
        wins=Count("matches", filter=Q(matches__result="win")),
        avg_turns=Avg("matches__total_turns", filter=Q(matches__result__isnull=False)),
        last_played=Max("matches__start_time"),
    ).order_by("-last_played")

    for deck in decks:
        deck.win_rate = round(deck.wins / deck.games * 100, 1) if deck.games > 0 else 0

    return render(request, "decks.html", {"decks": decks})


def deck_detail(request, deck_id):
    """Detailed deck view."""
    deck = get_object_or_404(Deck, pk=deck_id)

    # Get deck cards grouped by type
    deck_cards = deck.deck_cards.select_related("card").order_by("card__cmc", "card__name")

    cards_by_type = {}
    mana_curve = {i: 0 for i in range(8)}
    color_counts = {}

    for dc in deck_cards:
        card = dc.card
        type_line = card.type_line or "Unknown"

        # Categorize by type
        if "Creature" in type_line:
            category = "Creatures"
        elif "Land" in type_line:
            category = "Lands"
        elif "Instant" in type_line or "Sorcery" in type_line:
            category = "Spells"
        elif "Artifact" in type_line:
            category = "Artifacts"
        elif "Enchantment" in type_line:
            category = "Enchantments"
        elif "Planeswalker" in type_line:
            category = "Planeswalkers"
        else:
            category = "Other"

        if category not in cards_by_type:
            cards_by_type[category] = []
        cards_by_type[category].append(
            {
                "quantity": dc.quantity,
                "card": card,
            }
        )

        # Mana curve (exclude lands)
        if "Land" not in type_line:
            cmc = int(card.cmc or 0)
            bucket = min(cmc, 7)
            mana_curve[bucket] += dc.quantity

        # Color distribution
        colors = card.colors or []
        for color in colors:
            color_counts[color] = color_counts.get(color, 0) + dc.quantity

    # Match stats
    stats = deck.matches.filter(result__isnull=False).aggregate(
        games=Count("id"),
        wins=Count("id", filter=Q(result="win")),
        avg_turns=Avg("total_turns"),
        avg_duration=Avg("duration_seconds"),
    )
    stats["win_rate"] = round(stats["wins"] / stats["games"] * 100, 1) if stats["games"] > 0 else 0

    # Matchup stats
    matchups = (
        deck.matches.filter(result__isnull=False, opponent_name__isnull=False)
        .values("opponent_name")
        .annotate(games=Count("id"), wins=Count("id", filter=Q(result="win")))
        .order_by("-games")[:10]
    )

    for m in matchups:
        m["win_rate"] = round(m["wins"] / m["games"] * 100, 1) if m["games"] > 0 else 0

    return render(
        request,
        "deck_detail.html",
        {
            "deck": deck,
            "cards_by_type": cards_by_type,
            "mana_curve": mana_curve,
            "color_counts": color_counts,
            "stats": stats,
            "matchups": matchups,
        },
    )


def import_log(request):
    """Import log file via web UI."""
    if request.method == "POST":
        # Check if file was uploaded
        log_file = request.FILES.get("log_file")
        force = request.POST.get("force") == "on"

        if not log_file:
            messages.error(request, "No log file uploaded.")
            return redirect("stats:import_log")

        # Save uploaded file temporarily
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False, suffix=".log") as tmp_file:
            for chunk in log_file.chunks():
                tmp_file.write(chunk)
            tmp_path = tmp_file.name

        try:
            # Get file info
            file_size = os.path.getsize(tmp_path)
            file_modified = datetime.fromtimestamp(os.path.getmtime(tmp_path), tz=dt_timezone.utc)

            # Create import session
            session = ImportSession.objects.create(
                log_file=log_file.name,
                file_size=file_size,
                file_modified=file_modified,
                status="running",
            )

            # Ensure card data is available
            scryfall = get_scryfall()
            scryfall.ensure_bulk_data()

            # Get existing match IDs to skip
            existing_match_ids = set()
            if not force:
                existing_match_ids = set(Match.objects.values_list("match_id", flat=True))

            # Parse log file
            parser = MTGALogParser(tmp_path)
            matches = parser.parse_matches()

            # Import matches
            imported_count = 0
            skipped_count = 0
            errors = []

            for match_data in matches:
                if not force and match_data.match_id in existing_match_ids:
                    skipped_count += 1
                    continue

                try:
                    _import_match(match_data, scryfall)
                    imported_count += 1
                except Exception as e:
                    errors.append(f"Match {match_data.match_id[:8]}: {str(e)}")
                    if len(errors) <= 5:  # Only store first 5 errors
                        continue

            # Update session
            session.matches_imported = imported_count
            session.matches_skipped = skipped_count
            session.status = "completed" if not errors else "completed_with_errors"
            session.completed_at = timezone.now()
            if errors:
                session.error_message = "; ".join(errors[:5])
            session.save()

            # Show success message
            if imported_count > 0:
                messages.success(
                    request,
                    f"Successfully imported {imported_count} matches (skipped {skipped_count}).",
                )
            else:
                messages.warning(
                    request, f"No new matches found. Skipped {skipped_count} existing matches."
                )

            if errors:
                messages.warning(request, f"Encountered {len(errors)} errors during import.")

        except Exception as e:
            if "session" in locals():
                session.status = "failed"
                session.error_message = str(e)
                session.save()
            messages.error(request, f"Import failed: {str(e)}")
        finally:
            # Clean up temp file
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        return redirect("stats:import_sessions")

    # GET request - show upload form
    return render(request, "import_log.html")


def card_data(request):
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

    # Get database card count
    db_card_count = Card.objects.count()

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
        },
    )


def import_sessions(request):
    """View import session history."""
    sessions = ImportSession.objects.order_by("-started_at")[:20]
    return render(request, "import_sessions.html", {"sessions": sessions})


def api_stats(request):
    """API endpoint for dashboard charts."""
    thirty_days_ago = timezone.now() - timedelta(days=30)

    daily_stats = (
        Match.objects.filter(result__isnull=False, start_time__gte=thirty_days_ago)
        .annotate(date=TruncDate("start_time"))
        .values("date")
        .annotate(games=Count("id"), wins=Count("id", filter=Q(result="win")))
        .order_by("date")
    )

    daily_data = []
    for day in daily_stats:
        daily_data.append(
            {
                "date": day["date"].strftime("%Y-%m-%d") if day["date"] else None,
                "games": day["games"],
                "wins": day["wins"],
                "win_rate": round(day["wins"] / day["games"] * 100, 1) if day["games"] > 0 else 0,
            }
        )

    return JsonResponse({"daily": daily_data})


# Helper functions for importing matches
@transaction.atomic
def _import_match(match_data: MatchData, scryfall):
    """Import a single match into the database."""
    # Ensure deck exists
    deck = None
    if match_data.deck_id:
        deck = _ensure_deck(match_data, scryfall)

    # Collect all unique card IDs and ensure they're in the cards table
    card_grp_ids = _collect_card_ids(match_data)
    _ensure_cards(card_grp_ids, scryfall)

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

    # Create match
    match = Match.objects.create(
        match_id=match_data.match_id,
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

    # Import actions, life changes, zone transfers
    _import_actions(match, match_data)
    _import_life_changes(match, match_data)
    _import_zone_transfers(match, match_data)

    return match


def _ensure_deck(match_data: MatchData, scryfall) -> Deck:
    """Ensure deck exists in database."""
    deck, created = Deck.objects.get_or_create(
        deck_id=match_data.deck_id,
        defaults={
            "name": match_data.deck_name or "Unknown Deck",
            "format": match_data.format,
        },
    )

    if created and match_data.deck_cards:
        # Ensure cards exist first
        card_ids = {c.get("cardId") for c in match_data.deck_cards if c.get("cardId")}
        _ensure_cards(card_ids, scryfall)

        # Add deck cards
        for card_data in match_data.deck_cards:
            card_id = card_data.get("cardId")
            quantity = card_data.get("quantity", 1)
            if card_id:
                try:
                    card = Card.objects.get(grp_id=card_id)
                    DeckCard.objects.create(
                        deck=deck, card=card, quantity=quantity, is_sideboard=False
                    )
                except Card.DoesNotExist:
                    pass

    return deck


def _collect_card_ids(match_data: MatchData):
    """Collect all unique card IDs from match data."""
    card_ids = set()

    for card in match_data.deck_cards:
        if card.get("cardId"):
            card_ids.add(card["cardId"])

    for inst_data in match_data.card_instances.values():
        if inst_data.get("grp_id"):
            card_ids.add(inst_data["grp_id"])

    for action in match_data.actions:
        if action.get("card_grp_id"):
            card_ids.add(action["card_grp_id"])

    return card_ids


def _ensure_cards(card_ids, scryfall):
    """Ensure cards exist in database from Scryfall bulk data."""
    if not card_ids:
        return

    existing_ids = set(Card.objects.filter(grp_id__in=card_ids).values_list("grp_id", flat=True))
    missing_ids = card_ids - existing_ids

    if missing_ids:
        card_lookup = scryfall.lookup_cards_batch(missing_ids)

        cards_to_create = []
        for grp_id, card_data in card_lookup.items():
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
                cards_to_create.append(Card(grp_id=grp_id, name=f"Unknown Card ({grp_id})"))

        Card.objects.bulk_create(cards_to_create, ignore_conflicts=True)


def _import_actions(match: Match, match_data: MatchData):
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

    GameAction.objects.bulk_create(actions_to_create)


def _import_life_changes(match: Match, match_data: MatchData):
    """Import life total changes for a match."""
    prev_life = {}
    changes_to_create = []

    for lc in match_data.life_changes:
        seat_id = lc.get("seat_id")
        life_total = lc.get("life_total")

        if seat_id is None or life_total is None:
            continue

        change = None
        if seat_id in prev_life:
            change = life_total - prev_life[seat_id]
            if change == 0:
                continue

        prev_life[seat_id] = life_total

        changes_to_create.append(
            LifeChange(
                match=match,
                game_state_id=lc.get("game_state_id"),
                turn_number=lc.get("turn_number"),
                seat_id=seat_id,
                life_total=life_total,
                change=change,
            )
        )

    LifeChange.objects.bulk_create(changes_to_create)


def _import_zone_transfers(match: Match, match_data: MatchData):
    """Import zone transfers (card movements) for a match."""
    transfers_to_create = []

    for zt in match_data.zone_transfers:
        card_grp_id = zt.get("card_grp_id")

        transfers_to_create.append(
            ZoneTransfer(
                match=match,
                game_state_id=zt.get("game_state_id"),
                turn_number=zt.get("turn_number"),
                instance_id=zt.get("instance_id"),
                card_id=card_grp_id,
                from_zone=zt.get("from_zone"),
                to_zone=zt.get("to_zone"),
                seat_id=zt.get("seat_id"),
            )
        )

    ZoneTransfer.objects.bulk_create(transfers_to_create)

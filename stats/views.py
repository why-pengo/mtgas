"""
Django views for MTG Arena Statistics.
"""

import json
import logging
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
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

logger = logging.getLogger(__name__)

# Add src to path for parser imports  # noqa: E402
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.parser.log_parser import MatchData, MTGALogParser  # noqa: E402
from src.services.import_service import (  # noqa: E402
    _SKIP_OBJECT_TYPES,
    _TOKEN_OBJECT_TYPES,
    generate_token_name,
)
from src.services.scryfall import ScryfallBulkService, get_scryfall  # noqa: E402

from .models import (  # noqa: E402
    Card,
    Deck,
    DeckCard,
    GameAction,
    ImportSession,
    LifeChange,
    Match,
    UnknownCard,
    ZoneTransfer,
)


def dashboard(request: HttpRequest) -> HttpResponse:
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


def matches_list(request: HttpRequest) -> HttpResponse:
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


def match_detail(request: HttpRequest, match_id: int) -> HttpResponse:
    """Detailed match view with game timeline."""
    match = get_object_or_404(Match.objects.select_related("deck"), pk=match_id)

    # Build timeline from zone transfers using the same logic as the replay view
    zone_transfers = list(
        match.zone_transfers.select_related("card").order_by("game_state_id", "id")
    )
    life_changes = list(match.life_changes.order_by("game_state_id", "id"))

    zone_labels = _build_zone_labels(zone_transfers)

    # Player's hand = Hand zone whose Library sends named (visible) draws
    player_hand_zone: str | None = next(
        (
            str(t.to_zone)
            for t in zone_transfers
            if zone_labels.get(str(t.from_zone)) == "Library"
            and zone_labels.get(str(t.to_zone)) == "Hand"
            and t.card_id is not None
        ),
        None,
    )

    # Build life events sorted by gsid for linear scan
    life_events = sorted(
        [(lc.game_state_id or 0, lc.seat_id, lc.life_total) for lc in life_changes],
        key=lambda x: x[0],
    )
    life_idx = 0
    current_life: dict[int, int] = {}
    timeline: list[dict] = []

    for zt in zone_transfers:
        if zt.card_id is None:
            continue

        gsid = zt.game_state_id or 0
        # Advance life changes up to (and including) current gsid
        while life_idx < len(life_events) and life_events[life_idx][0] <= gsid:
            _, seat, life = life_events[life_idx]
            current_life[seat] = life
            life_idx += 1

        # Token creation events use a synthetic category rather than zone labels
        if zt.category == "TokenCreated":
            timeline.append(
                {
                    "turn": zt.turn_number or 0,
                    "actor": None,
                    "verb": "token created",
                    "card": zt.card,
                    "life_you": current_life.get(match.player_seat_id, 20),
                    "life_opp": current_life.get(match.opponent_seat_id, 20),
                }
            )
            continue

        fz = str(zt.from_zone) if zt.from_zone is not None else ""
        tz = str(zt.to_zone) if zt.to_zone is not None else ""
        from_label = zone_labels.get(fz, f"Zone {fz}")
        to_label = zone_labels.get(tz, f"Zone {tz}")

        if fz == player_hand_zone or (from_label == "Hand" and player_hand_zone is None):
            actor = "you"
        elif from_label == "Hand":
            actor = "opponent"
        else:
            actor = None

        verb = _zone_verb(from_label, to_label, actor or "")
        if verb is None:
            continue

        timeline.append(
            {
                "turn": zt.turn_number or 0,
                "actor": actor,
                "verb": verb,
                "card": zt.card,
                "life_you": current_life.get(match.player_seat_id, 20),
                "life_opp": current_life.get(match.opponent_seat_id, 20),
            }
        )

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
            "timeline": timeline,
            "life_changes": life_changes,
            "deck_cards": deck_cards,
        },
    )


# Zone integer codes from MTGA protobuf schema (unused now — zones are inferred dynamically)


def _clean_phase(phase: str | None) -> str:
    if not phase:
        return ""
    for prefix in ("Phase_", "Step_"):
        if phase.startswith(prefix):
            return phase[len(prefix) :]
    return phase


def _build_zone_labels(zone_transfers: list) -> dict[str, str]:
    """
    Infer the role of each zone ID for a single match from zone transfer patterns.

    MTGA assigns dynamic integer zone IDs per match (e.g. 28, 35, 36) rather than
    using fixed enums. This function deduces which ID maps to Battlefield, Stack,
    Hand, Library, Graveyard, or Exile by analysing transfer statistics.

    Inference heuristics (applied in order):

    1. **Battlefield** — the zone with the highest *net* named-card accumulation
       (arrivals minus departures). Permanents enter and stay here.

    2. **Stack** — a high-throughput transit zone whose net is near zero.
       Spells arrive when cast and leave when they resolve or are countered.

    3. **Opponent's Library** (first Library) — identified by having the most
       *anonymous* (face-down) outflows. The opponent's draws are hidden, so
       cards leaving their library appear without a card reference.

    4. **Opponent's Hand** — the first unlabelled destination reached from the
       opponent's Library via a named-card transfer (when the card is eventually
       revealed after being drawn).

    5. **Player's Library** (second Library) — a zone with high named outflows
       that feeds a *single* destination. Library→Hand is a 1-to-1 pipeline;
       a Hand zone, by contrast, sends cards to multiple destinations (Stack,
       Battlefield, etc.).

    6. **All Hand zones** — any unlabelled zone that receives cards directly
       from a Library is marked as Hand (covers both players after step 5).

    7. **Graveyards** — zones that accumulate named cards (positive net) *and*
       receive cards from Battlefield or Stack (die/resolve triggers).

    8. **Exile** — residual low-traffic zones not matched above.

    Returns a dict mapping str(zone_id) -> role label string.
    """
    from collections import Counter

    named_arr: Counter = Counter()
    named_dep: Counter = Counter()
    anon_dep: Counter = Counter()

    for zt in zone_transfers:
        fz = str(zt.from_zone) if zt.from_zone is not None else None
        tz = str(zt.to_zone) if zt.to_zone is not None else None
        has_card = zt.card_id is not None
        if fz:
            (named_dep if has_card else anon_dep)[fz] += 1
        if tz and has_card:
            named_arr[tz] += 1

    all_zones = set(named_arr) | set(named_dep)
    net = {z: named_arr.get(z, 0) - named_dep.get(z, 0) for z in all_zones}
    labels: dict[str, str] = {}

    # 1. Battlefield: highest net accumulation of named cards
    if named_arr:
        battlefield = max(named_arr, key=lambda z: net.get(z, 0))
        labels[battlefield] = "Battlefield"

    # 2. Stack: near-zero net transit zone with meaningful throughput
    for z in sorted(named_arr, key=named_arr.get, reverse=True):
        if z not in labels and named_arr[z] >= 3 and abs(net.get(z, 0)) <= 3:
            labels[z] = "Stack"
            break

    # 3. Opponent's Library: most anonymous outflows (opponent draws are face-down)
    for z, _ in anon_dep.most_common():
        if z not in labels:
            labels[z] = "Library"
            break

    # 4. Opponent's Hand: first unlabelled destination from the opponent's Library
    lib_zone = next((z for z, l in labels.items() if l == "Library"), None)
    if lib_zone:
        for zt in zone_transfers:
            fz = str(zt.from_zone) if zt.from_zone is not None else None
            tz = str(zt.to_zone) if zt.to_zone is not None else None
            if fz == lib_zone and tz and tz not in labels and zt.card_id:
                labels[tz] = "Hand"
                break

    # 5. Player's Library: high named outflows going to a *single* destination.
    #    A Hand zone sends to many destinations (Stack, Battlefield…); a Library
    #    feeds only its paired Hand zone.
    from collections import Counter as _Counter

    for z in sorted(named_dep, key=named_dep.get, reverse=True):
        if z not in labels and named_dep[z] >= 3 and net.get(z, 0) <= -3:
            dest_count = _Counter(
                str(zt.to_zone) for zt in zone_transfers if str(zt.from_zone) == z and zt.card_id
            )
            if len(dest_count) == 1:  # single destination → library, not hand
                labels[z] = "Library"
                break

    # 6. Hand zones: any unlabelled destination reachable directly from a Library
    for zt in zone_transfers:
        fz = str(zt.from_zone) if zt.from_zone is not None else None
        tz = str(zt.to_zone) if zt.to_zone is not None else None
        if fz and labels.get(fz) == "Library" and tz and tz not in labels:
            labels[tz] = "Hand"

    # 7. Graveyards: accumulate named cards received from Battlefield or Stack
    battlefield_zone = next((z for z, l in labels.items() if l == "Battlefield"), None)
    stack_zone = next((z for z, l in labels.items() if l == "Stack"), None)
    for z in sorted(named_arr, key=named_arr.get, reverse=True):
        if z not in labels and net.get(z, 0) >= 1:
            receives_from_play = any(
                str(zt.from_zone) in (battlefield_zone, stack_zone) and str(zt.to_zone) == z
                for zt in zone_transfers
                if zt.card_id
            )
            if receives_from_play:
                labels[z] = "Graveyard"

    # 8. Exile: residual low-traffic zones
    for z in all_zones:
        if z not in labels and named_arr.get(z, 0) <= 2:
            labels[z] = "Exile"

    return labels


def _zone_verb(from_label: str, to_label: str, actor: str) -> str | None:
    """
    Map a (from_zone_role, to_zone_role) pair to a human-readable event verb.

    Returns None for transfers that should be skipped in the replay (e.g. cards
    entering the Stack from somewhere other than a Hand, or other internal moves
    that aren't meaningful to show).

    Actor is accepted but unused; it is available for callers that want to build
    richer descriptions (e.g. "You cast" vs "Opponent cast").
    """
    if to_label == "Battlefield":
        if from_label in ("Hand", "Stack"):
            return "entered the battlefield"
        if from_label == "Library":
            return "put onto the battlefield"
    if to_label == "Stack":
        if from_label == "Hand":
            return "cast"
        return None  # only show spells cast from hand
    if from_label == "Battlefield":
        if to_label == "Graveyard":
            return "died"
        if to_label == "Exile":
            return "was exiled"
        if to_label == "Hand":
            return "bounced to hand"
        if to_label == "Library":
            return "shuffled into library"
    if from_label == "Stack":
        if to_label == "Graveyard":
            return "resolved"
        if to_label == "Exile":
            return "was exiled"
    if from_label == "Library" and to_label == "Hand":
        return "drawn"
    return None  # skip all other transfers


def match_replay(request: HttpRequest, match_id: int) -> HttpResponse:
    """
    Step-through match replay page driven by ZoneTransfer records.

    ## Why ZoneTransfers, not GameActions?

    MTGA's ``greToClientEvent / GREMessageType_GameStateMessage`` messages embed
    an ``actions`` array that represents the **legal moves available** to the
    active player at that moment — not the moves that were actually made. Each
    game-state update re-broadcasts the full action menu, so the GameAction table
    ends up with thousands of duplicate rows (one set per game-state snapshot)
    and is dominated by mana-tap options (~75 % of rows are ActionType_Activate_Mana).

    ZoneTransfer records, by contrast, come from ``AnnotationType_ZoneTransfer``
    annotations which are only emitted when a card *physically moves* between
    zones. They represent real game events: draws, casts, spell resolution,
    creature deaths, etc.

    ## Zone ID resolution

    MTGA assigns per-match integer IDs to each zone instance (Library, Hand,
    Battlefield, Stack, Graveyard, Exile — one set per player, plus shared zones).
    These IDs are not fixed across matches, so ``_build_zone_labels()`` infers
    the role of each ID from statistical patterns in the transfer data.

    ## Actor attribution

    The player's Hand zone is identified as the Hand zone whose paired Library
    sends *named* (face-up) cards. The opponent's Library sends anonymous
    (face-down) transfers because the player cannot see the opponent's draws.
    Any transfer originating from the player's Hand zone is attributed to "You";
    transfers from the opponent's Hand zone are attributed to "Opponent".
    Transfers from Battlefield or Stack (e.g. creature deaths, spell resolution)
    use "—" because ownership cannot be reliably determined from zone data alone.

    ## Life totals

    Life changes are stored separately (LifeChange model, ordered by
    game_state_id). A running dict is updated as each game-state boundary is
    crossed so that each replay step shows the life totals current at that point.

    ## Steps serialised to JSON

    Each step dict contains: turn, actor, action (verb), card_name, card_image
    (cached local path), card_fallback (Scryfall image_uri), life_you, life_opp,
    description. The template embeds this as a JS array and drives the UI.
    """
    match = get_object_or_404(Match.objects.select_related("deck"), pk=match_id)

    zone_transfers = list(
        match.zone_transfers.select_related("card").order_by("game_state_id", "id")
    )
    life_changes = list(match.life_changes.order_by("game_state_id", "id"))

    # Infer zone roles for this match
    zone_labels = _build_zone_labels(zone_transfers)

    # Determine player's hand zone once: the Hand zone whose Library sends NAMED cards (visible draws)
    player_hand_zone: str | None = next(
        (
            str(t.to_zone)
            for t in zone_transfers
            if zone_labels.get(str(t.from_zone)) == "Library"
            and zone_labels.get(str(t.to_zone)) == "Hand"
            and t.card_id is not None
        ),
        None,
    )

    # Build life events sorted by gsid for linear scan
    life_events = sorted(
        [(lc.game_state_id or 0, lc.seat_id, lc.life_total) for lc in life_changes],
        key=lambda x: x[0],
    )
    life_idx = 0
    current_life: dict[int, int] = {}
    steps = []

    for zt in zone_transfers:
        if zt.card_id is None:
            continue  # skip anonymous transfers

        gsid = zt.game_state_id or 0
        # Advance life changes up to (and including) current gsid
        while life_idx < len(life_events) and life_events[life_idx][0] <= gsid:
            _, seat, life = life_events[life_idx]
            current_life[seat] = life
            life_idx += 1

        card = zt.card
        card_name = card.name if card else None
        card_image = f"/static/card_images/{card.grp_id}.jpg" if card else None
        card_fallback = card.image_uri if card else None

        # Token creation events use a synthetic category rather than zone labels
        if zt.category == "TokenCreated":
            description = f"Token created: {card_name}" if card_name else "Token created"
            steps.append(
                {
                    "turn": zt.turn_number,
                    "phase": "",
                    "actor": "—",
                    "action": "token created",
                    "card_name": card_name,
                    "card_image": card_image,
                    "card_fallback": card_fallback or "",
                    "life_you": current_life.get(match.player_seat_id, 20),
                    "life_opp": current_life.get(match.opponent_seat_id, 20),
                    "description": description,
                    "is_token": True,
                }
            )
            continue

        fz = str(zt.from_zone) if zt.from_zone is not None else ""
        tz = str(zt.to_zone) if zt.to_zone is not None else ""
        from_label = zone_labels.get(fz, f"Zone {fz}")
        to_label = zone_labels.get(tz, f"Zone {tz}")

        if fz == player_hand_zone or (from_label == "Hand" and player_hand_zone is None):
            actor = "You"
        elif from_label == "Hand":
            actor = "Opponent"
        elif from_label in ("Battlefield", "Stack"):
            actor = "—"
        else:
            actor = "—"

        verb = _zone_verb(from_label, to_label, actor)
        if verb is None:
            continue  # skip this transfer

        if actor == "—":
            description = f"{verb}: {card_name}" if card_name else verb
        else:
            description = f"{actor} — {verb}: {card_name}" if card_name else f"{actor} — {verb}"

        steps.append(
            {
                "turn": zt.turn_number,
                "phase": "",
                "actor": actor,
                "action": verb,
                "card_name": card_name,
                "card_image": card_image,
                "card_fallback": card_fallback or "",
                "life_you": current_life.get(match.player_seat_id, 20),
                "life_opp": current_life.get(match.opponent_seat_id, 20),
                "description": description,
                "is_token": card.is_token if card else False,
            }
        )

    return render(
        request,
        "match_replay.html",
        {
            "match": match,
            "steps_json": json.dumps(steps),
            "total_steps": len(steps),
        },
    )


def decks_list(request: HttpRequest) -> HttpResponse:
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


def deck_detail(request: HttpRequest, deck_id: int) -> HttpResponse:
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

    # Count unknown cards in this deck
    unknown_cards_count = UnknownCard.objects.filter(deck=deck, is_resolved=False).count()

    total_cards = sum(dc.quantity for dc in deck_cards)
    total_lands = sum(dc.quantity for dc in deck_cards if "Land" in (dc.card.type_line or ""))
    land_pct = round(total_lands / total_cards * 100, 1) if total_cards > 0 else 0
    suggested_lands = round(total_cards * 17 / 40)

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
            "unknown_cards_count": unknown_cards_count,
            "total_cards": total_cards,
            "total_lands": total_lands,
            "land_pct": land_pct,
            "suggested_lands": suggested_lands,
        },
    )


def deck_gallery(request: HttpRequest, deck_id: int) -> HttpResponse:
    """Card gallery view with Scryfall images."""
    deck = get_object_or_404(Deck, pk=deck_id)
    scryfall = get_scryfall()

    # Handle image download request
    if request.method == "POST" and request.POST.get("action") == "download_images":
        deck_cards = deck.deck_cards.select_related("card").all()
        downloaded = 0
        failed = 0

        for dc in deck_cards:
            result = scryfall.download_card_image(dc.card.grp_id)
            if result:
                downloaded += 1
            else:
                failed += 1

        if downloaded > 0:
            messages.success(request, f"Downloaded {downloaded} card images.")
        if failed > 0:
            messages.warning(request, f"Failed to download {failed} images.")

        return redirect("stats:deck_gallery", deck_id=deck_id)

    # Get deck cards grouped by category
    deck_cards = deck.deck_cards.select_related("card").order_by("card__cmc", "card__name")

    cards_by_type = {}
    images_cached = 0
    total_cards = 0

    for dc in deck_cards:
        card = dc.card
        type_line = card.type_line or "Unknown"
        total_cards += dc.quantity

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

        # Check if image is cached
        image_cached = scryfall.get_cached_image_path(card.grp_id) is not None
        if image_cached:
            images_cached += 1

        if category not in cards_by_type:
            cards_by_type[category] = []

        cards_by_type[category].append(
            {
                "quantity": dc.quantity,
                "card": card,
                "image_cached": image_cached,
                "image_url": card.image_uri,
            }
        )

    # Calculate cache status
    unique_cards = len(deck_cards)
    cache_percentage = round(images_cached / unique_cards * 100) if unique_cards > 0 else 0

    return render(
        request,
        "deck_gallery.html",
        {
            "deck": deck,
            "cards_by_type": cards_by_type,
            "images_cached": images_cached,
            "unique_cards": unique_cards,
            "total_cards": total_cards,
            "cache_percentage": cache_percentage,
        },
    )


def import_log(request: HttpRequest) -> HttpResponse:
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

            logger.info(f"Starting import from uploaded file: {log_file.name} ({file_size} bytes)")

            # Create import session
            session = ImportSession.objects.create(
                log_file=log_file.name,
                file_size=file_size,
                file_modified=file_modified,
                status="running",
            )
            logger.info(f"Created import session: {session.id}")

            # Ensure card data is available
            scryfall = get_scryfall()
            scryfall.ensure_bulk_data()
            logger.info("Card data ready")

            # Get existing match IDs to skip
            existing_match_ids = set()
            if not force:
                existing_match_ids = set(Match.objects.values_list("match_id", flat=True))
                logger.info(f"Found {len(existing_match_ids)} existing matches in database")

            # Parse log file
            logger.info("Parsing log file...")
            parser = MTGALogParser(tmp_path)
            matches = parser.parse_matches()

            # Import matches
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
                    if len(errors) <= 5:  # Only store first 5 errors
                        continue

            logger.info(
                f"Import complete: {imported_count} imported, {skipped_count} skipped, {len(errors)} errors"
            )

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
                for error in errors[:3]:  # Show first 3 errors
                    messages.error(request, error)

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


def import_sessions(request: HttpRequest) -> HttpResponse:
    """View import session history."""
    sessions = ImportSession.objects.order_by("-started_at")[:20]
    return render(request, "import_sessions.html", {"sessions": sessions})


def api_stats(request: HttpRequest) -> JsonResponse:
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
def _import_match(
    match_data: MatchData, scryfall: ScryfallBulkService, import_session: ImportSession
) -> Match:
    """Import a single match into the database."""
    match_id = match_data.match_id
    logger.debug(f"[{match_id}] Starting import")

    # Ensure deck exists
    deck = None
    if match_data.deck_id:
        logger.debug(f"[{match_id}] Processing deck: {match_data.deck_id}")
        deck = _ensure_deck(match_data, scryfall, import_session)
        logger.debug(f"[{match_id}] Deck ready: {deck.name}")

    # Collect all unique card IDs and ensure they're in the cards table
    logger.debug(f"[{match_id}] Collecting card IDs")
    real_card_ids, special_objects = _collect_card_ids(match_data)
    logger.debug(
        f"[{match_id}] Found {len(real_card_ids)} real cards, {len(special_objects)} special objects"
    )

    # Create Match object first so we can reference it for unknown cards
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

    # Now ensure cards exist, passing match, deck, and session for unknown card tracking
    _ensure_cards(real_card_ids, special_objects, scryfall, import_session, match, deck, match_data)

    # Import actions, life changes, zone transfers
    logger.debug(f"[{match_id}] Importing game actions")
    _import_actions(match, match_data)

    logger.debug(f"[{match_id}] Importing life changes")
    _import_life_changes(match, match_data)

    logger.debug(f"[{match_id}] Importing zone transfers")
    _import_zone_transfers(match, match_data)

    logger.info(f"[{match_id}] Import complete")
    return match


def _ensure_deck(
    match_data: MatchData, scryfall: ScryfallBulkService, import_session: ImportSession
) -> Deck:
    """Ensure deck exists in database."""
    deck_id = match_data.deck_id
    logger.debug(f"Looking up deck: {deck_id}")

    deck, created = Deck.objects.get_or_create(
        deck_id=match_data.deck_id,
        defaults={
            "name": match_data.deck_name or "Unknown Deck",
            "format": match_data.format,
        },
    )

    if created:
        logger.info(f"Created new deck: {deck.name} ({deck_id})")

        if match_data.deck_cards:
            logger.debug(f"Adding {len(match_data.deck_cards)} cards to deck")
            # Ensure cards exist first — deck cards are all real cards
            card_ids = {c.get("cardId") for c in match_data.deck_cards if c.get("cardId")}
            # For deck creation, we don't have a match yet, so pass None
            _ensure_cards(card_ids, {}, scryfall, import_session, None, deck, match_data)

            # Add deck cards
            cards_added = 0
            for card_data in match_data.deck_cards:
                card_id = card_data.get("cardId")
                quantity = card_data.get("quantity", 1)
                if card_id:
                    try:
                        card = Card.objects.get(grp_id=card_id)
                        DeckCard.objects.create(
                            deck=deck, card=card, quantity=quantity, is_sideboard=False
                        )
                        cards_added += 1
                    except Card.DoesNotExist:
                        logger.warning(f"Card {card_id} not found in database")
                        pass
            logger.debug(f"Added {cards_added} cards to deck {deck.name}")
    else:
        logger.debug(f"Using existing deck: {deck.name}")

    return deck


def _collect_card_ids(match_data: MatchData) -> tuple[set[int], dict[int, dict]]:
    """Collect card IDs from match data.

    Returns:
        real_card_ids: grpIds that should be looked up in Scryfall.
        special_objects: grpId → instance_data for tokens, emblems, and card-face
            types (Adventure, MDFCBack, etc.) that are not standard cards.
    """
    real_card_ids: set[int] = set()
    special_objects: dict[int, dict] = {}

    # Deck cards are always real cards
    for card in match_data.deck_cards:
        if card.get("cardId"):
            real_card_ids.add(card["cardId"])

    # Categorise each card instance by its Arena object type
    for inst_data in match_data.card_instances.values():
        grp_id = inst_data.get("grp_id")
        obj_type = inst_data.get("type", "")
        if not grp_id:
            continue
        if obj_type in _SKIP_OBJECT_TYPES:
            continue  # Engine-only objects — never store in DB
        if obj_type == "GameObjectType_Card":
            real_card_ids.add(grp_id)
            special_objects.pop(grp_id, None)
        elif obj_type == "GameObjectType_Omen":
            # Omen back-face grpIds share their Arena ID with the front-face
            # GameObjectType_Card (the spell being cast). Card is processed first,
            # so we must override: back-face IDs are not in Scryfall.
            real_card_ids.discard(grp_id)
            special_objects[grp_id] = inst_data
        elif grp_id not in real_card_ids:
            special_objects.setdefault(grp_id, inst_data)

    # Actions may reference grpIds not captured as card instances
    for action in match_data.actions:
        cid = action.get("card_grp_id")
        if cid and cid not in special_objects:
            real_card_ids.add(cid)

    return real_card_ids, special_objects


def _ensure_cards(
    real_card_ids: set[int],
    special_objects: dict[int, dict],
    scryfall: ScryfallBulkService,
    import_session: ImportSession,
    match: Match | None = None,
    deck: Deck | None = None,
    match_data: MatchData | None = None,
) -> None:
    """Ensure cards/objects exist in the database.

    * real_card_ids: looked up in Scryfall; Unknown Card fallback + UnknownCard log.
    * special_objects: tokens/emblems get a generated name; other face types try
      Scryfall first and use a descriptive placeholder on failure.
    """
    all_ids = real_card_ids | set(special_objects)
    if not all_ids:
        return

    existing_ids = set(Card.objects.filter(grp_id__in=all_ids).values_list("grp_id", flat=True))
    missing_real = real_card_ids - existing_ids
    missing_special = {gid: d for gid, d in special_objects.items() if gid not in existing_ids}

    if missing_real or missing_special:
        logger.debug(
            f"Looking up {len(missing_real)} cards from Scryfall, "
            f"processing {len(missing_special)} special objects"
        )

    # ── Real cards: Scryfall lookup with Unknown Card fallback ──
    if missing_real:
        card_lookup = scryfall.lookup_cards_batch(missing_real)
        cards_to_create = []
        unknown_cards_to_log = []

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
                context_info: dict = {
                    "grp_id": grp_id,
                    "import_session_id": import_session.id,
                    "match_id": match.match_id if match else None,
                    "deck_id": deck.deck_id if deck else None,
                    "deck_name": deck.name if deck else None,
                }
                if match_data and match_data.card_instances:
                    instances_found = [
                        {
                            "instance_id": iid,
                            "name": ci.get("name"),
                            "type": ci.get("type"),
                            "card_types": ci.get("card_types", []),
                            "subtypes": ci.get("subtypes", []),
                            "colors": ci.get("colors", []),
                            "power": ci.get("power"),
                            "toughness": ci.get("toughness"),
                            "owner_seat": ci.get("owner_seat"),
                        }
                        for iid, ci in match_data.card_instances.items()
                        if ci.get("grp_id") == grp_id
                    ]
                    if instances_found:
                        context_info["card_instances"] = instances_found
                        first = instances_found[0]
                        if first.get("name"):
                            context_info["arena_name"] = first["name"]
                        if first.get("type"):
                            context_info["arena_type"] = first["type"]
                        if first.get("card_types"):
                            context_info["arena_card_types"] = first["card_types"]

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

                logger.info(
                    f"Unknown card discovered - grp_id: {grp_id}, "
                    f"deck: {card_deck.name if card_deck else 'N/A'}, "
                    f"match: {match.match_id[:8] if match else 'N/A'}"
                )
                cards_to_create.append(Card(grp_id=grp_id, name=f"Unknown Card ({grp_id})"))
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


def unknown_cards_list(request: HttpRequest) -> HttpResponse:
    """List all unknown cards discovered during imports."""
    # Get filter parameters
    deck_id = request.GET.get("deck_id")
    session_id = request.GET.get("session_id")
    show_resolved = request.GET.get("show_resolved", "false") == "true"

    # Base query for unresolved unknown cards
    unknown_cards = UnknownCard.objects.select_related(
        "card", "deck", "match", "import_session"
    ).order_by("-created_at")

    if not show_resolved:
        unknown_cards = unknown_cards.filter(is_resolved=False)

    if deck_id:
        unknown_cards = unknown_cards.filter(deck_id=deck_id)

    if session_id:
        unknown_cards = unknown_cards.filter(import_session_id=session_id)

    unique_unknown = {}
    for uc in unknown_cards:
        grp_id = uc.card.grp_id
        if grp_id not in unique_unknown:
            unique_unknown[grp_id] = {
                "card": uc.card,
                "occurrences": [],
                "deck_names": set(),
                "total_count": 0,
            }
        unique_unknown[grp_id]["occurrences"].append(uc)
        unique_unknown[grp_id]["total_count"] += 1
        if uc.deck:
            unique_unknown[grp_id]["deck_names"].add(uc.deck.name)

    # Convert to list for template
    unknown_list = []
    for data in unique_unknown.values():
        data["deck_names"] = ", ".join(sorted(data["deck_names"]))
        unknown_list.append(data)

    # Sort by count (most occurrences first)
    unknown_list.sort(key=lambda x: x["total_count"], reverse=True)

    # Get counts for display
    total_unresolved = UnknownCard.objects.filter(is_resolved=False).count()
    total_resolved = UnknownCard.objects.filter(is_resolved=True).count()

    context = {
        "unknown_list": unknown_list,
        "total_unresolved": total_unresolved,
        "total_resolved": total_resolved,
        "show_resolved": show_resolved,
        "deck_filter": deck_id,
        "session_filter": session_id,
    }

    return render(request, "unknown_cards_list.html", context)


def unknown_card_fix(request: HttpRequest, grp_id: int) -> HttpResponse:
    """Fix an unknown card by providing its correct name."""
    card = get_object_or_404(Card, grp_id=grp_id)

    # Get all UnknownCard records for this grp_id
    unknown_records = UnknownCard.objects.filter(card=card, is_resolved=False).select_related(
        "deck", "match", "import_session"
    )

    if request.method == "POST":
        new_name = request.POST.get("card_name", "").strip()

        if not new_name:
            messages.error(request, "Card name cannot be empty")
        else:
            # Update card name
            card.name = new_name
            card.save()

            # Mark all unknown records as resolved
            count = unknown_records.update(is_resolved=True, resolved_at=timezone.now())

            messages.success(
                request, f"Updated card {grp_id} to '{new_name}' ({count} occurrences resolved)"
            )
            return redirect("stats:unknown_cards_list")

    # Collect unique raw data for debugging
    raw_data_samples = []
    seen_data = set()
    for uc in unknown_records[:10]:  # Limit to first 10 samples
        if uc.raw_data:
            # Use JSON string as key for deduplication
            import json

            data_key = json.dumps(uc.raw_data, sort_keys=True)
            if data_key not in seen_data:
                seen_data.add(data_key)
                # Format JSON for display
                raw_data_samples.append(json.dumps(uc.raw_data, indent=2, sort_keys=True))

    context = {
        "card": card,
        "unknown_records": unknown_records,
        "occurrence_count": unknown_records.count(),
        "raw_data_samples": raw_data_samples,
    }

    return render(request, "unknown_card_fix.html", context)

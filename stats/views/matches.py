"""
Match list, detail, and replay views.
"""

import json
import logging

from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from ..models import Deck, Match
from ..utils.zone_utils import build_zone_labels, get_player_hand_zone, zone_verb

logger = logging.getLogger("stats.views")


def matches_list(request: HttpRequest) -> HttpResponse:
    """Match history page."""
    # Filter parameters
    deck_filter = request.GET.get("deck")
    result_filter = request.GET.get("result")
    format_filter = request.GET.get("format")

    # Sort parameters
    _SORT_FIELDS = {
        "date": "start_time",
        "result": "result",
        "opponent": "opponent_name",
        "deck": "deck__name",
        "format": "event_id",
        "turns": "total_turns",
        "duration": "duration_seconds",
    }
    sort_col = request.GET.get("sort", "date")
    sort_dir = request.GET.get("dir", "desc")
    if sort_col not in _SORT_FIELDS:
        sort_col = "date"
    if sort_dir not in ("asc", "desc"):
        sort_dir = "desc"

    field = _SORT_FIELDS[sort_col]
    order_prefix = "" if sort_dir == "asc" else "-"
    matches = Match.objects.select_related("deck").order_by(f"{order_prefix}{field}", "-start_time")

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
            "current_sort": sort_col,
            "current_dir": sort_dir,
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

    zone_labels = build_zone_labels(zone_transfers)

    # Player's hand = Hand zone whose Library sends named (visible) draws
    player_hand_zone: str | None = get_player_hand_zone(zone_transfers, zone_labels)

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

        verb = zone_verb(from_label, to_label, actor or "")
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

    # Get deck cards from this match's specific snapshot
    deck_cards = []
    try:
        snapshot = match.snapshot
        if snapshot:
            deck_cards = snapshot.cards.select_related("card").order_by("card__cmc", "card__name")
    except Exception:
        pass

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


def _clean_phase(phase: str | None) -> str:
    if not phase:
        return ""
    for prefix in ("Phase_", "Step_"):
        if phase.startswith(prefix):
            return phase[len(prefix) :]
    return phase


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
    These IDs are not fixed across matches, so ``build_zone_labels()`` infers
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
    zone_labels = build_zone_labels(zone_transfers)

    # Determine player's hand zone once: the Hand zone whose Library sends NAMED cards (visible draws)
    player_hand_zone: str | None = get_player_hand_zone(zone_transfers, zone_labels)

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

        verb = zone_verb(from_label, to_label, actor)
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


def match_analysis(request: HttpRequest, match_id: int) -> HttpResponse:
    """
    Per-match play analysis page.

    Runs the PlayAdvisor engine against the match's GameAction and ZoneTransfer
    data to surface mana-efficiency, ordering, missed-play, and alternate-play
    suggestions for every player turn.
    """
    from src.services.play_advisor import PlayAdvisor

    match = get_object_or_404(Match.objects.select_related("deck", "snapshot"), pk=match_id)
    analysis = PlayAdvisor(match).analyze()

    # Only pass turns that belong to the player and have data worth showing
    player_turns = [t for t in analysis.turns if t.is_player_turn]
    flagged_turns = [t for t in player_turns if t.suggestions]

    return render(
        request,
        "match_analysis.html",
        {
            "match": match,
            "analysis": analysis,
            "player_turns": player_turns,
            "flagged_turns": flagged_turns,
        },
    )

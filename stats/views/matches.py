"""
Match list, detail, and replay views.
"""

import json
import logging

from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from ..models import Deck, Match

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

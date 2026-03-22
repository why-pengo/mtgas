"""
Zone label inference and verb mapping utilities for MTGA zone transfer analysis.

MTGA assigns per-match integer IDs to each zone instance (Library, Hand, Battlefield,
Stack, Graveyard, Exile — one set per player). These IDs are not fixed across matches.
This module provides shared helpers used by both the match replay view and the play
advisor service.
"""

from collections import Counter


def build_zone_labels(zone_transfers: list) -> dict[str, str]:
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

    Args:
        zone_transfers: List of ZoneTransfer ORM objects (with from_zone, to_zone, card_id).

    Returns:
        Dict mapping str(zone_id) -> role label string.
    """
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
    for z in sorted(named_dep, key=named_dep.get, reverse=True):
        if z not in labels and named_dep[z] >= 3 and net.get(z, 0) <= -3:
            dest_count = Counter(
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


def get_player_hand_zone(zone_transfers: list, zone_labels: dict[str, str]) -> str | None:
    """
    Identify the zone ID of the player's hand.

    The player's hand is the Hand zone whose paired Library sends *named* (face-up)
    cards. The opponent's Library sends anonymous (face-down) transfers because the
    player cannot see the opponent's draws.

    Args:
        zone_transfers: List of ZoneTransfer ORM objects.
        zone_labels: Dict of zone_id -> role label (from build_zone_labels).

    Returns:
        str zone ID for the player's hand, or None if it cannot be determined.
    """
    return next(
        (
            str(t.to_zone)
            for t in zone_transfers
            if zone_labels.get(str(t.from_zone)) == "Library"
            and zone_labels.get(str(t.to_zone)) == "Hand"
            and t.card_id is not None
        ),
        None,
    )


def zone_verb(from_label: str, to_label: str, actor: str) -> str | None:
    """
    Map a (from_zone_role, to_zone_role) pair to a human-readable event verb.

    Returns None for transfers that should be skipped in the replay (e.g. cards
    entering the Stack from somewhere other than a Hand, or other internal moves
    that aren't meaningful to show).

    Args:
        from_label: Role label of the source zone (e.g. "Hand", "Battlefield").
        to_label: Role label of the destination zone.
        actor: "You", "Opponent", or "—" (unused in mapping, available for callers).

    Returns:
        Human-readable verb string, or None to skip this transfer.
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

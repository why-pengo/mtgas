"""
Deck list, detail, gallery, and history views.
"""

import logging
import re
from typing import Any

from django.contrib import messages
from django.db.models import Avg, Count, Max, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from src.services.scryfall import get_scryfall

from ..deck_diff import compute_deck_diff
from ..models import Deck, DeckCard, DeckSnapshot, UnknownCard

logger = logging.getLogger("stats.views")

_COLOR_NAMES = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}


def _parse_color_pips(mana_cost: str) -> dict[str, float]:
    """Count colored pip requirements from a mana cost string like '{2}{W}{U}'."""
    pips: dict[str, float] = {"W": 0.0, "U": 0.0, "B": 0.0, "R": 0.0, "G": 0.0}
    for symbol in re.findall(r"\{([^}]+)\}", mana_cost or ""):
        if "/" in symbol:
            # Hybrid mana e.g. {W/U} — split cost evenly between the two colors
            parts = [p for p in symbol.split("/") if p in pips]
            if parts:
                share = 1.0 / len(parts)
                for part in parts:
                    pips[part] += share
        elif symbol in pips:
            pips[symbol] += 1.0
    return pips


def _compute_deck_suggestions(
    deck_cards: list,
    mana_curve: dict[int, int],
    color_counts: dict[str, int],
    total_cards: int,
    total_lands: int,
    suggested_lands: int,
) -> dict[str, Any]:
    """Compute deck analysis metrics and improvement suggestions.

    Returns a dict with avg_cmc, curve_shape, pip data, copy-count distribution,
    and a list of suggestion dicts each containing category/severity/title/body.
    """
    suggestions: list[dict[str, str]] = []

    # ── Avg CMC & curve shape ────────────────────────────────────────────────
    total_pips = sum(cmc * count for cmc, count in mana_curve.items())
    non_land_count = total_cards - total_lands
    avg_cmc = round(total_pips / non_land_count, 2) if non_land_count > 0 else 0.0

    if avg_cmc < 2.0:
        curve_shape = "Aggro"
    elif avg_cmc < 3.2:
        curve_shape = "Midrange"
    elif avg_cmc < 4.5:
        curve_shape = "Control"
    else:
        curve_shape = "Ramp"

    # ── Mana curve suggestions ───────────────────────────────────────────────
    heavy_cards = sum(mana_curve.get(cmc, 0) for cmc in [5, 6, 7])
    heavy_warn = 4 if total_cards <= 40 else 6
    heavy_danger = 6 if total_cards <= 40 else 9

    if heavy_cards >= heavy_danger:
        suggestions.append(
            {
                "category": "Mana Curve",
                "severity": "danger",
                "title": f"{heavy_cards} cards with CMC 5+",
                "body": (
                    f"Your deck has {heavy_cards} cards costing 5 or more mana. "
                    "This many expensive cards will lead to slow starts and unplayable opening hands. "
                    "Consider cutting some high-CMC cards for lower-cost threats or interaction."
                ),
            }
        )
    elif heavy_cards >= heavy_warn:
        suggestions.append(
            {
                "category": "Mana Curve",
                "severity": "warning",
                "title": f"{heavy_cards} cards with CMC 5+",
                "body": (
                    f"Your deck has {heavy_cards} cards costing 5+ mana, which is on the high end. "
                    "Ensure your mana base supports these expensive spells "
                    "and that you have enough early plays to survive until you can cast them."
                ),
            }
        )

    # Gap detection: flag any missing CMC slot between 1 and the deck's peak CMC
    peak_cmc = max((cmc for cmc, count in mana_curve.items() if count > 0), default=0)
    for cmc in range(1, min(peak_cmc, 7)):
        if mana_curve.get(cmc, 0) == 0:
            suggestions.append(
                {
                    "category": "Mana Curve",
                    "severity": "info",
                    "title": f"No {cmc}-mana plays",
                    "body": (
                        f"Your deck has no cards with a mana value of {cmc}, creating a gap in your curve. "
                        f"This means you may have nothing meaningful to do on turn {cmc}. "
                        "Consider adding cards at this CMC to keep pressure on your opponent."
                    ),
                }
            )

    if curve_shape == "Aggro" and mana_curve.get(1, 0) == 0:
        suggestions.append(
            {
                "category": "Mana Curve",
                "severity": "warning",
                "title": "No 1-mana plays in an aggro-shaped deck",
                "body": (
                    f"Your deck has an average CMC of {avg_cmc} (Aggro range) "
                    "but no 1-mana cards. Aggro decks rely on early threats to apply pressure. "
                    "Adding 1-drops will make your deck faster and more consistent."
                ),
            }
        )

    # ── Land count suggestions ───────────────────────────────────────────────
    land_diff = total_lands - suggested_lands
    if abs(land_diff) >= 4:
        direction = "too many" if land_diff > 0 else "too few"
        fix_note = (
            "Cutting some lands will increase your threat density."
            if land_diff > 0
            else "Adding more lands will reduce the risk of mana screw."
        )
        suggestions.append(
            {
                "category": "Lands",
                "severity": "danger",
                "title": f"Significant land imbalance: {total_lands} lands (suggested {suggested_lands})",
                "body": (
                    f"Your {total_cards}-card deck has {total_lands} lands but the formula suggests "
                    f"{suggested_lands}. You have {direction} by {abs(land_diff)}. "
                    f"{fix_note} Adjust your land count based on your average CMC."
                ),
            }
        )
    elif abs(land_diff) >= 2:
        more_or_fewer = "more" if land_diff > 0 else "fewer"
        extra_note = (
            "Extra lands reduce your spell density."
            if land_diff > 0
            else "Fewer lands increases the risk of mana screw, especially with a higher average CMC."
        )
        suggestions.append(
            {
                "category": "Lands",
                "severity": "warning",
                "title": (
                    f"{abs(land_diff)} {more_or_fewer} lands than suggested "
                    f"({total_lands} vs {suggested_lands})"
                ),
                "body": (
                    f"The standard formula suggests {suggested_lands} lands for a {total_cards}-card deck. "
                    f"You have {total_lands}. {extra_note}"
                ),
            }
        )

    # ── Colored pip balance ──────────────────────────────────────────────────
    pip_counts: dict[str, float] = {"W": 0.0, "U": 0.0, "B": 0.0, "R": 0.0, "G": 0.0}
    for dc in deck_cards:
        if "Land" in (dc.card.type_line or ""):
            continue
        card_pips = _parse_color_pips(dc.card.mana_cost or "")
        for color, count in card_pips.items():
            pip_counts[color] += count * dc.quantity

    total_pip_count = sum(pip_counts.values())
    pip_pct: dict[str, float] = {
        c: round(v / total_pip_count * 100, 1) if total_pip_count > 0 else 0.0
        for c, v in pip_counts.items()
    }
    pip_summary = " | ".join(f"{c}: {p}%" for c, p in pip_pct.items() if p > 0) or "No colored pips"

    total_colored_cards = sum(color_counts.values())
    if total_lands > 0 and len(color_counts) >= 2 and total_pip_count > 0:
        for color in ["W", "U", "B", "R", "G"]:
            pip_share = pip_pct.get(color, 0.0)
            card_share = (
                round(color_counts.get(color, 0) / total_colored_cards * 100, 1)
                if total_colored_cards > 0
                else 0.0
            )
            # Flag colors where pip demand significantly outpaces their card share
            if pip_share >= 20 and card_share < pip_share * 0.55:
                cname = _COLOR_NAMES[color]
                suggestions.append(
                    {
                        "category": "Mana Base",
                        "severity": "warning",
                        "title": f"{cname} mana demand may exceed supply",
                        "body": (
                            f"{cname} accounts for {pip_share}% of your colored pip requirements "
                            f"but only {card_share}% of your colored cards are {cname}. "
                            f"Ensure your mana base has enough {cname.lower()}-producing sources "
                            "to reliably cast your spells on curve."
                        ),
                    }
                )

    # ── Copy-count distribution ──────────────────────────────────────────────
    non_land_main = [
        dc for dc in deck_cards if "Land" not in (dc.card.type_line or "") and not dc.is_sideboard
    ]
    one_ofs = sum(1 for dc in non_land_main if dc.quantity == 1)
    two_ofs = sum(1 for dc in non_land_main if dc.quantity == 2)
    three_ofs = sum(1 for dc in non_land_main if dc.quantity == 3)
    four_ofs = sum(1 for dc in non_land_main if dc.quantity == 4)

    if total_cards >= 60:
        if one_ofs >= 11:
            suggestions.append(
                {
                    "category": "Consistency",
                    "severity": "danger",
                    "title": f"{one_ofs} non-land cards played as 1-ofs",
                    "body": (
                        f"Your deck has {one_ofs} non-land cards with only 1 copy each. "
                        "This creates a highly inconsistent deck — you'll rarely draw any given card. "
                        "In 60-card formats, running 3–4 copies of key cards significantly improves consistency."
                    ),
                }
            )
        elif one_ofs >= 7:
            suggestions.append(
                {
                    "category": "Consistency",
                    "severity": "warning",
                    "title": f"{one_ofs} non-land cards played as 1-ofs",
                    "body": (
                        f"Your deck has {one_ofs} non-land cards with only 1 copy each. "
                        "High numbers of 1-ofs reduce consistency. "
                        "Consider running 3–4 copies of your best cards."
                    ),
                }
            )

    # ── Card type analysis (60-card constructed only) ────────────────────────
    creature_count = sum(
        dc.quantity
        for dc in deck_cards
        if "Creature" in (dc.card.type_line or "") and not dc.is_sideboard
    )
    interaction_count = 0
    card_draw_count = 0
    for dc in deck_cards:
        if "Land" in (dc.card.type_line or "") or dc.is_sideboard:
            continue
        oracle = (dc.card.oracle_text or "").lower()
        if any(
            kw in oracle for kw in ["destroy", "exile", "counter target", "deals damage", "-x/-x"]
        ):
            interaction_count += dc.quantity
        if any(kw in oracle for kw in ["draw a card", "draw two cards", "draw three cards"]):
            card_draw_count += dc.quantity

    if total_cards >= 60:
        if creature_count < 10 and curve_shape in ("Aggro", "Midrange"):
            suggestions.append(
                {
                    "category": "Card Types",
                    "severity": "info",
                    "title": f"Low creature count ({creature_count})",
                    "body": (
                        f"Your deck only has {creature_count} creatures for an "
                        f"{curve_shape.lower()} strategy. "
                        f"Most {curve_shape.lower()} decks want 18–26 creatures in a 60-card deck "
                        "to maintain consistent board presence and pressure."
                    ),
                }
            )
        if interaction_count == 0:
            suggestions.append(
                {
                    "category": "Card Types",
                    "severity": "warning",
                    "title": "No interaction detected",
                    "body": (
                        "Your deck has no visible removal, counterspells, or direct damage spells. "
                        "Having ways to answer your opponent's threats is important in most formats. "
                        "Consider adding a few removal spells or other interactive cards."
                    ),
                }
            )
        if card_draw_count == 0:
            suggestions.append(
                {
                    "category": "Card Types",
                    "severity": "info",
                    "title": "No card draw detected",
                    "body": (
                        "Your deck doesn't appear to contain cards that draw additional cards. "
                        "Card advantage helps you maintain resources in long games. "
                        "Even a few draw spells can significantly improve late-game performance."
                    ),
                }
            )

    # ── Sideboard ────────────────────────────────────────────────────────────
    sideboard_count = sum(dc.quantity for dc in deck_cards if dc.is_sideboard)
    if sideboard_count == 0:
        suggestions.append(
            {
                "category": "Sideboard",
                "severity": "info",
                "title": "No sideboard",
                "body": (
                    "Your deck has no sideboard. In best-of-3 matches, a 15-card sideboard lets you "
                    "swap cards between games to better target specific opponents and strategies."
                ),
            }
        )
    elif sideboard_count > 15:
        suggestions.append(
            {
                "category": "Sideboard",
                "severity": "warning",
                "title": f"Oversized sideboard ({sideboard_count} cards)",
                "body": (
                    f"Your sideboard has {sideboard_count} cards, which exceeds the 15-card maximum "
                    "in most formats. You'll need to cut it down for sanctioned play."
                ),
            }
        )

    return {
        "avg_cmc": avg_cmc,
        "curve_shape": curve_shape,
        "pip_counts": {k: round(v, 1) for k, v in pip_counts.items()},
        "pip_pct": pip_pct,
        "pip_summary": pip_summary,
        "one_ofs": one_ofs,
        "two_ofs": two_ofs,
        "three_ofs": three_ofs,
        "four_ofs": four_ofs,
        "suggestions": suggestions,
    }


def decks_list(request: HttpRequest) -> HttpResponse:
    """Deck performance overview."""
    decks = Deck.objects.annotate(
        games=Count("matches", filter=Q(matches__result__isnull=False)),
        wins=Count("matches", filter=Q(matches__result="win")),
        avg_turns=Avg("matches__total_turns", filter=Q(matches__result__isnull=False)),
        last_played=Max("matches__start_time"),
        version_count=Count("snapshots"),
    ).order_by("-last_played")

    for deck in decks:
        deck.win_rate = round(deck.wins / deck.games * 100, 1) if deck.games > 0 else 0

    return render(request, "decks.html", {"decks": decks})


def _categorize_cards(deck_cards):
    """Return cards_by_type dict, mana_curve, color_counts for a queryset of DeckCards."""
    cards_by_type = {}
    mana_curve = {i: 0 for i in range(8)}
    color_counts = {}

    for dc in deck_cards:
        card = dc.card
        type_line = card.type_line or "Unknown"

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

        cards_by_type.setdefault(category, []).append({"quantity": dc.quantity, "card": card})

        if "Land" not in type_line:
            cmc = int(card.cmc or 0)
            mana_curve[min(cmc, 7)] += dc.quantity

        for color in card.colors or []:
            color_counts[color] = color_counts.get(color, 0) + dc.quantity

    return cards_by_type, mana_curve, color_counts


def deck_detail(request: HttpRequest, deck_id: int) -> HttpResponse:
    """Detailed deck view — shows latest snapshot card list."""
    deck = get_object_or_404(Deck, pk=deck_id)

    latest = deck.latest_snapshot()
    deck_cards = list(
        latest.cards.select_related("card").order_by("card__cmc", "card__name")
        if latest
        else DeckCard.objects.none()
    )

    cards_by_type, mana_curve, color_counts = _categorize_cards(deck_cards)

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

    unknown_cards_count = UnknownCard.objects.filter(deck=deck, is_resolved=False).count()

    total_cards = sum(dc.quantity for dc in deck_cards)
    total_lands = sum(dc.quantity for dc in deck_cards if "Land" in (dc.card.type_line or ""))
    land_pct = round(total_lands / total_cards * 100, 1) if total_cards > 0 else 0
    suggested_lands = round(total_cards * 17 / 40)

    deck_analysis = _compute_deck_suggestions(
        deck_cards, mana_curve, color_counts, total_cards, total_lands, suggested_lands
    )

    return render(
        request,
        "deck_detail.html",
        {
            "deck": deck,
            "latest_snapshot": latest,
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
            "version_count": deck.snapshots.count(),
            "deck_analysis": deck_analysis,
        },
    )


def deck_history(request: HttpRequest, deck_id: int) -> HttpResponse:
    """Timeline of all deck snapshots with sequential diffs."""
    deck = get_object_or_404(Deck, pk=deck_id)

    snapshots = list(
        DeckSnapshot.objects.filter(deck=deck)
        .select_related("match")
        .order_by("match__start_time", "created_at")
    )

    # Build (snapshot, diff_from_previous, match) triples
    history = []
    for i, snap in enumerate(snapshots):
        prev = snapshots[i - 1] if i > 0 else None
        diff = compute_deck_diff(prev, snap)
        history.append(
            {
                "snapshot": snap,
                "diff": diff,
                "match": snap.match,
                "is_first": i == 0,
            }
        )

    # Reverse so newest first
    history.reverse()

    return render(
        request,
        "deck_history.html",
        {
            "deck": deck,
            "history": history,
            "total_versions": len(snapshots),
        },
    )


def deck_gallery(request: HttpRequest, deck_id: int) -> HttpResponse:
    """Card gallery view with Scryfall images."""
    deck = get_object_or_404(Deck, pk=deck_id)
    scryfall = get_scryfall()

    latest = deck.latest_snapshot()
    base_qs = latest.cards.select_related("card").all() if latest else DeckCard.objects.none()

    # Handle image download request
    if request.method == "POST" and request.POST.get("action") == "download_images":
        downloaded = 0
        failed = 0

        for dc in base_qs:
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
    deck_cards = base_qs.order_by("card__cmc", "card__name")

    cards_by_type = {}
    images_cached = 0
    total_cards = 0

    for dc in deck_cards:
        card = dc.card
        type_line = card.type_line or "Unknown"
        total_cards += dc.quantity

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

        image_cached = scryfall.get_cached_image_path(card.grp_id) is not None
        if image_cached:
            images_cached += 1

        cards_by_type.setdefault(category, []).append(
            {
                "quantity": dc.quantity,
                "card": card,
                "image_cached": image_cached,
                "image_url": card.image_uri,
            }
        )

    unique_cards = deck_cards.count()
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

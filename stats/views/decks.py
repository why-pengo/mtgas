"""
Deck list, detail, gallery, and history views.
"""

import logging

from django.contrib import messages
from django.db.models import Avg, Count, Max, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from src.services.scryfall import get_scryfall

from ..deck_diff import compute_deck_diff
from ..models import Deck, DeckCard, DeckSnapshot, UnknownCard

logger = logging.getLogger("stats.views")


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
    deck_cards = (
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

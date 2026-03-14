"""
Deck list, detail, and gallery views.
"""

import logging

from django.contrib import messages
from django.db.models import Avg, Count, Max, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from src.services.scryfall import get_scryfall

from ..models import Deck, UnknownCard

logger = logging.getLogger("stats.views")


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

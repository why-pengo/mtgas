"""
Dashboard and API stats views.
"""

import logging
from datetime import timedelta

from django.db.models import Avg, Count, Q
from django.db.models.functions import TruncDate
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone

from src.services.scryfall import get_scryfall

from ..models import Card, Deck, DeckCard, Match

logger = logging.getLogger("stats.views")


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

    # Check card data status and unknown card warnings
    scryfall = get_scryfall()
    try:
        card_stats = scryfall.stats()
        card_data_ready = card_stats["index_loaded"] and card_stats["total_cards"] > 0
        card_count = card_stats["total_cards"]
    except Exception:
        card_data_ready = False
        card_count = 0

    # Warn when unknown cards appear in real game usage (decks or cast spells)
    unknown_card_count = Card.objects.filter(name__startswith="Unknown Card").count()
    unknown_in_decks = (
        DeckCard.objects.filter(card__name__startswith="Unknown Card").exists()
        if unknown_card_count
        else False
    )
    show_unknown_warning = unknown_card_count > 0 and (unknown_in_decks or unknown_card_count > 5)

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
            "unknown_card_count": unknown_card_count,
            "show_unknown_warning": show_unknown_warning,
        },
    )


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

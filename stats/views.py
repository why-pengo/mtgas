"""
Django views for MTG Arena Statistics.
"""

from datetime import timedelta

from django.core.paginator import Paginator
from django.db.models import Avg, Count, Max, Q
from django.db.models.functions import TruncDate
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from .models import Deck, ImportSession, Match


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

"""
Unknown card list and fix views.
"""

import logging

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from ..models import Card, UnknownCard

logger = logging.getLogger("stats.views")


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

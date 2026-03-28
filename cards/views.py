import requests
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .models import PaperCard

SCRYFALL_NAMED_URL = "https://api.scryfall.com/cards/named"


def card_index(request):
    paper_cards = PaperCard.objects.order_by("name")
    return render(request, "cards/index.html", {"paper_cards": paper_cards})


@require_http_methods(["GET", "POST"])
def add_paper_card(request):
    """Add a paper card by typing its name — no photo required."""
    error = None
    if request.method == "POST":
        name = request.POST.get("card_name", "").strip()
        if name:
            resp = requests.get(SCRYFALL_NAMED_URL, params={"fuzzy": name}, timeout=10)
            if resp.status_code == 200:
                paper_card = PaperCard.upsert_from_scryfall(resp.json())
                return redirect("cards:paper_card_detail", pk=paper_card.pk)
            else:
                error = f'No card found matching "{name}". Try a different name or spelling.'
        else:
            error = "Please enter a card name."

    return render(request, "cards/add_paper_card.html", {"error": error})


def paper_card_detail(request, pk):
    paper_card = get_object_or_404(PaperCard, pk=pk)
    return render(request, "cards/paper_card_detail.html", {"paper_card": paper_card})


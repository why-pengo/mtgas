import requests
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .models import PaperCard

SCRYFALL_NAMED_URL = "https://api.scryfall.com/cards/named"
SCRYFALL_SEARCH_URL = "https://api.scryfall.com/cards/search"
SCRYFALL_CARD_URL = "https://api.scryfall.com/cards"


def card_index(request):
    paper_cards = PaperCard.objects.order_by("name")
    return render(request, "cards/index.html", {"paper_cards": paper_cards})


@require_http_methods(["GET", "POST"])
def add_paper_card(request):
    """Add a paper card by typing its name — no photo required.

    If Scryfall returns an ambiguous result, a pick-list of candidates is shown.
    If the user selects a candidate (via scryfall_id POST param), that card is
    fetched by ID and saved directly.
    """
    error = None
    candidates = None

    if request.method == "POST":
        scryfall_id = request.POST.get("scryfall_id", "").strip()
        name = request.POST.get("card_name", "").strip()

        if scryfall_id:
            # User picked a card from the disambiguation list — fetch it by ID.
            resp = requests.get(f"{SCRYFALL_CARD_URL}/{scryfall_id}", timeout=10)
            if resp.status_code == 200:
                paper_card = PaperCard.upsert_from_scryfall(resp.json())
                return redirect("cards:paper_card_detail", pk=paper_card.pk)
            else:
                error = "Could not fetch the selected card from Scryfall. Please try again."

        elif name:
            resp = requests.get(SCRYFALL_NAMED_URL, params={"fuzzy": name}, timeout=10)
            if resp.status_code == 200:
                paper_card = PaperCard.upsert_from_scryfall(resp.json())
                return redirect("cards:paper_card_detail", pk=paper_card.pk)
            else:
                data = resp.json()
                if data.get("type") == "ambiguous":
                    search_resp = requests.get(
                        SCRYFALL_SEARCH_URL,
                        params={"q": name, "order": "name", "unique": "cards"},
                        timeout=10,
                    )
                    if search_resp.status_code == 200:
                        candidates = search_resp.json().get("data", [])[:20]
                    else:
                        error = (
                            f'Multiple cards match "{name}" but suggestions could not be loaded. '
                            "Try a more specific name."
                        )
                else:
                    error = f'No card found matching "{name}". Try a different name or spelling.'
        else:
            error = "Please enter a card name."

    return render(request, "cards/add_paper_card.html", {"error": error, "candidates": candidates})


def paper_card_detail(request, pk):
    paper_card = get_object_or_404(PaperCard, pk=pk)
    return render(request, "cards/paper_card_detail.html", {"paper_card": paper_card})


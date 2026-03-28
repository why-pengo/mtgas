import requests
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .forms import CardImageUploadForm
from .models import CardImage, PaperCard
from .tasks import match_card_image

SCRYFALL_NAMED_URL = "https://api.scryfall.com/cards/named"


def card_index(request):
    recent_uploads = CardImage.objects.select_related("paper_card").order_by("-uploaded_at")[:20]
    paper_cards = PaperCard.objects.order_by("name")
    return render(
        request,
        "cards/index.html",
        {
            "recent_uploads": recent_uploads,
            "paper_cards": paper_cards,
        },
    )


def card_photography_guide(request):
    return render(request, "card_image_help.html")


@require_http_methods(["GET", "POST"])
def upload_card(request):
    if request.method == "POST":
        form = CardImageUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            error = " ".join(str(e) for errs in form.errors.values() for e in errs)
            return render(request, "cards/upload.html", {"error": error})

        card = form.save()
        transaction.on_commit(lambda: match_card_image.delay(card.pk))
        return redirect("cards:card_detail", pk=card.pk)

    return render(request, "cards/upload.html")


def card_detail(request, pk):
    card = get_object_or_404(
        CardImage.objects.select_related("paper_card"),
        pk=pk,
    )
    return render(request, "cards/card_detail.html", {"card": card})


@require_http_methods(["POST"])
def name_lookup(request, pk):
    """Re-match a CardImage using a user-supplied card name instead of the OCR result."""
    card = get_object_or_404(CardImage, pk=pk)
    name = request.POST.get("card_name", "").strip()
    if not name:
        return redirect("cards:card_detail", pk=pk)

    resp = requests.get(SCRYFALL_NAMED_URL, params={"fuzzy": name}, timeout=10)
    if resp.status_code == 200:
        paper_card = PaperCard.upsert_from_scryfall(resp.json())
        card.paper_card = paper_card
        card.ocr_text = name
        card.status = CardImage.Status.MATCHED
        card.error = ""
    else:
        card.status = CardImage.Status.UNMATCHED
        card.error = f'No Scryfall match for: "{name}"'

    card.save(update_fields=["paper_card", "ocr_text", "status", "error", "updated_at"])
    return redirect("cards:card_detail", pk=pk)


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


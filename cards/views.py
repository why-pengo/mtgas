from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from stats.models import Card

from .models import CardImage
from .tasks import match_card_image


def card_index(request):
    recent = CardImage.objects.select_related("scryfall_card").order_by("-uploaded_at")[:20]

    phash_total = Card.objects.count()
    phash_indexed = Card.objects.exclude(phash__isnull=True).exclude(phash="").count()
    phash_missing = phash_total - phash_indexed
    phash_pct = round(phash_indexed / phash_total * 100) if phash_total else 0

    return render(
        request,
        "cards/index.html",
        {
            "recent": recent,
            "phash_total": phash_total,
            "phash_indexed": phash_indexed,
            "phash_missing": phash_missing,
            "phash_pct": phash_pct,
        },
    )


def card_photography_guide(request):
    return render(request, "card_image_help.html")


@require_http_methods(["GET", "POST"])
def upload_card(request):
    if request.method == "POST":
        f = request.FILES.get("image")
        if not f:
            return render(request, "cards/upload.html", {"error": "Please select an image."})

        card = CardImage.objects.create(image=f)
        transaction.on_commit(lambda: match_card_image.delay(card.pk))
        return redirect("cards:card_detail", pk=card.pk)

    return render(request, "cards/upload.html")


def card_detail(request, pk):
    card = get_object_or_404(
        CardImage.objects.select_related("scryfall_card"),
        pk=pk,
    )
    return render(request, "cards/card_detail.html", {"card": card})

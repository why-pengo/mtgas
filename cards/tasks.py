import re

import pytesseract
import requests
from celery import shared_task
from PIL import Image

from .models import CardImage, PaperCard

SCRYFALL_NAMED_URL = "https://api.scryfall.com/cards/named"


def _extract_card_name(img: Image.Image) -> str:
    """
    Run OCR on the full image and return the most likely card name candidate.

    MTG card names sit near the top of the card as the largest/first text line.
    We take the first non-empty line that looks like a name (letters, reasonable
    length) as the best candidate.
    """
    text = pytesseract.image_to_string(img)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    candidates = [
        line for line in lines
        if 2 <= len(line) <= 60 and re.search(r"[A-Za-z]", line)
    ]
    return candidates[0] if candidates else text.strip()[:60]


def _lookup_scryfall(name: str) -> dict | None:
    """Call Scryfall fuzzy name search. Returns the card data dict or None."""
    try:
        resp = requests.get(SCRYFALL_NAMED_URL, params={"fuzzy": name}, timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except requests.RequestException:
        pass
    return None


@shared_task(bind=True, max_retries=3)
def match_card_image(self, card_image_id: int):
    try:
        card = CardImage.objects.get(pk=card_image_id)
    except CardImage.DoesNotExist:
        return

    card.status = CardImage.Status.PROCESSING
    card.save(update_fields=["status", "updated_at"])

    try:
        img = Image.open(card.image.path).convert("RGB")
        ocr_name = _extract_card_name(img)
        card.ocr_text = ocr_name

        scryfall_data = _lookup_scryfall(ocr_name)
        if scryfall_data:
            paper_card = PaperCard.upsert_from_scryfall(scryfall_data)
            card.paper_card = paper_card
            card.status = CardImage.Status.MATCHED
            card.error = ""
        else:
            card.status = CardImage.Status.UNMATCHED
            card.error = f'No Scryfall match for OCR text: "{ocr_name}"'

        card.save(update_fields=["paper_card", "ocr_text", "status", "error", "updated_at"])

    except Exception as exc:
        card.status = CardImage.Status.FAILED
        card.error = str(exc)
        card.save(update_fields=["status", "error", "updated_at"])
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


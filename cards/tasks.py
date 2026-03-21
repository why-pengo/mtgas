import imagehash
from io import BytesIO
from PIL import Image
from celery import shared_task

from .models import CardImage
from stats.models import Card

MATCH_THRESHOLD = 12  # Hamming distance; lower = stricter. 10-15 is a good starting range.


@shared_task(bind=True, max_retries=3)
def match_card_image(self, card_image_id: int):
    try:
        card = CardImage.objects.get(pk=card_image_id)
    except CardImage.DoesNotExist:
        return

    card.status = CardImage.Status.PROCESSING
    card.save(update_fields=["status", "updated_at"])

    try:
        # 1. Compute phash of the uploaded image
        img = Image.open(card.image.path).convert("RGB")
        upload_hash = imagehash.phash(img)

        # 2. Compare against all cards in the Card model that have a phash
        best_match = None
        best_distance = None

        for scryfall_card in Card.objects.exclude(phash__isnull=True).iterator():
            db_hash = imagehash.hex_to_hash(scryfall_card.phash)
            distance = upload_hash - db_hash
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_match = scryfall_card
            if distance == 0:
                break  # perfect match, stop early

        if best_match and best_distance <= MATCH_THRESHOLD:
            card.scryfall_card = best_match
            card.match_distance = best_distance
            card.status = CardImage.Status.MATCHED
        else:
            card.status = CardImage.Status.UNMATCHED
            card.error = (
                f"No match within threshold {MATCH_THRESHOLD}. "
                f"Best distance was {best_distance}."
            )

        card.save(
            update_fields=["scryfall_card", "match_distance", "status", "error", "updated_at"]
        )

    except Exception as exc:
        card.status = CardImage.Status.FAILED
        card.error = str(exc)
        card.save(update_fields=["status", "error", "updated_at"])
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))

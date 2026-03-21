import requests
import imagehash
from io import BytesIO
from PIL import Image
from django.core.management.base import BaseCommand

from stats.models import Card


class Command(BaseCommand):
    help = "Download card images from Scryfall URLs and compute phashes for all cards"

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Only process this many cards (useful for testing)",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Recompute phash even if one already exists",
        )

    def handle(self, *args, **options):
        qs = Card.objects.all()
        if not options["overwrite"]:
            qs = qs.filter(phash__isnull=True)
        if options["limit"]:
            qs = qs[: options["limit"]]

        total = qs.count()
        self.stdout.write(f"Computing phashes for {total} cards...")

        updated = 0
        failed = 0
        for card in qs.iterator():
            try:
                url = card.image_uri
                if not url:
                    continue
                resp = requests.get(url, timeout=10)
                resp.raise_for_status()
                img = Image.open(BytesIO(resp.content)).convert("RGB")
                card.phash = str(imagehash.phash(img))
                card.save(update_fields=["phash"])
                updated += 1
                if updated % 100 == 0:
                    self.stdout.write(f"  {updated}/{total} done...")
            except Exception as e:
                failed += 1
                self.stderr.write(f"  Failed {card.pk}: {e}")

        self.stdout.write(
            self.style.SUCCESS(f"Done. {updated} phashes computed, {failed} failed.")
        )

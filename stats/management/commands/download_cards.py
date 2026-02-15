"""
Django management command to download Scryfall bulk card data.
"""

from django.core.management.base import BaseCommand
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from src.services.scryfall import get_scryfall


class Command(BaseCommand):
    help = 'Download Scryfall bulk card data for card name lookups'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force re-download even if data exists'
        )

    def handle(self, *args, **options):
        force = options['force']

        self.stdout.write("Downloading Scryfall bulk data...")
        self.stdout.write("This may take a few minutes (~350MB download)...")

        scryfall = get_scryfall()
        success = scryfall.ensure_bulk_data(force_download=force)

        if success:
            stats = scryfall.stats()
            self.stdout.write(self.style.SUCCESS(
                f"\nCard database ready!"
                f"\n  Total cards with Arena IDs: {stats['total_cards']}"
                f"\n  Bulk file size: {stats['bulk_file_size_mb']:.1f} MB"
            ))
        else:
            self.stderr.write(self.style.ERROR("Failed to download card data"))


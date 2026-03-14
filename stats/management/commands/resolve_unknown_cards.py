"""
Django management command to resolve unknown card placeholders.

Looks up all "Unknown" and game-state-only card entries against the current
Scryfall index and updates any that now have a match.  Run this after
``download_cards`` to repair existing database entries without a full re-import.
"""

import sys
from pathlib import Path

from django.core.management.base import BaseCommand

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from src.services.scryfall import get_scryfall  # noqa: E402
from stats.models import Card  # noqa: E402


class Command(BaseCommand):
    help = (
        "Resolve unknown card placeholders by looking them up in the current Scryfall data. "
        "Run after 'download_cards' to update existing matches without re-importing."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be updated without making changes",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        scryfall = get_scryfall()

        if not scryfall.ensure_bulk_data():
            self.stderr.write(
                self.style.ERROR("Scryfall data not available. Run download_cards first.")
            )
            return

        # Find all cards that are placeholders (name starts with "Unknown Card" or
        # descriptive game-state placeholder like "Creature — Human Villain [N]")
        unknown_qs = Card.objects.filter(name__startswith="Unknown Card")
        total = unknown_qs.count()

        if total == 0:
            self.stdout.write(self.style.SUCCESS("No unknown cards found — nothing to resolve."))
            return

        self.stdout.write(f"Found {total} unknown card(s) to resolve...")

        resolved = 0
        still_unknown = 0

        for card in unknown_qs:
            grp_id = card.grp_id
            card_data = scryfall.get_card_by_arena_id(grp_id)

            if card_data:
                if dry_run:
                    self.stdout.write(
                        f"  [dry-run] {grp_id}: '{card.name}' → '{card_data['name']}'"
                    )
                else:
                    card.name = card_data.get("name")
                    card.mana_cost = card_data.get("mana_cost")
                    card.cmc = card_data.get("cmc")
                    card.type_line = card_data.get("type_line")
                    card.colors = card_data.get("colors", [])
                    card.color_identity = card_data.get("color_identity", [])
                    card.set_code = card_data.get("set_code")
                    card.rarity = card_data.get("rarity")
                    card.oracle_text = card_data.get("oracle_text")
                    card.power = card_data.get("power")
                    card.toughness = card_data.get("toughness")
                    card.scryfall_id = card_data.get("scryfall_id")
                    card.image_uri = card_data.get("image_uri")
                    card.save()
                resolved += 1
            else:
                still_unknown += 1

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"\n[dry-run] Would resolve {resolved}/{total} cards. "
                    f"{still_unknown} still not in Scryfall."
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS(f"\nResolved {resolved}/{total} unknown cards."))
            if still_unknown:
                self.stdout.write(
                    self.style.WARNING(
                        f"{still_unknown} card(s) still not found in Scryfall "
                        f"(may be from sets not yet indexed)."
                    )
                )

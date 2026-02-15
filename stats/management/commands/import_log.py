"""
Django management command to import MTG Arena log files.

Batch imports matches from Player.log after game sessions.
Tracks imports using match_id to avoid duplicates.
"""

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Set

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

# Add src to path for parser imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from stats.models import Match, Deck, DeckCard, Card, GameAction, LifeChange, ZoneTransfer, ImportSession
from src.parser.log_parser import MTGALogParser, MatchData
from src.services.scryfall import get_scryfall


class Command(BaseCommand):
    help = 'Import matches from MTG Arena Player.log file (batch import after sessions)'

    def add_arguments(self, parser):
        parser.add_argument(
            'log_file',
            help='Path to Player.log file'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Re-import all matches, even if already imported'
        )
        parser.add_argument(
            '--download-cards',
            action='store_true',
            help='Download Scryfall bulk data before importing'
        )

    def handle(self, *args, **options):
        log_path = options['log_file']
        force = options['force']
        download_cards = options['download_cards']

        if not os.path.exists(log_path):
            raise CommandError(f"Log file not found: {log_path}")

        # Get file info
        file_stat = os.stat(log_path)
        file_size = file_stat.st_size
        file_modified = datetime.fromtimestamp(file_stat.st_mtime, tz=timezone.utc)

        # Create import session
        session = ImportSession.objects.create(
            log_file=log_path,
            file_size=file_size,
            file_modified=file_modified,
            status='running'
        )

        try:
            # Ensure card data is available
            scryfall = get_scryfall()
            if download_cards:
                self.stdout.write("Downloading Scryfall bulk data...")
                scryfall.ensure_bulk_data(force_download=True)
            else:
                scryfall.ensure_bulk_data()

            # Get existing match IDs to skip
            existing_match_ids: Set[str] = set()
            if not force:
                existing_match_ids = set(
                    Match.objects.values_list('match_id', flat=True)
                )
                self.stdout.write(f"Found {len(existing_match_ids)} existing matches")

            # Parse log file
            self.stdout.write(f"Parsing log file: {log_path}")
            parser = MTGALogParser(log_path)
            matches = parser.parse_matches()
            self.stdout.write(f"Found {len(matches)} matches in log file")

            # Import matches
            imported_count = 0
            skipped_count = 0

            for match_data in matches:
                if not force and match_data.match_id in existing_match_ids:
                    skipped_count += 1
                    continue

                try:
                    self._import_match(match_data, scryfall)
                    imported_count += 1
                    self.stdout.write(
                        f"  Imported: {match_data.match_id[:8]}... "
                        f"vs {match_data.opponent_name} ({match_data.result or 'incomplete'})"
                    )
                except Exception as e:
                    self.stderr.write(f"  Failed to import {match_data.match_id}: {e}")

            # Update session
            session.matches_imported = imported_count
            session.matches_skipped = skipped_count
            session.status = 'completed'
            session.completed_at = timezone.now()
            session.save()

            self.stdout.write(self.style.SUCCESS(
                f"\nImport complete: {imported_count} imported, {skipped_count} skipped"
            ))

        except Exception as e:
            session.status = 'failed'
            session.error_message = str(e)
            session.save()
            raise CommandError(f"Import failed: {e}")

    @transaction.atomic
    def _import_match(self, match_data: MatchData, scryfall):
        """Import a single match into the database."""
        # Ensure deck exists
        deck = None
        if match_data.deck_id:
            deck = self._ensure_deck(match_data, scryfall)

        # Collect all unique card IDs and ensure they're in the cards table
        card_grp_ids = self._collect_card_ids(match_data)
        self._ensure_cards(card_grp_ids, scryfall)

        # Calculate duration
        duration = None
        if match_data.start_time and match_data.end_time:
            duration = int((match_data.end_time - match_data.start_time).total_seconds())

        # Create match - use match_id as the tracking key
        match = Match.objects.create(
            match_id=match_data.match_id,
            game_number=1,
            player_seat_id=match_data.player_seat_id,
            player_name=match_data.player_name,
            player_user_id=match_data.player_user_id,
            opponent_seat_id=match_data.opponent_seat_id,
            opponent_name=match_data.opponent_name,
            opponent_user_id=match_data.opponent_user_id,
            deck=deck,
            event_id=match_data.event_id,
            format=match_data.format,
            match_type=match_data.match_type,
            result=match_data.result,
            winning_team_id=match_data.winning_team_id,
            winning_reason=match_data.winning_reason,
            start_time=match_data.start_time,
            end_time=match_data.end_time,
            duration_seconds=duration,
            total_turns=match_data.total_turns,
        )

        # Import actions (significant ones only)
        self._import_actions(match, match_data)

        # Import life changes
        self._import_life_changes(match, match_data)

        # Import zone transfers
        self._import_zone_transfers(match, match_data)

        return match

    def _ensure_deck(self, match_data: MatchData, scryfall) -> Deck:
        """Ensure deck exists in database."""
        deck, created = Deck.objects.get_or_create(
            deck_id=match_data.deck_id,
            defaults={
                'name': match_data.deck_name or 'Unknown Deck',
                'format': match_data.format,
            }
        )

        if created and match_data.deck_cards:
            # Ensure cards exist first
            card_ids = {c.get('cardId') for c in match_data.deck_cards if c.get('cardId')}
            self._ensure_cards(card_ids, scryfall)

            # Add deck cards
            for card_data in match_data.deck_cards:
                card_id = card_data.get('cardId')
                quantity = card_data.get('quantity', 1)
                if card_id:
                    try:
                        card = Card.objects.get(grp_id=card_id)
                        DeckCard.objects.create(
                            deck=deck,
                            card=card,
                            quantity=quantity,
                            is_sideboard=False
                        )
                    except Card.DoesNotExist:
                        pass

        return deck

    def _collect_card_ids(self, match_data: MatchData) -> Set[int]:
        """Collect all unique card IDs from match data."""
        card_ids = set()

        for card in match_data.deck_cards:
            if card.get('cardId'):
                card_ids.add(card['cardId'])

        for inst_data in match_data.card_instances.values():
            if inst_data.get('grp_id'):
                card_ids.add(inst_data['grp_id'])

        for action in match_data.actions:
            if action.get('card_grp_id'):
                card_ids.add(action['card_grp_id'])

        return card_ids

    def _ensure_cards(self, card_ids: Set[int], scryfall):
        """Ensure cards exist in database from Scryfall bulk data."""
        if not card_ids:
            return

        existing_ids = set(Card.objects.filter(grp_id__in=card_ids).values_list('grp_id', flat=True))
        missing_ids = card_ids - existing_ids

        if missing_ids:
            card_lookup = scryfall.lookup_cards_batch(missing_ids)

            cards_to_create = []
            for grp_id, card_data in card_lookup.items():
                if card_data:
                    cards_to_create.append(Card(
                        grp_id=grp_id,
                        name=card_data.get('name'),
                        mana_cost=card_data.get('mana_cost'),
                        cmc=card_data.get('cmc'),
                        type_line=card_data.get('type_line'),
                        colors=card_data.get('colors', []),
                        color_identity=card_data.get('color_identity', []),
                        set_code=card_data.get('set_code'),
                        rarity=card_data.get('rarity'),
                        oracle_text=card_data.get('oracle_text'),
                        power=card_data.get('power'),
                        toughness=card_data.get('toughness'),
                        scryfall_id=card_data.get('scryfall_id'),
                        image_uri=card_data.get('image_uri'),
                    ))
                else:
                    # Unknown card placeholder
                    cards_to_create.append(Card(
                        grp_id=grp_id,
                        name=f"Unknown Card ({grp_id})"
                    ))

            Card.objects.bulk_create(cards_to_create, ignore_conflicts=True)

    def _import_actions(self, match: Match, match_data: MatchData):
        """Import game actions for a match."""
        significant_types = {
            'ActionType_Cast', 'ActionType_Play',
            'ActionType_Attack', 'ActionType_Block',
            'ActionType_Activate', 'ActionType_Activate_Mana',
            'ActionType_Resolution'
        }

        seen = set()
        actions_to_create = []

        for action in match_data.actions:
            key = (
                action.get('game_state_id'),
                action.get('action_type'),
                action.get('instance_id')
            )

            action_type = action.get('action_type', '')
            if key in seen or action_type not in significant_types:
                continue
            seen.add(key)

            card_grp_id = action.get('card_grp_id')

            actions_to_create.append(GameAction(
                match=match,
                game_state_id=action.get('game_state_id'),
                turn_number=action.get('turn_number'),
                phase=action.get('phase'),
                step=action.get('step'),
                active_player_seat=action.get('active_player'),
                seat_id=action.get('seat_id'),
                action_type=action_type,
                instance_id=action.get('instance_id'),
                card_id=card_grp_id,
                ability_grp_id=action.get('ability_grp_id'),
                mana_cost=action.get('mana_cost'),
                timestamp_ms=action.get('timestamp'),
            ))

        GameAction.objects.bulk_create(actions_to_create)

    def _import_life_changes(self, match: Match, match_data: MatchData):
        """Import life total changes for a match."""
        prev_life = {}
        changes_to_create = []

        for lc in match_data.life_changes:
            seat_id = lc.get('seat_id')
            life_total = lc.get('life_total')

            if seat_id is None or life_total is None:
                continue

            change = None
            if seat_id in prev_life:
                change = life_total - prev_life[seat_id]
                if change == 0:
                    continue

            prev_life[seat_id] = life_total

            changes_to_create.append(LifeChange(
                match=match,
                game_state_id=lc.get('game_state_id'),
                turn_number=lc.get('turn_number'),
                seat_id=seat_id,
                life_total=life_total,
                change_amount=change,
            ))

        LifeChange.objects.bulk_create(changes_to_create)

    def _import_zone_transfers(self, match: Match, match_data: MatchData):
        """Import zone transfers for a match."""
        seen = set()
        transfers_to_create = []

        for zt in match_data.zone_transfers:
            key = (
                zt.get('game_state_id'),
                zt.get('instance_id'),
                zt.get('category')
            )

            if key in seen:
                continue
            seen.add(key)

            card_grp_id = zt.get('card_grp_id')

            transfers_to_create.append(ZoneTransfer(
                match=match,
                game_state_id=zt.get('game_state_id'),
                turn_number=zt.get('turn_number'),
                instance_id=zt.get('instance_id'),
                card_id=card_grp_id,
                from_zone=zt.get('from_zone'),
                to_zone=zt.get('to_zone'),
                category=zt.get('category'),
            ))

        ZoneTransfer.objects.bulk_create(transfers_to_create)


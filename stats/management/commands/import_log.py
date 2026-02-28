"""
Django management command to import MTG Arena log files.

Batch imports matches from Player.log after game sessions.
Tracks imports using match_id to avoid duplicates.
"""

import os
import sys
from datetime import datetime
from datetime import timezone as dt_timezone
from pathlib import Path
from typing import Set

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

# Add src to path for parser imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from src.parser.log_parser import MatchData, MTGALogParser  # noqa: E402
from src.services.import_service import (  # noqa: E402
    _SKIP_OBJECT_TYPES,
    _TOKEN_OBJECT_TYPES,
    generate_token_name,
)
from src.services.scryfall import get_scryfall  # noqa: E402
from stats.models import (  # noqa: E402
    Card,
    Deck,
    DeckCard,
    GameAction,
    ImportSession,
    LifeChange,
    Match,
    UnknownCard,
    ZoneTransfer,
)


class Command(BaseCommand):
    help = "Import matches from MTG Arena Player.log file (batch import after sessions)"

    def add_arguments(self, parser):
        parser.add_argument("log_file", help="Path to Player.log file")
        parser.add_argument(
            "--force", action="store_true", help="Re-import all matches, even if already imported"
        )
        parser.add_argument(
            "--download-cards",
            action="store_true",
            help="Download Scryfall bulk data before importing",
        )

    def handle(self, *args, **options):
        log_path = options["log_file"]
        force = options["force"]
        download_cards = options["download_cards"]

        if not os.path.exists(log_path):
            raise CommandError(f"Log file not found: {log_path}")

        # Get file info
        file_stat = os.stat(log_path)
        file_size = file_stat.st_size
        file_modified = datetime.fromtimestamp(file_stat.st_mtime, tz=dt_timezone.utc)

        # Create import session
        session = ImportSession.objects.create(
            log_file=log_path, file_size=file_size, file_modified=file_modified, status="running"
        )

        # Store session for use in import methods
        self.import_session = session

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
                existing_match_ids = set(Match.objects.values_list("match_id", flat=True))
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
                    if force:
                        Match.objects.filter(match_id=match_data.match_id).delete()
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
            session.status = "completed"
            session.completed_at = timezone.now()
            session.save()

            self.stdout.write(
                self.style.SUCCESS(
                    f"\nImport complete: {imported_count} imported, {skipped_count} skipped"
                )
            )

        except Exception as e:
            session.status = "failed"
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

        # Calculate duration
        duration = None
        if match_data.start_time and match_data.end_time:
            duration = int((match_data.end_time - match_data.start_time).total_seconds())

        # Defensive check: ensure datetimes are timezone-aware
        # Note: Parser now creates timezone-aware datetimes, but we keep this
        # as a safety net in case of legacy data or future changes
        start_time = match_data.start_time
        end_time = match_data.end_time
        if start_time and start_time.tzinfo is None:
            start_time = timezone.make_aware(start_time)
        if end_time and end_time.tzinfo is None:
            end_time = timezone.make_aware(end_time)

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
            start_time=start_time,
            end_time=end_time,
            duration_seconds=duration,
            total_turns=match_data.total_turns,
        )

        # Collect all unique card IDs and ensure they're in the cards table
        # Pass match and deck for unknown card tracking
        real_card_ids, special_objects = self._collect_card_ids(match_data)
        self._ensure_cards(real_card_ids, special_objects, scryfall, match, deck)

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
                "name": match_data.deck_name or "Unknown Deck",
                "format": match_data.format,
            },
        )

        if created and match_data.deck_cards:
            # Ensure cards exist first - pass None for match since it doesn't exist yet
            card_ids = {c.get("cardId") for c in match_data.deck_cards if c.get("cardId")}
            self._ensure_cards(card_ids, {}, scryfall, None, deck)

            # Add deck cards
            for card_data in match_data.deck_cards:
                card_id = card_data.get("cardId")
                quantity = card_data.get("quantity", 1)
                if card_id:
                    try:
                        card = Card.objects.get(grp_id=card_id)
                        DeckCard.objects.create(
                            deck=deck, card=card, quantity=quantity, is_sideboard=False
                        )
                    except Card.DoesNotExist:
                        pass

        return deck

    def _collect_card_ids(self, match_data: MatchData) -> tuple[set[int], dict[int, dict]]:
        """Collect card IDs from match data.

        Returns:
            real_card_ids: grpIds that should be looked up in Scryfall.
            special_objects: grpId → instance_data for tokens, emblems, and
                named card-face types that are not standard cards.
        """
        real_card_ids: set[int] = set()
        special_objects: dict[int, dict] = {}

        for card in match_data.deck_cards:
            if card.get("cardId"):
                real_card_ids.add(card["cardId"])

        for inst_data in match_data.card_instances.values():
            grp_id = inst_data.get("grp_id")
            obj_type = inst_data.get("type", "")
            if not grp_id:
                continue
            if obj_type in _SKIP_OBJECT_TYPES:
                continue
            if obj_type == "GameObjectType_Card":
                real_card_ids.add(grp_id)
                special_objects.pop(grp_id, None)
            elif obj_type == "GameObjectType_Omen":
                # Omen back-face grpIds share their Arena ID with the front-face
                # GameObjectType_Card (the spell being cast). Card is processed first,
                # so we must override: back-face IDs are not in Scryfall.
                real_card_ids.discard(grp_id)
                special_objects[grp_id] = inst_data
            elif grp_id not in real_card_ids:
                special_objects.setdefault(grp_id, inst_data)

        for action in match_data.actions:
            cid = action.get("card_grp_id")
            if cid and cid not in special_objects:
                real_card_ids.add(cid)

        return real_card_ids, special_objects

    def _ensure_cards(
        self,
        real_card_ids: set[int],
        special_objects: dict[int, dict],
        scryfall,
        match=None,
        deck=None,
    ):
        """Ensure cards/objects exist in the database."""
        all_ids = real_card_ids | set(special_objects)
        if not all_ids:
            return

        existing_ids = set(Card.objects.filter(grp_id__in=all_ids).values_list("grp_id", flat=True))
        missing_real = real_card_ids - existing_ids
        missing_special = {gid: d for gid, d in special_objects.items() if gid not in existing_ids}

        # ── Real cards: Scryfall lookup, Unknown Card fallback ──
        if missing_real:
            card_lookup = scryfall.lookup_cards_batch(missing_real)
            cards_to_create = []
            unknown_cards_to_log = []

            for grp_id, card_data in card_lookup.items():
                if card_data:
                    cards_to_create.append(
                        Card(
                            grp_id=grp_id,
                            name=card_data.get("name"),
                            mana_cost=card_data.get("mana_cost"),
                            cmc=card_data.get("cmc"),
                            type_line=card_data.get("type_line"),
                            colors=card_data.get("colors", []),
                            color_identity=card_data.get("color_identity", []),
                            set_code=card_data.get("set_code"),
                            rarity=card_data.get("rarity"),
                            oracle_text=card_data.get("oracle_text"),
                            power=card_data.get("power"),
                            toughness=card_data.get("toughness"),
                            scryfall_id=card_data.get("scryfall_id"),
                            image_uri=card_data.get("image_uri"),
                        )
                    )
                else:
                    context_info = {
                        "grp_id": grp_id,
                        "import_session_id": self.import_session.id,
                        "match_id": match.match_id if match else None,
                        "deck_id": deck.deck_id if deck else None,
                        "deck_name": deck.name if deck else None,
                    }
                    self.stdout.write(
                        self.style.WARNING(
                            f"Unknown card: grp_id={grp_id}, "
                            f"deck={deck.name if deck else 'N/A'}, "
                            f"match={match.match_id[:8] if match else 'N/A'}"
                        )
                    )
                    cards_to_create.append(Card(grp_id=grp_id, name=f"Unknown Card ({grp_id})"))
                    unknown_cards_to_log.append((grp_id, context_info))

            if cards_to_create:
                Card.objects.bulk_create(cards_to_create, ignore_conflicts=True)

            if unknown_cards_to_log:
                unknown_records = []
                for grp_id, context in unknown_cards_to_log:
                    card = Card.objects.get(grp_id=grp_id)
                    unknown_records.append(
                        UnknownCard(
                            card=card,
                            match=match,
                            deck=deck,
                            import_session=self.import_session,
                            raw_data=context,
                            is_resolved=False,
                        )
                    )
                UnknownCard.objects.bulk_create(unknown_records, ignore_conflicts=True)
                self.stdout.write(
                    self.style.WARNING(f"Logged {len(unknown_records)} unknown cards for review")
                )

        # ── Special objects: tokens/emblems get generated names; others try Scryfall ──
        for grp_id, inst_data in missing_special.items():
            obj_type = inst_data.get("type", "")
            source_grp_id = inst_data.get("source_grp_id")

            if obj_type in _TOKEN_OBJECT_TYPES:
                name = generate_token_name(inst_data)
                Card.objects.get_or_create(
                    grp_id=grp_id,
                    defaults={
                        "name": name,
                        "is_token": True,
                        "object_type": obj_type,
                        "source_grp_id": source_grp_id,
                    },
                )
            else:
                card_data = scryfall.get_card_by_arena_id(grp_id)
                if card_data:
                    Card.objects.get_or_create(
                        grp_id=grp_id,
                        defaults={
                            "name": card_data.get("name"),
                            "mana_cost": card_data.get("mana_cost"),
                            "cmc": card_data.get("cmc"),
                            "type_line": card_data.get("type_line"),
                            "colors": card_data.get("colors", []),
                            "color_identity": card_data.get("color_identity", []),
                            "set_code": card_data.get("set_code"),
                            "rarity": card_data.get("rarity"),
                            "oracle_text": card_data.get("oracle_text"),
                            "power": card_data.get("power"),
                            "toughness": card_data.get("toughness"),
                            "scryfall_id": card_data.get("scryfall_id"),
                            "image_uri": card_data.get("image_uri"),
                            "object_type": obj_type,
                        },
                    )
                else:
                    # For Omen back faces, try the front face (grpId - 1) for the real name.
                    name = None
                    effective_source = source_grp_id
                    if obj_type == "GameObjectType_Omen":
                        front_data = scryfall.get_card_by_arena_id(grp_id - 1)
                        if front_data and " // " in (front_data.get("name") or ""):
                            name = front_data["name"].split(" // ")[1]
                            effective_source = grp_id - 1
                    if name is None:
                        label = obj_type.replace("GameObjectType_", "") if obj_type else "Unknown"
                        name = f"[{label}] ({grp_id})"
                    Card.objects.get_or_create(
                        grp_id=grp_id,
                        defaults={
                            "name": name,
                            "object_type": obj_type,
                            "source_grp_id": effective_source,
                        },
                    )

    def _import_actions(self, match: Match, match_data: MatchData):
        """Import game actions for a match."""
        significant_types = {
            "ActionType_Cast",
            "ActionType_Play",
            "ActionType_Attack",
            "ActionType_Block",
            "ActionType_Activate",
            "ActionType_Activate_Mana",
            "ActionType_Resolution",
        }

        seen = set()
        actions_to_create = []

        for action in match_data.actions:
            key = (
                action.get("game_state_id"),
                action.get("action_type"),
                action.get("instance_id"),
            )

            action_type = action.get("action_type", "")
            if key in seen or action_type not in significant_types:
                continue
            seen.add(key)

            card_grp_id = action.get("card_grp_id")

            actions_to_create.append(
                GameAction(
                    match=match,
                    game_state_id=action.get("game_state_id"),
                    turn_number=action.get("turn_number"),
                    phase=action.get("phase"),
                    step=action.get("step"),
                    active_player_seat=action.get("active_player"),
                    seat_id=action.get("seat_id"),
                    action_type=action_type,
                    instance_id=action.get("instance_id"),
                    card_id=card_grp_id,
                    ability_grp_id=action.get("ability_grp_id"),
                    mana_cost=action.get("mana_cost"),
                    timestamp_ms=action.get("timestamp"),
                )
            )

        GameAction.objects.bulk_create(actions_to_create)

    def _import_life_changes(self, match: Match, match_data: MatchData):
        """Import life total changes for a match."""
        prev_life = {}
        changes_to_create = []

        for lc in match_data.life_changes:
            seat_id = lc.get("seat_id")
            life_total = lc.get("life_total")

            if seat_id is None or life_total is None:
                continue

            change = None
            if seat_id in prev_life:
                change = life_total - prev_life[seat_id]
                if change == 0:
                    continue

            prev_life[seat_id] = life_total

            changes_to_create.append(
                LifeChange(
                    match=match,
                    game_state_id=lc.get("game_state_id"),
                    turn_number=lc.get("turn_number"),
                    seat_id=seat_id,
                    life_total=life_total,
                    change_amount=change,
                )
            )

        LifeChange.objects.bulk_create(changes_to_create)

    def _import_zone_transfers(self, match: Match, match_data: MatchData):
        """Import zone transfers for a match."""
        # Pre-validate: only reference card_grp_ids that actually exist in the cards table.
        # Skipped object types (Ability, TriggerHolder, RevealedCard) are never inserted,
        # so their grpIds would violate the FK constraint.
        candidate_ids = {
            zt.get("card_grp_id") for zt in match_data.zone_transfers if zt.get("card_grp_id")
        }
        valid_card_ids = set(
            Card.objects.filter(grp_id__in=candidate_ids).values_list("grp_id", flat=True)
        )

        seen = set()
        transfers_to_create = []

        for zt in match_data.zone_transfers:
            key = (zt.get("game_state_id"), zt.get("instance_id"), zt.get("category"))

            if key in seen:
                continue
            seen.add(key)

            card_grp_id = zt.get("card_grp_id")
            if card_grp_id not in valid_card_ids:
                card_grp_id = None

            transfers_to_create.append(
                ZoneTransfer(
                    match=match,
                    game_state_id=zt.get("game_state_id"),
                    turn_number=zt.get("turn_number"),
                    instance_id=zt.get("instance_id"),
                    card_id=card_grp_id,
                    from_zone=zt.get("from_zone"),
                    to_zone=zt.get("to_zone"),
                    category=zt.get("category"),
                )
            )

        ZoneTransfer.objects.bulk_create(transfers_to_create)

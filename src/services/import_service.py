"""
Data Import Service.

Coordinates log parsing, card lookup, and database storage.
"""

import json
import logging
from typing import Dict, Optional, Set, Tuple

from ..db.database import DatabaseManager, get_db, init_db
from ..parser.log_parser import MatchData, parse_log_file
from .scryfall import ScryfallBulkService, get_scryfall

logger = logging.getLogger(__name__)

# GameObjectTypes that are purely internal engine state — never look up in Scryfall.
_SKIP_OBJECT_TYPES = frozenset(
    {
        "GameObjectType_TriggerHolder",
        "GameObjectType_Ability",
        "GameObjectType_RevealedCard",
    }
)

# GameObjectTypes that are tokens/emblems created by card abilities.
# They don't exist in Scryfall; we build their name from game-state data.
_TOKEN_OBJECT_TYPES = frozenset(
    {
        "GameObjectType_Token",
        "GameObjectType_Emblem",
    }
)

_COLOR_LABELS: Dict[str, str] = {
    "CardColor_White": "White",
    "CardColor_Blue": "Blue",
    "CardColor_Black": "Black",
    "CardColor_Red": "Red",
    "CardColor_Green": "Green",
}
_CARD_TYPE_LABELS: Dict[str, str] = {
    "CardType_Creature": "Creature",
    "CardType_Artifact": "Artifact",
    "CardType_Enchantment": "Enchantment",
    "CardType_Land": "Land",
    "CardType_Planeswalker": "Planeswalker",
    "CardType_Instant": "Instant",
    "CardType_Sorcery": "Sorcery",
}


def generate_token_name(inst_data: dict) -> str:
    """Build a human-readable name for a token from its game-state data.

    Example outputs:
        "1/1 Red Goblin Creature Token"
        "Lander Artifact Token"
        "Emblem"
    """
    if inst_data.get("type") == "GameObjectType_Emblem":
        return "Emblem"

    parts = []
    power = inst_data.get("power")
    toughness = inst_data.get("toughness")
    if power is not None and toughness is not None:
        parts.append(f"{power}/{toughness}")

    colors = [_COLOR_LABELS.get(c, c) for c in (inst_data.get("colors") or [])]
    parts.extend(colors)

    subtypes = [s.replace("SubType_", "") for s in (inst_data.get("subtypes") or [])]
    card_types = [
        _CARD_TYPE_LABELS.get(t, t.replace("CardType_", ""))
        for t in (inst_data.get("card_types") or [])
    ]
    parts.extend(subtypes)
    parts.extend(card_types)
    parts.append("Token")
    return " ".join(parts)


class DataImportService:
    """
    Service for importing MTG Arena log data into the database.
    """

    def __init__(
        self, db: Optional[DatabaseManager] = None, scryfall: Optional[ScryfallBulkService] = None
    ):
        """
        Initialize the import service.

        Args:
            db: Database manager instance
            scryfall: Scryfall service instance
        """
        self.db = db or get_db()
        self.scryfall = scryfall or get_scryfall()
        self._imported_matches: Set[str] = set()
        self._load_existing_matches()

    def _load_existing_matches(self):
        """Load IDs of already imported matches."""
        try:
            cursor = self.db.execute("SELECT match_id FROM matches")
            self._imported_matches = {row["match_id"] for row in cursor.fetchall()}
            logger.info(f"Found {len(self._imported_matches)} existing matches")
        except Exception as e:
            logger.warning(f"Failed to load existing matches: {e}")

    def import_log_file(self, log_path: str, skip_existing: bool = True) -> int:
        """
        Import all matches from a log file.

        Args:
            log_path: Path to the Player.log file
            skip_existing: Whether to skip already imported matches

        Returns:
            Number of matches imported
        """
        logger.info(f"Parsing log file: {log_path}")
        matches = parse_log_file(log_path)
        logger.info(f"Found {len(matches)} matches in log file")

        imported_count = 0
        for match in matches:
            if skip_existing and match.match_id in self._imported_matches:
                logger.debug(f"Skipping existing match: {match.match_id}")
                continue

            try:
                self._import_match(match)
                self._imported_matches.add(match.match_id)
                imported_count += 1
            except Exception as e:
                logger.error(f"Failed to import match {match.match_id}: {e}")

        self.db.commit()
        logger.info(f"Imported {imported_count} new matches")
        return imported_count

    def _import_match(self, match: MatchData):
        """Import a single match into the database."""
        # First, ensure deck exists
        deck_db_id = None
        if match.deck_id:
            deck_db_id = self._ensure_deck(match)

        # Collect all unique card IDs and ensure they're in the cards table
        card_grp_ids, special_objects = self._collect_card_ids(match)
        self._ensure_cards(card_grp_ids, special_objects)

        # Calculate duration
        duration = None
        if match.start_time and match.end_time:
            duration = int((match.end_time - match.start_time).total_seconds())

        # Insert match record
        cursor = self.db.execute(
            """
            INSERT INTO matches (
                match_id, game_number,
                player_seat_id, player_name, player_user_id,
                opponent_seat_id, opponent_name, opponent_user_id,
                deck_id, event_id, format, match_type,
                result, winning_team_id, winning_reason,
                start_time, end_time, duration_seconds, total_turns
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                match.match_id,
                1,
                match.player_seat_id,
                match.player_name,
                match.player_user_id,
                match.opponent_seat_id,
                match.opponent_name,
                match.opponent_user_id,
                deck_db_id,
                match.event_id,
                match.format,
                match.match_type,
                match.result,
                match.winning_team_id,
                match.winning_reason,
                match.start_time,
                match.end_time,
                duration,
                match.total_turns,
            ),
        )

        match_db_id = cursor.lastrowid

        # Import actions (limit to significant actions to avoid bloat)
        self._import_actions(match_db_id, match)

        # Import life changes
        self._import_life_changes(match_db_id, match)

        # Import zone transfers
        self._import_zone_transfers(match_db_id, match)

        logger.info(f"Imported match {match.match_id}: {match.result or 'incomplete'}")

    def _ensure_deck(self, match: MatchData) -> Optional[int]:
        """Ensure deck exists in database, return its ID."""
        if not match.deck_id:
            return None

        # Check if deck already exists
        cursor = self.db.execute("SELECT id FROM decks WHERE deck_id = ?", (match.deck_id,))
        row = cursor.fetchone()
        if row:
            return row["id"]

        # Insert new deck
        cursor = self.db.execute(
            """
            INSERT INTO decks (deck_id, name, format)
            VALUES (?, ?, ?)
        """,
            (match.deck_id, match.deck_name, match.format),
        )

        deck_db_id = cursor.lastrowid

        # Insert deck cards
        for card in match.deck_cards:
            card_id = card.get("cardId")
            quantity = card.get("quantity", 1)
            if card_id:
                self.db.execute(
                    """
                    INSERT OR IGNORE INTO deck_cards (deck_id, card_grp_id, quantity, is_sideboard)
                    VALUES (?, ?, ?, ?)
                """,
                    (deck_db_id, card_id, quantity, False),
                )

        return deck_db_id

    def _collect_card_ids(self, match: MatchData) -> Tuple[Set[int], Dict[int, dict]]:
        """Collect card IDs from match data.

        Returns:
            real_card_ids: grpIds that should be looked up in Scryfall
                (GameObjectType_Card, deck cards, or unrecognised types).
            special_objects: grpId → instance_data for tokens, emblems, and
                named card-face types (Adventure, MDFCBack, etc.) that may or
                may not be in Scryfall.
        """
        real_card_ids: Set[int] = set()
        special_objects: Dict[int, dict] = {}

        # Deck cards are always real cards
        for card in match.deck_cards:
            if card.get("cardId"):
                real_card_ids.add(card["cardId"])

        # Categorise each card instance by its Arena object type
        for inst_data in match.card_instances.values():
            grp_id = inst_data.get("grp_id")
            obj_type = inst_data.get("type", "")
            if not grp_id:
                continue
            if obj_type in _SKIP_OBJECT_TYPES:
                continue  # Engine-only objects — ignore entirely
            if obj_type == "GameObjectType_Card":
                # Confirmed real card; remove from special_objects if seen earlier
                real_card_ids.add(grp_id)
                special_objects.pop(grp_id, None)
            elif obj_type == "GameObjectType_Omen":
                # Omen back-face grpIds share their Arena ID with the front-face
                # GameObjectType_Card (the spell being cast). Card is processed first,
                # so we must override: back-face IDs are not in Scryfall.
                real_card_ids.discard(grp_id)
                special_objects[grp_id] = inst_data
            elif grp_id not in real_card_ids:
                # Token, emblem, adventure face, MDFC back, etc.
                # First occurrence wins; real-card sighting takes priority above.
                special_objects.setdefault(grp_id, inst_data)

        # Actions may reference grpIds not captured as card instances
        for action in match.actions:
            cid = action.get("card_grp_id")
            if cid and cid not in special_objects:
                real_card_ids.add(cid)

        return real_card_ids, special_objects

    def _generate_token_name(self, inst_data: dict) -> str:
        return generate_token_name(inst_data)

    def _ensure_cards(self, real_card_ids: Set[int], special_objects: Dict[int, dict]):
        """Ensure all referenced cards/objects exist in the database.

        * real_card_ids: looked up in Scryfall; fall back to "Unknown Card (N)".
        * special_objects: tokens/emblems get a generated name; other face types
          try Scryfall first and use a descriptive placeholder on failure.
        """
        all_ids = real_card_ids | set(special_objects)
        if not all_ids:
            return

        placeholders = ",".join("?" * len(all_ids))
        cursor = self.db.execute(
            f"SELECT grp_id FROM cards WHERE grp_id IN ({placeholders})", tuple(all_ids)
        )
        existing_ids = {row["grp_id"] for row in cursor.fetchall()}

        missing_real = real_card_ids - existing_ids
        missing_special = {gid: d for gid, d in special_objects.items() if gid not in existing_ids}

        if missing_real or missing_special:
            logger.info(
                f"Looking up {len(missing_real)} cards from Scryfall, "
                f"processing {len(missing_special)} special objects..."
            )

        # ── Real cards: look up Scryfall, fall back to Unknown placeholder ──
        for grp_id in missing_real:
            card_data = self.scryfall.get_card_by_arena_id(grp_id)
            if card_data:
                self.db.execute(
                    """
                    INSERT OR REPLACE INTO cards (
                        grp_id, name, mana_cost, cmc, type_line,
                        colors, color_identity, set_code, rarity,
                        oracle_text, power, toughness, scryfall_id, image_uri
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        grp_id,
                        card_data.get("name"),
                        card_data.get("mana_cost"),
                        card_data.get("cmc"),
                        card_data.get("type_line"),
                        json.dumps(card_data.get("colors", [])),
                        json.dumps(card_data.get("color_identity", [])),
                        card_data.get("set_code"),
                        card_data.get("rarity"),
                        card_data.get("oracle_text"),
                        card_data.get("power"),
                        card_data.get("toughness"),
                        card_data.get("scryfall_id"),
                        card_data.get("image_uri"),
                    ),
                )
            else:
                logger.debug(f"Card grp_id={grp_id} not found in Scryfall")
                self.db.execute(
                    "INSERT OR IGNORE INTO cards (grp_id, name) VALUES (?, ?)",
                    (grp_id, f"Unknown Card ({grp_id})"),
                )

        # ── Special objects: tokens/emblems get generated names; others try Scryfall ──
        for grp_id, inst_data in missing_special.items():
            obj_type = inst_data.get("type", "")
            source_grp_id = inst_data.get("source_grp_id")

            if obj_type in _TOKEN_OBJECT_TYPES:
                name = self._generate_token_name(inst_data)
                logger.debug(f"Inserting token grp_id={grp_id} as '{name}'")
                self.db.execute(
                    """
                    INSERT OR IGNORE INTO cards (
                        grp_id, name, is_token, object_type, source_grp_id
                    ) VALUES (?, ?, 1, ?, ?)
                """,
                    (grp_id, name, obj_type, source_grp_id),
                )
            else:
                # Adventure face, MDFC back, Room half, Omen, etc.
                # Try Scryfall first (some face types have Arena IDs in bulk data).
                card_data = self.scryfall.get_card_by_arena_id(grp_id)
                if card_data:
                    self.db.execute(
                        """
                        INSERT OR REPLACE INTO cards (
                            grp_id, name, mana_cost, cmc, type_line,
                            colors, color_identity, set_code, rarity,
                            oracle_text, power, toughness, scryfall_id, image_uri,
                            object_type
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            grp_id,
                            card_data.get("name"),
                            card_data.get("mana_cost"),
                            card_data.get("cmc"),
                            card_data.get("type_line"),
                            json.dumps(card_data.get("colors", [])),
                            json.dumps(card_data.get("color_identity", [])),
                            card_data.get("set_code"),
                            card_data.get("rarity"),
                            card_data.get("oracle_text"),
                            card_data.get("power"),
                            card_data.get("toughness"),
                            card_data.get("scryfall_id"),
                            card_data.get("image_uri"),
                            obj_type,
                        ),
                    )
                else:
                    # Friendly placeholder showing the object type.
                    # For Omen back faces, try the front face (grpId - 1) for the real name.
                    name = None
                    effective_source = source_grp_id
                    if obj_type == "GameObjectType_Omen":
                        front_data = self.scryfall.get_card_by_arena_id(grp_id - 1)
                        if front_data and " // " in (front_data.get("name") or ""):
                            name = front_data["name"].split(" // ")[1]
                            effective_source = grp_id - 1
                    if name is None:
                        label = obj_type.replace("GameObjectType_", "") if obj_type else "Unknown"
                        name = f"[{label}] ({grp_id})"
                    logger.debug(f"Inserting special object grp_id={grp_id} as '{name}'")
                    self.db.execute(
                        """
                        INSERT OR IGNORE INTO cards (
                            grp_id, name, object_type, source_grp_id
                        ) VALUES (?, ?, ?, ?)
                    """,
                        (grp_id, name, obj_type, effective_source),
                    )

    def _import_actions(self, match_db_id: int, match: MatchData):
        """Import game actions for a match."""
        # Filter to significant actions only
        significant_types = {
            "ActionType_Cast",
            "ActionType_Play",
            "ActionType_Attack",
            "ActionType_Block",
            "ActionType_Activate",
            "ActionType_Activate_Mana",
            "ActionType_Resolution",
        }

        # Deduplicate actions by (game_state_id, action_type, instance_id)
        seen = set()
        for action in match.actions:
            key = (
                action.get("game_state_id"),
                action.get("action_type"),
                action.get("instance_id"),
            )

            action_type = action.get("action_type", "")
            if key in seen or action_type not in significant_types:
                continue
            seen.add(key)

            mana_cost_json = None
            if action.get("mana_cost"):
                mana_cost_json = json.dumps(action["mana_cost"])

            self.db.execute(
                """
                INSERT INTO game_actions (
                    match_id, game_state_id, turn_number, phase, step,
                    active_player_seat, seat_id, action_type,
                    instance_id, card_grp_id, ability_grp_id, mana_cost, timestamp_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    match_db_id,
                    action.get("game_state_id"),
                    action.get("turn_number"),
                    action.get("phase"),
                    action.get("step"),
                    action.get("active_player"),
                    action.get("seat_id"),
                    action.get("action_type"),
                    action.get("instance_id"),
                    action.get("card_grp_id"),
                    action.get("ability_grp_id"),
                    mana_cost_json,
                    action.get("timestamp"),
                ),
            )

    def _import_life_changes(self, match_db_id: int, match: MatchData):
        """Import life total changes for a match."""
        prev_life = {}

        for lc in match.life_changes:
            seat_id = lc.get("seat_id")
            life_total = lc.get("life_total")

            if seat_id is None or life_total is None:
                continue

            # Calculate change
            change = None
            if seat_id in prev_life:
                change = life_total - prev_life[seat_id]
                # Only record if there was an actual change
                if change == 0:
                    continue

            prev_life[seat_id] = life_total

            self.db.execute(
                """
                INSERT INTO life_changes (
                    match_id, game_state_id, turn_number, seat_id, life_total, change_amount
                ) VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    match_db_id,
                    lc.get("game_state_id"),
                    lc.get("turn_number"),
                    seat_id,
                    life_total,
                    change,
                ),
            )

    def _import_zone_transfers(self, match_db_id: int, match: MatchData):
        """Import zone transfers for a match."""
        # Deduplicate by (game_state_id, instance_id, category)
        seen = set()

        for zt in match.zone_transfers:
            key = (zt.get("game_state_id"), zt.get("instance_id"), zt.get("category"))

            if key in seen:
                continue
            seen.add(key)

            self.db.execute(
                """
                INSERT INTO zone_transfers (
                    match_id, game_state_id, turn_number,
                    instance_id, card_grp_id, from_zone, to_zone, category
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    match_db_id,
                    zt.get("game_state_id"),
                    zt.get("turn_number"),
                    zt.get("instance_id"),
                    zt.get("card_grp_id"),
                    zt.get("from_zone"),
                    zt.get("to_zone"),
                    zt.get("category"),
                ),
            )


def import_log(log_path: str, db_path: Optional[str] = None) -> int:
    """
    Convenience function to import a log file.

    Args:
        log_path: Path to Player.log file
        db_path: Optional path to database file

    Returns:
        Number of matches imported
    """
    db = init_db(db_path)
    service = DataImportService(db)
    return service.import_log_file(log_path)

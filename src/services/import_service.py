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
    "CardType_Battle": "Battle",
}
_MANA_COLOR_SYMBOLS: Dict[str, str] = {
    "ManaColor_White": "W",
    "ManaColor_Blue": "U",
    "ManaColor_Black": "B",
    "ManaColor_Red": "R",
    "ManaColor_Green": "G",
    "ManaColor_Colorless": "C",
}


def format_mana_cost(mana_cost: list) -> str:
    """Convert Arena mana cost JSON to standard MTG notation like {2}{U}{R}."""
    parts = []
    for pip in mana_cost or []:
        colors = pip.get("color", [])
        count = pip.get("count", 1)
        if not colors or colors == ["ManaColor_Generic"]:
            parts.append(f"{{{count}}}")
        else:
            for _ in range(count):
                for color in colors:
                    symbol = _MANA_COLOR_SYMBOLS.get(color, "?")
                    parts.append(f"{{{symbol}}}")
    return "".join(parts)


def build_type_line(inst_data: dict) -> str:
    """Build a MTG-style type line from game-state data (e.g. 'Legendary Creature — Human Villain')."""
    super_types = [st.replace("SuperType_", "") for st in (inst_data.get("super_types") or [])]
    card_types = [
        _CARD_TYPE_LABELS.get(t, t.replace("CardType_", ""))
        for t in (inst_data.get("card_types") or [])
    ]
    subtypes = [s.replace("SubType_", "") for s in (inst_data.get("subtypes") or [])]

    main = " ".join(super_types + card_types)
    if subtypes:
        return f"{main} \u2014 {' '.join(subtypes)}"
    return main


def generate_unknown_card_description(
    grp_id: int, inst_data: dict, mana_cost_json: Optional[list] = None
) -> str:
    """Build a descriptive placeholder name for a card not found in Scryfall.

    Produces names like:
        "Legendary Creature — Human Villain [97852]"
        "Basic Land — Plains [98592]"
        "{3}{U}{U} Creature — Wizard [97854]"
    Falls back to "Unknown Card (97852)" when no type info is available.
    """
    parts = []

    super_types = [st.replace("SuperType_", "") for st in (inst_data.get("super_types") or [])]
    card_types = [
        _CARD_TYPE_LABELS.get(t, t.replace("CardType_", ""))
        for t in (inst_data.get("card_types") or [])
    ]
    subtypes = [s.replace("SubType_", "") for s in (inst_data.get("subtypes") or [])]

    if not card_types and not super_types:
        return f"Unknown Card ({grp_id})"

    if mana_cost_json:
        mana_str = format_mana_cost(mana_cost_json)
        if mana_str:
            parts.append(mana_str)

    parts.extend(super_types)
    parts.extend(card_types)
    if subtypes:
        parts.append("\u2014")
        parts.extend(subtypes)

    power = inst_data.get("power")
    toughness = inst_data.get("toughness")
    if power is not None and toughness is not None:
        parts.append(f"({power}/{toughness})")

    return f"{' '.join(parts)} [{grp_id}]"


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
        real_cards, special_objects = self._collect_card_ids(match)
        cast_mana_costs = self._collect_cast_mana_costs(match)
        self._ensure_cards(real_cards, special_objects, cast_mana_costs)

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

    def _collect_card_ids(self, match: MatchData) -> Tuple[Dict[int, dict], Dict[int, dict]]:
        """Collect card IDs from match data.

        Returns:
            real_cards: grpId → instance_data for cards that should be looked up in Scryfall
                (GameObjectType_Card, deck cards, or unrecognised types).
            special_objects: grpId → instance_data for tokens, emblems, and
                named card-face types (Adventure, MDFCBack, etc.) that may or
                may not be in Scryfall.
        """
        real_cards: Dict[int, dict] = {}
        special_objects: Dict[int, dict] = {}

        # Deck cards are always real cards (no instance data available)
        for card in match.deck_cards:
            if card.get("cardId"):
                real_cards.setdefault(card["cardId"], {})

        # Categorise each card instance by its Arena object type
        for inst_data in match.card_instances.values():
            grp_id = inst_data.get("grp_id")
            obj_type = inst_data.get("type", "")
            if not grp_id:
                continue
            if obj_type in _SKIP_OBJECT_TYPES:
                continue  # Engine-only objects — ignore entirely
            if obj_type == "GameObjectType_Card":
                # Confirmed real card; remove from special_objects if seen earlier.
                # Prefer instance with the most data (first public sighting wins).
                if grp_id not in real_cards or not real_cards[grp_id].get("card_types"):
                    real_cards[grp_id] = inst_data
                special_objects.pop(grp_id, None)
            elif obj_type == "GameObjectType_Omen":
                # Omen back-face grpIds share their Arena ID with the front-face
                # GameObjectType_Card (the spell being cast). Card is processed first,
                # so we must override: back-face IDs are not in Scryfall.
                real_cards.pop(grp_id, None)
                special_objects[grp_id] = inst_data
            elif grp_id not in real_cards:
                # Token, emblem, adventure face, MDFC back, etc.
                # First occurrence wins; real-card sighting takes priority above.
                special_objects.setdefault(grp_id, inst_data)

        # Actions may reference grpIds not captured as card instances
        for action in match.actions:
            cid = action.get("card_grp_id")
            if cid and cid not in special_objects:
                real_cards.setdefault(cid, {})

        return real_cards, special_objects

    def _generate_token_name(self, inst_data: dict) -> str:
        return generate_token_name(inst_data)

    def _collect_cast_mana_costs(self, match: MatchData) -> Dict[int, list]:
        """Collect mana costs paid for cast actions, keyed by card grpId.

        Returns the first observed mana cost for each card. Used to enrich
        unknown card placeholders with partial mana cost information.
        """
        costs: Dict[int, list] = {}
        for action in match.actions:
            if action.get("action_type") != "ActionType_Cast":
                continue
            cid = action.get("card_grp_id")
            mana_cost = action.get("mana_cost")
            if cid and mana_cost and cid not in costs:
                costs[cid] = mana_cost
        return costs

    def _ensure_cards(
        self,
        real_cards: Dict[int, dict],
        special_objects: Dict[int, dict],
        cast_mana_costs: Optional[Dict[int, list]] = None,
    ):
        """Ensure all referenced cards/objects exist in the database.

        * real_cards: grpId → inst_data; looked up in Scryfall; fall back to a
          descriptive placeholder built from game-state data.
        * special_objects: tokens/emblems get a generated name; other face types
          try Scryfall first and use a descriptive placeholder on failure.
        * cast_mana_costs: optional grpId → mana_cost list from cast actions,
          used to enrich unknown card placeholders.
        """
        if cast_mana_costs is None:
            cast_mana_costs = {}

        all_ids = set(real_cards) | set(special_objects)
        if not all_ids:
            return

        placeholders = ",".join("?" * len(all_ids))
        cursor = self.db.execute(
            f"SELECT grp_id, name FROM cards WHERE grp_id IN ({placeholders})", tuple(all_ids)
        )
        rows = cursor.fetchall()
        existing_ids = {row["grp_id"] for row in rows}
        # Track which existing cards are still unknown placeholders so we can
        # upgrade them if we have richer game-state data in this match.
        unknown_placeholder_ids = {
            row["grp_id"] for row in rows if row["name"].startswith("Unknown Card (")
        }

        missing_real = {gid: real_cards[gid] for gid in (set(real_cards) - existing_ids)}
        missing_special = {gid: d for gid, d in special_objects.items() if gid not in existing_ids}
        # Real cards that were stored as bare placeholders and may now have better data.
        upgradeable_real = {
            gid: real_cards[gid] for gid in (set(real_cards) & unknown_placeholder_ids)
        }

        if missing_real or missing_special:
            logger.info(
                f"Looking up {len(missing_real)} cards from Scryfall, "
                f"processing {len(missing_special)} special objects..."
            )

        # ── Real cards: look up Scryfall, fall back to descriptive placeholder ──
        for grp_id, inst_data in missing_real.items():
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
                logger.debug(f"Card grp_id={grp_id} not found in Scryfall, building placeholder")
                mana_cost_json = cast_mana_costs.get(grp_id)
                name = generate_unknown_card_description(grp_id, inst_data, mana_cost_json)
                type_line = build_type_line(inst_data) or None
                colors = inst_data.get("colors") or []
                color_json = json.dumps([_COLOR_LABELS.get(c, c) for c in colors] if colors else [])
                power = inst_data.get("power")
                toughness = inst_data.get("toughness")
                mana_cost_str = format_mana_cost(mana_cost_json) if mana_cost_json else None
                self.db.execute(
                    """
                    INSERT OR IGNORE INTO cards (
                        grp_id, name, type_line, colors, power, toughness, mana_cost
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (grp_id, name, type_line, color_json, power, toughness, mana_cost_str),
                )

        # ── Special objects: tokens/emblems get generated names; others try Scryfall ──
        # ── Upgrade existing "Unknown Card" placeholders with better game-state data ──
        for grp_id, inst_data in upgradeable_real.items():
            mana_cost_json = cast_mana_costs.get(grp_id)
            name = generate_unknown_card_description(grp_id, inst_data, mana_cost_json)
            # Only update if we have a more informative name than the bare placeholder
            if name == f"Unknown Card ({grp_id})":
                continue
            type_line = build_type_line(inst_data) or None
            colors = inst_data.get("colors") or []
            color_json = json.dumps([_COLOR_LABELS.get(c, c) for c in colors] if colors else [])
            power = inst_data.get("power")
            toughness = inst_data.get("toughness")
            mana_cost_str = format_mana_cost(mana_cost_json) if mana_cost_json else None
            logger.debug(f"Upgrading placeholder grp_id={grp_id} to '{name}'")
            self.db.execute(
                """
                UPDATE cards
                SET name = ?, type_line = ?, colors = ?, power = ?, toughness = ?, mana_cost = ?
                WHERE grp_id = ? AND name = ?
            """,
                (
                    name,
                    type_line,
                    color_json,
                    power,
                    toughness,
                    mana_cost_str,
                    grp_id,
                    f"Unknown Card ({grp_id})",
                ),
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

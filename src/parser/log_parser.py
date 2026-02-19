"""
MTG Arena Log Parser.

Parses the Player.log file from MTG Arena to extract game data.
The log file contains JSON events embedded in log lines with various prefixes.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from ..exceptions import InvalidLogFormatError

logger = logging.getLogger(__name__)


@dataclass
class ParsedEvent:
    """Represents a parsed event from the log file."""

    event_type: str
    data: Dict[str, Any]
    timestamp: Optional[int] = None  # milliseconds
    line_number: int = 0
    raw_line: str = ""


@dataclass
class MatchData:
    """Aggregated data for a single match."""

    match_id: str
    player_name: Optional[str] = None
    player_seat_id: Optional[int] = None
    player_user_id: Optional[str] = None
    opponent_name: Optional[str] = None
    opponent_seat_id: Optional[int] = None
    opponent_user_id: Optional[str] = None
    event_id: Optional[str] = None  # Format like "Ladder"
    format: Optional[str] = None
    match_type: Optional[str] = None
    deck_name: Optional[str] = None
    deck_id: Optional[str] = None
    deck_cards: List[Dict] = field(default_factory=list)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    result: Optional[str] = None  # 'win', 'loss', 'draw'
    winning_team_id: Optional[int] = None
    winning_reason: Optional[str] = None
    total_turns: int = 0
    game_states: List[Dict] = field(default_factory=list)
    actions: List[Dict] = field(default_factory=list)
    life_changes: List[Dict] = field(default_factory=list)
    zone_transfers: List[Dict] = field(default_factory=list)
    card_instances: Dict[int, Dict] = field(default_factory=dict)  # instance_id -> card data


class MTGALogParser:
    """
    Parser for MTG Arena log files.

    Designed to handle large files efficiently using generators.
    """

    # Patterns to identify different event types
    PATTERNS = {
        "match_state": re.compile(r"matchGameRoomStateChangedEvent"),
        "gre_event": re.compile(r"greToClientEvent"),
        "deck_set": re.compile(r"EventSetDeckV2"),
        "deck_upsert": re.compile(r"DeckUpsertDeckV2"),
        "course_deck": re.compile(r'"CourseDeck"'),
        "timestamp_line": re.compile(
            r"\[UnityCrossThreadLogger\](\d+/\d+/\d+\s+\d+:\d+:\d+\s+[AP]M)"
        ),
        "json_start": re.compile(r"^\s*\{"),
    }

    # Pattern to extract JSON from log lines
    JSON_EXTRACT = re.compile(r"(\{.*\})\s*$")

    def __init__(self, log_path: str):
        """
        Initialize parser with path to log file.

        Args:
            log_path: Path to Player.log file

        Raises:
            FileNotFoundError: If log file doesn't exist
            InvalidLogFormatError: If file is not a valid log file
        """
        self.log_path = Path(log_path)
        if not self.log_path.exists():
            raise FileNotFoundError(f"Log file not found: {log_path}")

        # Validate file is readable and not empty
        file_size = self.log_path.stat().st_size
        if file_size == 0:
            raise InvalidLogFormatError("Log file is empty", details=str(log_path))

        # Check if file appears to be a valid MTGA log (basic validation)
        try:
            with open(self.log_path, "r", encoding="utf-8", errors="ignore") as f:
                # Read first few KB to validate
                header = f.read(4096)
                if not header:
                    raise InvalidLogFormatError("Cannot read log file", details=str(log_path))
                # Look for MTGA log indicators
                if "Unity" not in header and "MTGA" not in header and "Wizards" not in header:
                    logger.warning(f"File may not be a valid MTGA log: {log_path}")
        except UnicodeDecodeError as e:
            raise InvalidLogFormatError(
                "Log file encoding error", details=f"Cannot decode file: {e}"
            )

        self.current_match: Optional[MatchData] = None
        self.completed_matches: List[MatchData] = []
        self._last_timestamp: Optional[datetime] = None
        self._last_turn_number: int = 0  # Track last seen turn for messages without turnInfo
        self._parse_errors: List[Dict] = []  # Track non-fatal parse errors

    def parse_events(self) -> Generator[ParsedEvent, None, None]:
        """
        Generator that yields parsed events from the log file.

        Yields:
            ParsedEvent objects for each relevant event found
        """
        with open(self.log_path, "r", encoding="utf-8", errors="ignore") as f:
            line_number = 0
            current_json_lines = []
            in_json_block = False

            for line in f:
                line_number += 1
                stripped = line.strip()

                # Track timestamps
                ts_match = self.PATTERNS["timestamp_line"].match(line)
                if ts_match:
                    try:
                        self._last_timestamp = datetime.strptime(
                            ts_match.group(1), "%m/%d/%Y %I:%M:%S %p"
                        ).replace(tzinfo=timezone.utc)
                    except ValueError:
                        pass

                # Handle multi-line JSON blocks
                if in_json_block:
                    current_json_lines.append(stripped)
                    # Try to parse accumulated JSON
                    try:
                        full_json = "\n".join(current_json_lines)
                        data = json.loads(full_json)
                        in_json_block = False
                        event = self._classify_event(data, line_number, stripped)
                        if event:
                            yield event
                        current_json_lines = []
                    except json.JSONDecodeError:
                        # Not complete yet, continue accumulating
                        pass
                    continue

                # Check for JSON starting on this line
                if self.PATTERNS["json_start"].match(stripped):
                    try:
                        data = json.loads(stripped)
                        event = self._classify_event(data, line_number, stripped)
                        if event:
                            yield event
                    except json.JSONDecodeError:
                        # Multi-line JSON, start accumulating
                        in_json_block = True
                        current_json_lines = [stripped]
                    continue

                # Try to extract JSON from the end of the line
                json_match = self.JSON_EXTRACT.search(stripped)
                if json_match:
                    try:
                        data = json.loads(json_match.group(1))
                        event = self._classify_event(data, line_number, stripped)
                        if event:
                            yield event
                    except json.JSONDecodeError:
                        pass

    def _classify_event(self, data: Dict, line_number: int, raw_line: str) -> Optional[ParsedEvent]:
        """Classify and create a ParsedEvent from JSON data."""
        event_type = None
        timestamp = data.get("timestamp")

        if "matchGameRoomStateChangedEvent" in data:
            event_type = "match_state"
        elif "greToClientEvent" in data:
            event_type = "gre_event"
        elif "CourseDeck" in data or "CourseDeckSummary" in data:
            event_type = "course_deck"
        elif "request" in data and "DeckUpsertDeckV2" in str(data):
            event_type = "deck_upsert"
        elif "request" in data and "EventSetDeckV2" in str(data):
            event_type = "deck_set"
        elif "gameStateMessage" in str(data):
            event_type = "game_state"

        if event_type:
            return ParsedEvent(
                event_type=event_type,
                data=data,
                timestamp=int(timestamp) if timestamp else None,
                line_number=line_number,
                raw_line=raw_line[:200],  # Truncate for memory
            )

        return None

    def parse_matches(self) -> List[MatchData]:
        """
        Parse the log file and extract all match data.

        Returns:
            List of MatchData objects for completed matches
        """
        self.completed_matches = []
        self.current_match = None
        self._parse_errors = []

        for event in self.parse_events():
            try:
                self._process_event(event)
            except Exception as e:
                # Log error but continue processing
                error_info = {
                    "event_type": event.event_type,
                    "line_number": event.line_number,
                    "error": str(e),
                }
                self._parse_errors.append(error_info)
                logger.warning(f"Error processing event at line {event.line_number}: {e}")

        # If there's an ongoing match at end of file, add it
        if self.current_match:
            self.completed_matches.append(self.current_match)

        if self._parse_errors:
            logger.info(f"Completed with {len(self._parse_errors)} non-fatal parse errors")

        return self.completed_matches

    def get_parse_errors(self) -> List[Dict]:
        """Get list of non-fatal errors encountered during parsing."""
        return self._parse_errors.copy()

    def _process_event(self, event: ParsedEvent):
        """Process a single event and update match data."""
        if event.event_type == "match_state":
            self._process_match_state(event)
        elif event.event_type == "gre_event":
            self._process_gre_event(event)
        elif event.event_type in ("course_deck", "deck_set", "deck_upsert"):
            self._process_deck_event(event)

    def _process_match_state(self, event: ParsedEvent):
        """Process matchGameRoomStateChangedEvent."""
        data = event.data.get("matchGameRoomStateChangedEvent", {})
        game_room_info = data.get("gameRoomInfo", {})
        config = game_room_info.get("gameRoomConfig", {})
        state_type = game_room_info.get("stateType", "")

        match_id = config.get("matchId")
        if not match_id:
            return

        # Check if this is a new match
        if self.current_match is None or self.current_match.match_id != match_id:
            # Save previous match if exists
            if self.current_match:
                self.completed_matches.append(self.current_match)

            # Start new match
            self.current_match = MatchData(match_id=match_id)
            self._last_turn_number = 0
            if event.timestamp:
                self.current_match.start_time = datetime.fromtimestamp(
                    event.timestamp / 1000, tz=timezone.utc
                )
            elif self._last_timestamp:
                self.current_match.start_time = self._last_timestamp

        # Extract player information from reservedPlayers
        reserved_players = config.get("reservedPlayers", [])
        for player in reserved_players:
            player_name = player.get("playerName", "")
            user_id = player.get("userId", "")
            seat_id = player.get("systemSeatId")
            event_id = player.get("eventId", "")

            # Determine if this is the user or opponent
            # The user typically has a different platform or is seat 2 for their view
            # We'll use the convention that the logged-in user sees themselves as seat 2
            # in their own logs (based on the log analysis)
            if seat_id == 2:
                self.current_match.player_name = player_name
                self.current_match.player_seat_id = seat_id
                self.current_match.player_user_id = user_id
            else:
                self.current_match.opponent_name = player_name
                self.current_match.opponent_seat_id = seat_id
                self.current_match.opponent_user_id = user_id

            if event_id and not self.current_match.event_id:
                self.current_match.event_id = event_id

        # Check for match completion
        if state_type == "MatchGameRoomStateType_MatchCompleted":
            if event.timestamp:
                self.current_match.end_time = datetime.fromtimestamp(
                    event.timestamp / 1000, tz=timezone.utc
                )

            # Extract result from finalMatchResult if available
            final_result = game_room_info.get("finalMatchResult", {})
            self.current_match.winning_team_id = final_result.get("winningTeamId")

            result_list = final_result.get("resultList", [])
            for result in result_list:
                if result.get("scope") == "MatchScope_Match":
                    winning_team = result.get("winningTeamId")
                    if winning_team == self.current_match.player_seat_id:
                        self.current_match.result = "win"
                    elif winning_team:
                        self.current_match.result = "loss"
                    self.current_match.winning_reason = result.get("reason")

    def _process_gre_event(self, event: ParsedEvent):
        """Process greToClientEvent containing game state updates."""
        if not self.current_match:
            return

        gre_data = event.data.get("greToClientEvent", {})
        messages = gre_data.get("greToClientMessages", [])

        for msg in messages:
            msg_type = msg.get("type", "")

            if msg_type == "GREMessageType_GameStateMessage":
                self._process_game_state_message(msg, event.timestamp)

    def _process_game_state_message(self, msg: Dict, timestamp: Optional[int]):
        """Process a game state message."""
        if not self.current_match:
            return

        game_state = msg.get("gameStateMessage", {})
        game_state_id = game_state.get("gameStateId", 0)

        # Extract turn info
        turn_info = game_state.get("turnInfo", {})
        turn_number = turn_info.get("turnNumber", 0)
        if turn_number > 0:
            self._last_turn_number = turn_number
        elif self._last_turn_number > 0:
            turn_number = self._last_turn_number
        if turn_number > self.current_match.total_turns:
            self.current_match.total_turns = turn_number

        phase = turn_info.get("phase", "")
        step = turn_info.get("step", "")
        active_player = turn_info.get("activePlayer")

        # Extract game info (format, type)
        game_info = game_state.get("gameInfo", {})
        if game_info:
            self.current_match.format = game_info.get("superFormat")
            self.current_match.match_type = game_info.get("type")

        # Extract player life totals (both player and opponent)
        players = game_state.get("players", [])
        for player in players:
            seat = player.get("systemSeatNumber")
            life = player.get("lifeTotal")

            if life is not None and seat is not None:
                self.current_match.life_changes.append(
                    {
                        "game_state_id": game_state_id,
                        "turn_number": turn_number,
                        "seat_id": seat,
                        "life_total": life,
                    }
                )

        # Extract game objects (cards)
        game_objects = game_state.get("gameObjects", [])
        for obj in game_objects:
            instance_id = obj.get("instanceId")
            if instance_id is not None:
                self.current_match.card_instances[instance_id] = {
                    "grp_id": obj.get("grpId"),
                    "name": obj.get("name"),
                    "type": obj.get("type"),
                    "card_types": obj.get("cardTypes", []),
                    "subtypes": obj.get("subtypes", []),
                    "colors": obj.get("color", []),
                    "power": obj.get("power", {}).get("value"),
                    "toughness": obj.get("toughness", {}).get("value"),
                    "owner_seat": obj.get("ownerSeatId"),
                    "controller_seat": obj.get("controllerSeatId"),
                }

        # Extract actions
        actions = game_state.get("actions", [])
        for action in actions:
            seat_id = action.get("seatId")
            action_data = action.get("action", {})

            if action_data:
                action_type = action_data.get("actionType", "")
                instance_id = action_data.get("instanceId")

                # Get card info if we have it
                card_info = self.current_match.card_instances.get(instance_id, {})

                self.current_match.actions.append(
                    {
                        "game_state_id": game_state_id,
                        "turn_number": turn_number,
                        "phase": phase,
                        "step": step,
                        "active_player": active_player,
                        "seat_id": seat_id,
                        "action_type": action_type,
                        "instance_id": instance_id,
                        "card_grp_id": card_info.get("grp_id") or action_data.get("grpId"),
                        "ability_grp_id": action_data.get("abilityGrpId"),
                        "mana_cost": action_data.get("manaCost"),
                        "timestamp": timestamp,
                    }
                )

        # Extract zone transfers from annotations
        annotations = game_state.get("annotations", [])
        for annotation in annotations:
            ann_type = annotation.get("type", [])
            if "AnnotationType_ZoneTransfer" in ann_type:
                details = {d.get("key"): d for d in annotation.get("details", [])}

                affected_ids = annotation.get("affectedIds", [])
                for inst_id in affected_ids:
                    card_info = self.current_match.card_instances.get(inst_id, {})

                    zone_src = None
                    zone_dest = None
                    category = None

                    if "zone_src" in details:
                        zone_src = details["zone_src"].get("valueInt32", [None])[0]
                    if "zone_dest" in details:
                        zone_dest = details["zone_dest"].get("valueInt32", [None])[0]
                    if "category" in details:
                        category = details["category"].get("valueString", [None])[0]

                    self.current_match.zone_transfers.append(
                        {
                            "game_state_id": game_state_id,
                            "turn_number": turn_number,
                            "instance_id": inst_id,
                            "card_grp_id": card_info.get("grp_id"),
                            "from_zone": zone_src,
                            "to_zone": zone_dest,
                            "category": category,
                        }
                    )

    def _process_deck_event(self, event: ParsedEvent):
        """Process deck-related events."""
        if not self.current_match:
            return

        data = event.data

        # Try to find deck info in various formats
        deck_summary = None
        deck_data = None

        if "CourseDeckSummary" in data:
            deck_summary = data.get("CourseDeckSummary", {})
            deck_data = data.get("CourseDeck", {})
        elif "Summary" in data:
            deck_summary = data.get("Summary", {})
            deck_data = data.get("Deck", {})

        if deck_summary:
            self.current_match.deck_name = deck_summary.get("Name")
            self.current_match.deck_id = deck_summary.get("DeckId")

            # Get format from attributes
            attributes = deck_summary.get("Attributes", [])
            for attr in attributes:
                if attr.get("name") == "Format":
                    self.current_match.format = attr.get("value")

        if deck_data:
            main_deck = deck_data.get("MainDeck", [])
            self.current_match.deck_cards = main_deck


def parse_log_file(log_path: str) -> List[MatchData]:
    """
    Convenience function to parse a log file.

    Args:
        log_path: Path to the Player.log file

    Returns:
        List of MatchData objects
    """
    parser = MTGALogParser(log_path)
    return parser.parse_matches()

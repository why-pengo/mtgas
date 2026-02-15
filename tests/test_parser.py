"""
Tests for the MTG Arena log parser.

Tests data extraction from Player.log files, handling of edge cases,
and proper parsing of match data, deck info, and game actions.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.parser.log_parser import MatchData, MTGALogParser  # noqa: E402


class TestMatchDataClass:
    """Tests for the MatchData dataclass."""

    def test_match_data_creation(self):
        """Test basic MatchData creation."""
        match = MatchData(match_id="test-123")
        assert match.match_id == "test-123"
        assert match.player_name is None
        assert match.result is None
        assert match.total_turns == 0
        assert match.actions == []
        assert match.card_instances == {}

    def test_match_data_with_values(self):
        """Test MatchData with all fields populated."""
        match = MatchData(
            match_id="abc-123",
            player_name="TestPlayer",
            opponent_name="Opponent",
            result="win",
            total_turns=10,
            event_id="Ladder",
        )
        assert match.player_name == "TestPlayer"
        assert match.opponent_name == "Opponent"
        assert match.result == "win"
        assert match.total_turns == 10
        assert match.event_id == "Ladder"


class TestLogParserInitialization:
    """Tests for parser initialization and file handling."""

    def test_parser_file_not_found(self):
        """Test that parser raises error for missing file."""
        with pytest.raises(FileNotFoundError):
            MTGALogParser("/nonexistent/path/Player.log")

    def test_parser_with_valid_file(self, tmp_path):
        """Test parser initializes with valid file."""
        log_file = tmp_path / "Player.log"
        log_file.write_text("test content")

        parser = MTGALogParser(str(log_file))
        assert parser.log_path == log_file
        assert parser.current_match is None
        assert parser.completed_matches == []


class TestLogParserEventExtraction:
    """Tests for extracting events from log lines."""

    def test_parse_match_state_event(self, tmp_path):
        """Test parsing matchGameRoomStateChangedEvent."""
        log_content = """[UnityCrossThreadLogger]1/15/2026 10:30:00 AM
{"matchGameRoomStateChangedEvent":{"gameRoomInfo":{"stateType":"MatchGameRoomStateType_Playing","gameRoomConfig":{"matchId":"match-uuid-123","reservedPlayers":[{"playerName":"TestPlayer","systemSeatId":2,"userId":"user123","eventId":"Ladder"},{"playerName":"Opponent","systemSeatId":1,"userId":"opp123","eventId":"Ladder"}]}}}}
"""
        log_file = tmp_path / "Player.log"
        log_file.write_text(log_content)

        parser = MTGALogParser(str(log_file))
        events = list(parser.parse_events())

        assert len(events) >= 1
        match_events = [e for e in events if e.event_type == "match_state"]
        assert len(match_events) == 1
        assert "matchGameRoomStateChangedEvent" in match_events[0].data

    def test_parse_gre_event(self, tmp_path):
        """Test parsing greToClientEvent."""
        log_content = """{"greToClientEvent":{"greToClientMessages":[{"type":"GREMessageType_GameStateMessage","gameStateMessage":{"gameStateId":1,"turnInfo":{"turnNumber":1,"phase":"Phase_Main1"}}}]}}
"""
        log_file = tmp_path / "Player.log"
        log_file.write_text(log_content)

        parser = MTGALogParser(str(log_file))
        events = list(parser.parse_events())

        gre_events = [e for e in events if e.event_type == "gre_event"]
        assert len(gre_events) == 1

    def test_parse_empty_file(self, tmp_path):
        """Test parsing empty log file raises exception."""
        from src.exceptions import InvalidLogFormatError

        log_file = tmp_path / "Player.log"
        log_file.write_text("")

        with pytest.raises(InvalidLogFormatError):
            MTGALogParser(str(log_file))

    def test_parse_non_json_lines(self, tmp_path):
        """Test that non-JSON lines are skipped gracefully."""
        log_content = """This is not JSON
[UnityCrossThreadLogger]Some log message
Another regular line
{"greToClientEvent":{"test":"data"}}
More text here
"""
        log_file = tmp_path / "Player.log"
        log_file.write_text(log_content)

        parser = MTGALogParser(str(log_file))
        events = list(parser.parse_events())

        # Should only find the one valid JSON event
        assert len(events) == 1


class TestMatchParsing:
    """Tests for full match parsing."""

    def test_parse_complete_match(self, tmp_path):
        """Test parsing a complete match with start and end."""
        log_content = """{"matchGameRoomStateChangedEvent":{"gameRoomInfo":{"stateType":"MatchGameRoomStateType_Playing","gameRoomConfig":{"matchId":"match-123","reservedPlayers":[{"playerName":"Player1","systemSeatId":2,"userId":"u1","eventId":"Ladder"},{"playerName":"Player2","systemSeatId":1,"userId":"u2","eventId":"Ladder"}]}}}}
{"greToClientEvent":{"greToClientMessages":[{"type":"GREMessageType_GameStateMessage","gameStateMessage":{"gameStateId":1,"turnInfo":{"turnNumber":1,"phase":"Phase_Main1"},"gameInfo":{"superFormat":"SuperFormat_Standard","type":"GameType_Duel"}}}]}}
{"matchGameRoomStateChangedEvent":{"gameRoomInfo":{"stateType":"MatchGameRoomStateType_MatchCompleted","gameRoomConfig":{"matchId":"match-123"},"finalMatchResult":{"winningTeamId":2,"resultList":[{"scope":"MatchScope_Match","winningTeamId":2,"reason":"ResultReason_Game"}]}}}}
"""
        log_file = tmp_path / "Player.log"
        log_file.write_text(log_content)

        parser = MTGALogParser(str(log_file))
        matches = parser.parse_matches()

        assert len(matches) == 1
        match = matches[0]
        assert match.match_id == "match-123"
        assert match.player_name == "Player1"
        assert match.opponent_name == "Player2"
        assert match.player_seat_id == 2
        assert match.opponent_seat_id == 1
        assert match.event_id == "Ladder"
        assert match.result == "win"  # Player seat 2 won (winningTeamId == player_seat_id)

    def test_parse_multiple_matches(self, tmp_path):
        """Test parsing multiple matches from one log file."""
        log_content = """{"matchGameRoomStateChangedEvent":{"gameRoomInfo":{"stateType":"MatchGameRoomStateType_Playing","gameRoomConfig":{"matchId":"match-1","reservedPlayers":[{"playerName":"P1","systemSeatId":2},{"playerName":"O1","systemSeatId":1}]}}}}
{"matchGameRoomStateChangedEvent":{"gameRoomInfo":{"stateType":"MatchGameRoomStateType_MatchCompleted","gameRoomConfig":{"matchId":"match-1"}}}}
{"matchGameRoomStateChangedEvent":{"gameRoomInfo":{"stateType":"MatchGameRoomStateType_Playing","gameRoomConfig":{"matchId":"match-2","reservedPlayers":[{"playerName":"P1","systemSeatId":2},{"playerName":"O2","systemSeatId":1}]}}}}
{"matchGameRoomStateChangedEvent":{"gameRoomInfo":{"stateType":"MatchGameRoomStateType_MatchCompleted","gameRoomConfig":{"matchId":"match-2"}}}}
"""
        log_file = tmp_path / "Player.log"
        log_file.write_text(log_content)

        parser = MTGALogParser(str(log_file))
        matches = parser.parse_matches()

        assert len(matches) == 2
        assert matches[0].match_id == "match-1"
        assert matches[1].match_id == "match-2"

    def test_parse_incomplete_match(self, tmp_path):
        """Test parsing match that doesn't have completion event."""
        log_content = """{"matchGameRoomStateChangedEvent":{"gameRoomInfo":{"stateType":"MatchGameRoomStateType_Playing","gameRoomConfig":{"matchId":"incomplete-match","reservedPlayers":[{"playerName":"Player","systemSeatId":2}]}}}}
{"greToClientEvent":{"greToClientMessages":[{"type":"GREMessageType_GameStateMessage","gameStateMessage":{"gameStateId":1,"turnInfo":{"turnNumber":5}}}]}}
"""
        log_file = tmp_path / "Player.log"
        log_file.write_text(log_content)

        parser = MTGALogParser(str(log_file))
        matches = parser.parse_matches()

        # Incomplete match should still be captured
        assert len(matches) == 1
        assert matches[0].match_id == "incomplete-match"
        assert matches[0].result is None  # No result for incomplete match
        assert matches[0].total_turns == 5


class TestGameActionParsing:
    """Tests for parsing game actions."""

    def test_parse_actions(self, tmp_path):
        """Test parsing game actions from GRE messages."""
        log_content = """{"matchGameRoomStateChangedEvent":{"gameRoomInfo":{"stateType":"MatchGameRoomStateType_Playing","gameRoomConfig":{"matchId":"action-test"}}}}
{"greToClientEvent":{"greToClientMessages":[{"type":"GREMessageType_GameStateMessage","gameStateMessage":{"gameStateId":10,"turnInfo":{"turnNumber":3,"phase":"Phase_Main1"},"actions":[{"seatId":2,"action":{"actionType":"ActionType_Cast","instanceId":100,"grpId":12345}}]}}]}}
"""
        log_file = tmp_path / "Player.log"
        log_file.write_text(log_content)

        parser = MTGALogParser(str(log_file))
        matches = parser.parse_matches()

        assert len(matches) == 1
        # Actions should be captured
        assert len(matches[0].actions) >= 1

        cast_actions = [a for a in matches[0].actions if a.get("action_type") == "ActionType_Cast"]
        assert len(cast_actions) >= 1

    def test_parse_turn_info(self, tmp_path):
        """Test that turn numbers are tracked correctly."""
        log_content = """{"matchGameRoomStateChangedEvent":{"gameRoomInfo":{"stateType":"MatchGameRoomStateType_Playing","gameRoomConfig":{"matchId":"turn-test"}}}}
{"greToClientEvent":{"greToClientMessages":[{"type":"GREMessageType_GameStateMessage","gameStateMessage":{"turnInfo":{"turnNumber":1}}}]}}
{"greToClientEvent":{"greToClientMessages":[{"type":"GREMessageType_GameStateMessage","gameStateMessage":{"turnInfo":{"turnNumber":5}}}]}}
{"greToClientEvent":{"greToClientMessages":[{"type":"GREMessageType_GameStateMessage","gameStateMessage":{"turnInfo":{"turnNumber":10}}}]}}
"""
        log_file = tmp_path / "Player.log"
        log_file.write_text(log_content)

        parser = MTGALogParser(str(log_file))
        matches = parser.parse_matches()

        assert matches[0].total_turns == 10


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_malformed_json(self, tmp_path):
        """Test handling of malformed JSON."""
        # The parser should skip lines with invalid JSON and continue
        log_content = """[UnityCrossThreadLogger] Starting MTGA
This is not JSON at all
{"greToClientEvent":{"greToClientMessages":[{"type":"test"}]}}
Another non-JSON line {broken
{"matchGameRoomStateChangedEvent":{"gameRoomInfo":{"stateType":"MatchGameRoomStateType_Playing","gameRoomConfig":{"matchId":"test-123"}}}}
"""
        log_file = tmp_path / "Player.log"
        log_file.write_text(log_content)

        parser = MTGALogParser(str(log_file))
        # Should not raise, should skip non-JSON lines
        events = list(parser.parse_events())

        # Should get the two valid events
        assert len(events) == 2
        event_types = {e.event_type for e in events}
        assert "gre_event" in event_types
        assert "match_state" in event_types

    def test_unicode_characters(self, tmp_path):
        """Test handling of unicode characters in player names."""
        log_content = """{"matchGameRoomStateChangedEvent":{"gameRoomInfo":{"stateType":"MatchGameRoomStateType_Playing","gameRoomConfig":{"matchId":"unicode-test","reservedPlayers":[{"playerName":"Plàyér™","systemSeatId":2},{"playerName":"対戦相手","systemSeatId":1}]}}}}
"""
        log_file = tmp_path / "Player.log"
        log_file.write_text(log_content, encoding="utf-8")

        parser = MTGALogParser(str(log_file))
        matches = parser.parse_matches()

        assert len(matches) == 1
        assert matches[0].player_name == "Plàyér™"
        assert matches[0].opponent_name == "対戦相手"

    def test_missing_fields(self, tmp_path):
        """Test handling of events with missing expected fields."""
        log_content = """{"matchGameRoomStateChangedEvent":{"gameRoomInfo":{"stateType":"MatchGameRoomStateType_Playing","gameRoomConfig":{}}}}
{"matchGameRoomStateChangedEvent":{"gameRoomInfo":{}}}
{"greToClientEvent":{}}
"""
        log_file = tmp_path / "Player.log"
        log_file.write_text(log_content)

        parser = MTGALogParser(str(log_file))
        # Should not raise
        matches = parser.parse_matches()
        # No valid matches should be extracted due to missing matchId
        assert len(matches) == 0

    def test_large_game_state(self, tmp_path):
        """Test handling of large game state with many objects."""
        # Create a large game state with many game objects
        # Start from instanceId 1 since 0 is treated as falsy
        game_objects = [
            {"instanceId": i + 1, "grpId": 10000 + i, "type": "GameObjectType_Card"}
            for i in range(100)
        ]

        log_content = f"""{{"matchGameRoomStateChangedEvent":{{"gameRoomInfo":{{"stateType":"MatchGameRoomStateType_Playing","gameRoomConfig":{{"matchId":"large-test"}}}}}}}}
{{"greToClientEvent":{{"greToClientMessages":[{{"type":"GREMessageType_GameStateMessage","gameStateMessage":{{"gameStateId":1,"gameObjects":{json.dumps(game_objects)}}}}}]}}}}
"""
        log_file = tmp_path / "Player.log"
        log_file.write_text(log_content)

        parser = MTGALogParser(str(log_file))
        matches = parser.parse_matches()

        assert len(matches) == 1
        assert len(matches[0].card_instances) == 100

    def test_duplicate_match_ids(self, tmp_path):
        """Test handling of duplicate match state events for same match."""
        log_content = """{"matchGameRoomStateChangedEvent":{"gameRoomInfo":{"stateType":"MatchGameRoomStateType_Playing","gameRoomConfig":{"matchId":"same-match","reservedPlayers":[{"playerName":"P1","systemSeatId":2}]}}}}
{"matchGameRoomStateChangedEvent":{"gameRoomInfo":{"stateType":"MatchGameRoomStateType_Playing","gameRoomConfig":{"matchId":"same-match","reservedPlayers":[{"playerName":"P1","systemSeatId":2}]}}}}
{"matchGameRoomStateChangedEvent":{"gameRoomInfo":{"stateType":"MatchGameRoomStateType_MatchCompleted","gameRoomConfig":{"matchId":"same-match"}}}}
"""
        log_file = tmp_path / "Player.log"
        log_file.write_text(log_content)

        parser = MTGALogParser(str(log_file))
        matches = parser.parse_matches()

        # Should only have one match despite multiple state events
        assert len(matches) == 1

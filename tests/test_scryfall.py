"""
Tests for the Scryfall bulk data service.

Tests downloading, indexing, and querying card data.
"""

import pytest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.scryfall import ScryfallBulkService


class TestScryfallServiceInitialization:
    """Tests for Scryfall service initialization."""

    def test_service_creates_cache_dir(self, tmp_path):
        """Test that service creates cache directory if missing."""
        cache_dir = tmp_path / "cache"
        assert not cache_dir.exists()

        service = ScryfallBulkService(str(cache_dir))

        assert cache_dir.exists()

    def test_service_default_paths(self, tmp_path):
        """Test service initializes with expected paths."""
        service = ScryfallBulkService(str(tmp_path))

        assert service._bulk_file_path == tmp_path / "scryfall_default_cards.json"
        assert service._index_file_path == tmp_path / "arena_id_index.json"


class TestCardDataSimplification:
    """Tests for card data simplification."""

    def test_simplify_basic_card(self, tmp_path):
        """Test simplifying a basic card."""
        service = ScryfallBulkService(str(tmp_path))

        card_data = {
            "name": "Lightning Bolt",
            "mana_cost": "{R}",
            "cmc": 1,
            "type_line": "Instant",
            "colors": ["R"],
            "color_identity": ["R"],
            "set": "m10",
            "rarity": "common",
            "oracle_text": "Lightning Bolt deals 3 damage to any target.",
            "id": "scryfall-123",
            "arena_id": 67890,
            "image_uris": {"normal": "https://example.com/bolt.jpg"}
        }

        simplified = service._simplify_card_data(card_data)

        assert simplified["name"] == "Lightning Bolt"
        assert simplified["mana_cost"] == "{R}"
        assert simplified["cmc"] == 1
        assert simplified["type_line"] == "Instant"
        assert simplified["colors"] == ["R"]
        assert simplified["rarity"] == "common"
        assert simplified["arena_id"] == 67890

    def test_simplify_double_faced_card(self, tmp_path):
        """Test simplifying a double-faced card."""
        service = ScryfallBulkService(str(tmp_path))

        card_data = {
            "name": "Delver of Secrets // Insectile Aberration",
            "card_faces": [
                {
                    "name": "Delver of Secrets",
                    "mana_cost": "{U}",
                    "type_line": "Creature — Human Wizard",
                    "oracle_text": "At the beginning of your upkeep...",
                    "power": "1",
                    "toughness": "1",
                    "image_uris": {"normal": "https://example.com/delver.jpg"}
                },
                {
                    "name": "Insectile Aberration",
                    "type_line": "Creature — Human Insect",
                    "power": "3",
                    "toughness": "2"
                }
            ],
            "cmc": 1,
            "colors": ["U"],
            "color_identity": ["U"],
            "set": "isd",
            "rarity": "common",
            "id": "scryfall-456",
            "arena_id": 12345
        }

        simplified = service._simplify_card_data(card_data)

        # Should use front face data
        assert simplified["mana_cost"] == "{U}"
        assert simplified["type_line"] == "Creature — Human Wizard"
        assert simplified["power"] == "1"
        assert simplified["toughness"] == "1"

    def test_simplify_card_missing_fields(self, tmp_path):
        """Test simplifying card with missing optional fields."""
        service = ScryfallBulkService(str(tmp_path))

        card_data = {
            "name": "Basic Land",
            "type_line": "Basic Land — Plains",
            "set": "m21"
        }

        simplified = service._simplify_card_data(card_data)

        assert simplified["name"] == "Basic Land"
        assert simplified["mana_cost"] == ""
        assert simplified["colors"] == []
        assert simplified["power"] is None


class TestIndexOperations:
    """Tests for index building and loading."""

    def test_build_index_from_bulk_data(self, tmp_path):
        """Test building index from bulk data file."""
        service = ScryfallBulkService(str(tmp_path))

        # Create mock bulk data
        bulk_data = [
            {"name": "Card 1", "arena_id": 1001, "cmc": 1, "colors": []},
            {"name": "Card 2", "arena_id": 1002, "cmc": 2, "colors": ["U"]},
            {"name": "No Arena ID", "cmc": 3},  # Should be skipped
        ]

        bulk_file = tmp_path / "scryfall_default_cards.json"
        with open(bulk_file, 'w') as f:
            json.dump(bulk_data, f)

        result = service._build_index()

        assert result is True
        assert service._index_loaded is True
        assert len(service._arena_id_index) == 2
        assert 1001 in service._arena_id_index
        assert 1002 in service._arena_id_index

    def test_save_and_load_index(self, tmp_path):
        """Test saving and loading index from disk."""
        service = ScryfallBulkService(str(tmp_path))

        # Manually set index
        service._arena_id_index = {
            1001: {"name": "Test Card 1"},
            1002: {"name": "Test Card 2"},
        }

        # Save
        service._save_index()

        # Create new service and load
        service2 = ScryfallBulkService(str(tmp_path))
        result = service2._load_index()

        assert result is True
        assert service2._index_loaded is True
        assert len(service2._arena_id_index) == 2
        assert service2._arena_id_index[1001]["name"] == "Test Card 1"

    def test_load_index_missing_file(self, tmp_path):
        """Test loading index when file doesn't exist."""
        service = ScryfallBulkService(str(tmp_path))

        result = service._load_index()

        assert result is False
        assert service._index_loaded is False


class TestCardLookup:
    """Tests for card lookup functionality."""

    def test_get_card_by_arena_id(self, tmp_path):
        """Test looking up card by Arena ID."""
        service = ScryfallBulkService(str(tmp_path))
        service._arena_id_index = {
            12345: {"name": "Lightning Bolt", "mana_cost": "{R}"}
        }
        service._index_loaded = True

        card = service.get_card_by_arena_id(12345)

        assert card is not None
        assert card["name"] == "Lightning Bolt"

    def test_get_card_not_found(self, tmp_path):
        """Test lookup for non-existent card."""
        service = ScryfallBulkService(str(tmp_path))
        service._arena_id_index = {}
        service._index_loaded = True

        card = service.get_card_by_arena_id(99999)

        assert card is None

    def test_batch_lookup(self, tmp_path):
        """Test batch lookup of multiple cards."""
        service = ScryfallBulkService(str(tmp_path))
        service._arena_id_index = {
            1001: {"name": "Card 1"},
            1002: {"name": "Card 2"},
            1003: {"name": "Card 3"},
        }
        service._index_loaded = True

        results = service.lookup_cards_batch({1001, 1002, 9999})

        assert len(results) == 3
        assert results[1001]["name"] == "Card 1"
        assert results[1002]["name"] == "Card 2"
        assert results[9999] is None


class TestServiceStats:
    """Tests for service statistics."""

    def test_stats_with_loaded_index(self, tmp_path):
        """Test stats when index is loaded."""
        service = ScryfallBulkService(str(tmp_path))
        service._arena_id_index = {i: {"name": f"Card {i}"} for i in range(100)}
        service._index_loaded = True

        # Create a fake bulk file
        bulk_file = tmp_path / "scryfall_default_cards.json"
        bulk_file.write_text("[]")

        stats = service.stats()

        assert stats["total_cards"] == 100
        assert stats["index_loaded"] is True
        assert stats["bulk_file_exists"] is True


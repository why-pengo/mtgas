"""
Tests for Django models and database operations.

Tests model creation, relationships, and querying.
"""

import sys
from datetime import timedelta
from pathlib import Path

from django.utils import timezone

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def sample_card(db):
    """Create a sample card for testing."""
    from stats.models import Card

    return Card.objects.create(
        grp_id=12345,
        name="Lightning Bolt",
        mana_cost="{R}",
        cmc=1.0,
        type_line="Instant",
        colors=["R"],
        color_identity=["R"],
        set_code="m10",
        rarity="common",
    )


@pytest.fixture
def sample_deck(db, sample_card):
    """Create a sample deck for testing."""
    from stats.models import Deck, DeckCard

    deck = Deck.objects.create(deck_id="deck-uuid-123", name="Red Aggro", format="Standard")
    DeckCard.objects.create(deck=deck, card=sample_card, quantity=4)
    return deck


@pytest.fixture
def sample_match(db, sample_deck):
    """Create a sample match for testing."""
    from stats.models import Match

    return Match.objects.create(
        match_id="match-uuid-456",
        player_name="TestPlayer",
        player_seat_id=2,
        opponent_name="Opponent",
        opponent_seat_id=1,
        deck=sample_deck,
        event_id="Ladder",
        format="Standard",
        result="win",
        winning_team_id=2,
        start_time=timezone.now() - timedelta(minutes=15),
        end_time=timezone.now(),
        duration_seconds=900,
        total_turns=10,
    )


@pytest.mark.django_db
class TestCardModel:
    """Tests for the Card model."""

    def test_card_creation(self):
        """Test basic card creation."""
        from stats.models import Card

        card = Card.objects.create(grp_id=11111, name="Test Card", mana_cost="{1}{U}", cmc=2.0)

        assert card.grp_id == 11111
        assert card.name == "Test Card"
        assert str(card) == "Test Card"

    def test_card_str_unknown(self):
        """Test card string representation with no name."""
        from stats.models import Card

        card = Card.objects.create(grp_id=99999)

        assert "Unknown" in str(card)
        assert "99999" in str(card)

    def test_card_colors_json_field(self):
        """Test that colors field stores JSON correctly."""
        from stats.models import Card

        card = Card.objects.create(grp_id=22222, name="Multicolor", colors=["W", "U", "B"])

        card.refresh_from_db()
        assert card.colors == ["W", "U", "B"]


@pytest.mark.django_db
class TestDeckModel:
    """Tests for the Deck model."""

    def test_deck_creation(self, sample_card):
        """Test deck creation with cards."""
        from stats.models import Deck, DeckCard

        deck = Deck.objects.create(deck_id="new-deck-123", name="Control Deck", format="Historic")

        DeckCard.objects.create(deck=deck, card=sample_card, quantity=4)

        assert deck.name == "Control Deck"
        assert deck.deck_cards.count() == 1
        assert deck.total_cards() == 4

    def test_deck_win_rate_no_matches(self, sample_deck):
        """Test win rate with no matches."""
        assert sample_deck.win_rate() == 0

    def test_deck_win_rate_with_matches(self, sample_deck):
        """Test win rate calculation with matches."""
        from stats.models import Match

        # Create 3 wins and 1 loss
        for i in range(3):
            Match.objects.create(match_id=f"win-{i}", deck=sample_deck, result="win")
        Match.objects.create(match_id="loss-1", deck=sample_deck, result="loss")

        assert sample_deck.win_rate() == 75.0


@pytest.mark.django_db
class TestMatchModel:
    """Tests for the Match model."""

    def test_match_creation(self, sample_match):
        """Test match creation."""
        assert sample_match.match_id == "match-uuid-456"
        assert sample_match.result == "win"
        assert sample_match.total_turns == 10

    def test_match_duration_display(self, sample_match):
        """Test duration display formatting."""
        duration = sample_match.duration_display()
        assert duration == "15m 0s"

    def test_match_duration_display_none(self):
        """Test duration display when no duration."""
        from stats.models import Match

        match = Match.objects.create(match_id="no-duration")
        assert match.duration_display() is None

    def test_match_ordering(self, sample_deck):
        """Test that matches are ordered by start_time descending."""
        from stats.models import Match

        now = timezone.now()
        Match.objects.create(match_id="m1", start_time=now - timedelta(hours=2))
        Match.objects.create(match_id="m2", start_time=now - timedelta(hours=1))
        Match.objects.create(match_id="m3", start_time=now)

        matches = list(Match.objects.values_list("match_id", flat=True))
        assert matches == ["m3", "m2", "m1"]


@pytest.mark.django_db
class TestGameActionModel:
    """Tests for the GameAction model."""

    def test_action_creation(self, sample_match, sample_card):
        """Test game action creation."""
        from stats.models import GameAction

        action = GameAction.objects.create(
            match=sample_match,
            turn_number=3,
            phase="Phase_Main1",
            seat_id=2,
            action_type="ActionType_Cast",
            card=sample_card,
            mana_cost=[{"color": "R", "count": 1}],
        )

        assert action.turn_number == 3
        assert action.action_type == "ActionType_Cast"
        assert action.card.name == "Lightning Bolt"


@pytest.mark.django_db
class TestLifeChangeModel:
    """Tests for the LifeChange model."""

    def test_life_change_creation(self, sample_match):
        """Test life change recording."""
        from stats.models import LifeChange

        LifeChange.objects.create(match=sample_match, turn_number=1, seat_id=2, life_total=20)
        LifeChange.objects.create(
            match=sample_match, turn_number=2, seat_id=2, life_total=17, change_amount=-3
        )

        changes = sample_match.life_changes.all()
        assert changes.count() == 2


@pytest.mark.django_db
class TestImportSessionModel:
    """Tests for the ImportSession model."""

    def test_import_session_creation(self):
        """Test import session tracking."""
        from stats.models import ImportSession

        session = ImportSession.objects.create(
            log_file="/path/to/Player.log",
            file_size=1024000,
            status="completed",
            matches_imported=5,
            matches_skipped=2,
        )

        assert session.matches_imported == 5
        assert session.status == "completed"


@pytest.mark.django_db
class TestModelRelationships:
    """Tests for model relationships and cascades."""

    def test_deck_deletion_sets_match_deck_null(self, sample_match, sample_deck):
        """Test that deleting deck sets match.deck to null."""
        from stats.models import Match

        match_id = sample_match.id
        sample_deck.delete()

        match = Match.objects.get(id=match_id)
        assert match.deck is None

    def test_match_deletion_cascades_to_actions(self, sample_match, sample_card):
        """Test that deleting match deletes related actions."""
        from stats.models import GameAction

        GameAction.objects.create(
            match=sample_match, action_type="ActionType_Cast", card=sample_card
        )

        assert GameAction.objects.count() == 1

        sample_match.delete()

        assert GameAction.objects.count() == 0


@pytest.mark.django_db
class TestQueryingMatches:
    """Tests for querying match data."""

    def test_filter_by_result(self, sample_deck):
        """Test filtering matches by result."""
        from stats.models import Match

        Match.objects.create(match_id="w1", deck=sample_deck, result="win")
        Match.objects.create(match_id="w2", deck=sample_deck, result="win")
        Match.objects.create(match_id="l1", deck=sample_deck, result="loss")

        wins = Match.objects.filter(result="win")
        losses = Match.objects.filter(result="loss")

        assert wins.count() == 2
        assert losses.count() == 1

    def test_aggregate_stats(self, sample_deck):
        """Test aggregating match statistics."""
        from django.db.models import Avg, Count

        from stats.models import Match

        Match.objects.create(match_id="m1", deck=sample_deck, result="win", total_turns=8)
        Match.objects.create(match_id="m2", deck=sample_deck, result="win", total_turns=12)
        Match.objects.create(match_id="m3", deck=sample_deck, result="loss", total_turns=10)

        stats = Match.objects.aggregate(total=Count("id"), avg_turns=Avg("total_turns"))

        assert stats["total"] == 3
        assert stats["avg_turns"] == 10.0

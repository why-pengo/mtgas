"""
Tests for Django views.

Tests web interface functionality including dashboard, matches, and decks views.
"""

import pytest
from django.test import Client
from django.urls import reverse
from datetime import datetime, timedelta
from django.utils import timezone

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def client():
    """Create a test client."""
    return Client()


@pytest.fixture
def sample_data(db):
    """Create sample data for view tests."""
    from stats.models import Card, Deck, DeckCard, Match

    # Create cards
    card = Card.objects.create(
        grp_id=12345,
        name="Lightning Bolt",
        mana_cost="{R}",
        cmc=1.0,
        type_line="Instant",
        colors=["R"]
    )

    # Create deck
    deck = Deck.objects.create(
        deck_id="test-deck-123",
        name="Red Deck Wins",
        format="Standard"
    )
    DeckCard.objects.create(deck=deck, card=card, quantity=4)

    # Create matches
    now = timezone.now()
    Match.objects.create(
        match_id="match-1",
        player_name="TestPlayer",
        opponent_name="Opponent1",
        deck=deck,
        event_id="Ladder",
        result="win",
        total_turns=8,
        start_time=now - timedelta(hours=1),
        duration_seconds=600
    )
    Match.objects.create(
        match_id="match-2",
        player_name="TestPlayer",
        opponent_name="Opponent2",
        deck=deck,
        event_id="Ladder",
        result="loss",
        total_turns=12,
        start_time=now - timedelta(hours=2),
        duration_seconds=900
    )

    return {'card': card, 'deck': deck}


@pytest.mark.django_db
class TestDashboardView:
    """Tests for the dashboard view."""

    def test_dashboard_empty(self, client):
        """Test dashboard with no data."""
        response = client.get(reverse('stats:dashboard'))

        assert response.status_code == 200
        assert 'overall_stats' in response.context
        assert response.context['overall_stats']['total_matches'] == 0

    def test_dashboard_with_data(self, client, sample_data):
        """Test dashboard with match data."""
        response = client.get(reverse('stats:dashboard'))

        assert response.status_code == 200
        stats = response.context['overall_stats']
        assert stats['total_matches'] == 2
        assert stats['wins'] == 1
        assert stats['losses'] == 1
        assert stats['win_rate'] == 50.0

    def test_dashboard_deck_stats(self, client, sample_data):
        """Test dashboard shows deck statistics."""
        response = client.get(reverse('stats:dashboard'))

        deck_stats = response.context['deck_stats']
        assert len(deck_stats) >= 1
        assert deck_stats[0].name == "Red Deck Wins"


@pytest.mark.django_db
class TestMatchesView:
    """Tests for the matches list view."""

    def test_matches_list_empty(self, client):
        """Test matches list with no data."""
        response = client.get(reverse('stats:matches'))

        assert response.status_code == 200
        assert len(response.context['matches']) == 0

    def test_matches_list_with_data(self, client, sample_data):
        """Test matches list with data."""
        response = client.get(reverse('stats:matches'))

        assert response.status_code == 200
        matches = response.context['matches']
        assert len(matches) == 2

    def test_matches_filter_by_result(self, client, sample_data):
        """Test filtering matches by result."""
        response = client.get(reverse('stats:matches'), {'result': 'win'})

        assert response.status_code == 200
        matches = response.context['matches']
        assert len(matches) == 1
        assert matches[0].result == 'win'

    def test_matches_filter_by_deck(self, client, sample_data):
        """Test filtering matches by deck."""
        response = client.get(reverse('stats:matches'), {'deck': 'Red Deck'})

        assert response.status_code == 200
        matches = response.context['matches']
        assert len(matches) == 2


@pytest.mark.django_db
class TestMatchDetailView:
    """Tests for the match detail view."""

    def test_match_detail(self, client, sample_data):
        """Test match detail view."""
        from stats.models import Match
        match = Match.objects.first()

        response = client.get(reverse('stats:match_detail', args=[match.id]))

        assert response.status_code == 200
        assert response.context['match'].match_id == match.match_id

    def test_match_detail_not_found(self, client):
        """Test match detail with invalid ID."""
        response = client.get(reverse('stats:match_detail', args=[99999]))

        assert response.status_code == 404


@pytest.mark.django_db
class TestDecksView:
    """Tests for the decks list view."""

    def test_decks_list_empty(self, client):
        """Test decks list with no data."""
        response = client.get(reverse('stats:decks'))

        assert response.status_code == 200

    def test_decks_list_with_data(self, client, sample_data):
        """Test decks list with data."""
        response = client.get(reverse('stats:decks'))

        assert response.status_code == 200
        decks = response.context['decks']
        assert len(decks) >= 1


@pytest.mark.django_db
class TestDeckDetailView:
    """Tests for the deck detail view."""

    def test_deck_detail(self, client, sample_data):
        """Test deck detail view."""
        deck = sample_data['deck']

        response = client.get(reverse('stats:deck_detail', args=[deck.id]))

        assert response.status_code == 200
        assert response.context['deck'].name == "Red Deck Wins"
        assert 'cards_by_type' in response.context
        assert 'mana_curve' in response.context

    def test_deck_detail_not_found(self, client):
        """Test deck detail with invalid ID."""
        response = client.get(reverse('stats:deck_detail', args=[99999]))

        assert response.status_code == 404


@pytest.mark.django_db
class TestImportSessionsView:
    """Tests for the import sessions view."""

    def test_import_sessions_empty(self, client):
        """Test import sessions with no data."""
        response = client.get(reverse('stats:import_sessions'))

        assert response.status_code == 200

    def test_import_sessions_with_data(self, client, db):
        """Test import sessions with data."""
        from stats.models import ImportSession

        ImportSession.objects.create(
            log_file="/test/Player.log",
            status="completed",
            matches_imported=5
        )

        response = client.get(reverse('stats:import_sessions'))

        assert response.status_code == 200
        sessions = response.context['sessions']
        assert len(sessions) >= 1


@pytest.mark.django_db
class TestAPIEndpoints:
    """Tests for API endpoints."""

    def test_api_stats(self, client, sample_data):
        """Test stats API endpoint."""
        response = client.get(reverse('stats:api_stats'))

        assert response.status_code == 200
        data = response.json()
        assert 'daily' in data


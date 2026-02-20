"""
Automated tests for Unknown Card workflow.

Run with: pytest tests/test_unknown_cards.py -v
"""

from django.utils import timezone

import pytest

from stats.models import Card, Deck, ImportSession, Match, UnknownCard


@pytest.mark.django_db
class TestUnknownCardWorkflow:
    """Test the complete unknown card workflow."""

    @pytest.fixture
    def test_data(self):
        """Create test data for unknown card workflow."""
        # Create import session
        session = ImportSession.objects.create(
            log_file="test_workflow.log",
            file_size=1024,
            status="completed",
            matches_imported=1,
            started_at=timezone.now(),
            completed_at=timezone.now(),
        )

        # Create deck
        deck = Deck.objects.create(
            deck_id="test-unknown-deck", name="Test Unknown Deck", format="Standard"
        )

        # Create match
        match = Match.objects.create(
            match_id="test-unknown-match",
            player_name="TestPlayer",
            opponent_name="TestOpponent",
            deck=deck,
            result="win",
            start_time=timezone.now(),
            format="Standard",
        )

        # Create unknown cards
        card1 = Card.objects.create(grp_id=99999, name="Unknown Card (99999)")
        card2 = Card.objects.create(grp_id=88888, name="Unknown Card (88888)")

        # Create UnknownCard records
        uc1 = UnknownCard.objects.create(
            card=card1,
            import_session=session,
            deck=deck,
            match=match,
            raw_data={"test": True},
            is_resolved=False,
        )

        uc2 = UnknownCard.objects.create(
            card=card2,
            import_session=session,
            deck=None,
            match=match,
            raw_data={"test": True},
            is_resolved=False,
        )

        return {
            "session": session,
            "deck": deck,
            "match": match,
            "card1": card1,
            "card2": card2,
            "uc1": uc1,
            "uc2": uc2,
        }

    def test_unknown_card_model(self, test_data):
        """Test UnknownCard model creation and relationships."""
        uc = test_data["uc1"]
        assert uc.card.grp_id == 99999
        assert uc.deck.name == "Test Unknown Deck"
        assert uc.is_resolved is False
        assert uc.resolved_at is None
        assert str(uc) == "Unknown: Unknown Card (99999) (grp_id: 99999)"

    def test_unknown_cards_list_view(self, client, test_data):
        """Test unknown cards list view."""
        response = client.get("/unknown-cards/")
        assert response.status_code == 200
        assert "Unknown Cards" in response.content.decode()
        assert "99999" in response.content.decode()
        assert "88888" in response.content.decode()

    def test_unknown_cards_list_filter_by_deck(self, client, test_data):
        """Test filtering unknown cards by deck."""
        deck = test_data["deck"]
        response = client.get(f"/unknown-cards/?deck_id={deck.id}")
        assert response.status_code == 200
        # Card 1 is in deck, Card 2 is not
        assert "99999" in response.content.decode()

    def test_unknown_card_fix_view_get(self, client, test_data):
        """Test unknown card fix form (GET request)."""
        card = test_data["card1"]
        response = client.get(f"/unknown-card/{card.grp_id}/fix/")
        assert response.status_code == 200
        assert "Fix Unknown Card" in response.content.decode()
        assert str(card.grp_id) in response.content.decode()
        assert card.name in response.content.decode()

    def test_unknown_card_fix_view_post(self, client, test_data):
        """Test unknown card fix form submission (POST request)."""
        card = test_data["card1"]

        # Submit fix with new name
        response = client.post(
            f"/unknown-card/{card.grp_id}/fix/", {"card_name": "Lightning Bolt"}, follow=True
        )

        assert response.status_code == 200

        # Verify card name was updated
        card.refresh_from_db()
        assert card.name == "Lightning Bolt"

        # Verify UnknownCard record was marked as resolved
        uc = UnknownCard.objects.get(card=card)
        assert uc.is_resolved is True
        assert uc.resolved_at is not None

    def test_deck_detail_shows_unknown_count(self, client, test_data):
        """Test that deck detail view shows unknown card count."""
        deck = test_data["deck"]
        response = client.get(f"/deck/{deck.id}/")
        assert response.status_code == 200

        content = response.content.decode()
        # Should show warning for unknown cards
        assert "Fix" in content
        assert "Unknown" in content

    def test_unknown_cards_show_resolved_filter(self, client, test_data):
        """Test show resolved filter in unknown cards list."""
        # Mark one card as resolved
        card = test_data["card1"]
        UnknownCard.objects.filter(card=card).update(is_resolved=True, resolved_at=timezone.now())

        # Default view (unresolved only)
        response = client.get("/unknown-cards/")
        content = response.content.decode()
        assert "88888" in content  # Unresolved card shown

        # Show all view
        response = client.get("/unknown-cards/?show_resolved=true")
        content = response.content.decode()
        assert "99999" in content  # Resolved card shown
        assert "88888" in content  # Unresolved card also shown

    def test_unknown_card_admin_registered(self):
        """Test that UnknownCard is registered in admin."""
        from django.contrib import admin

        from stats.models import UnknownCard

        assert UnknownCard in admin.site._registry

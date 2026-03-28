"""
Tests for the cards Django app.

Tests the PaperCard model, card_index view, and add_paper_card view.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import Client
from django.urls import reverse

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scryfall_response(name="Test Card", scryfall_id="aaaa-1111"):
    """Build a minimal Scryfall card API response dict."""
    return {
        "id": scryfall_id,
        "name": name,
        "type_line": "Creature — Elf",
        "oracle_text": "Whenever this enters, draw a card.",
        "mana_cost": "{1}{G}",
        "colors": ["G"],
        "set": "tst",
        "rarity": "rare",
        "image_uris": {"normal": f"https://cards.scryfall.io/{scryfall_id}.jpg"},
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def paper_card(db):
    """A saved PaperCard for use in tests."""
    from cards.models import PaperCard

    return PaperCard.objects.create(
        scryfall_id="test-scryfall-0001",
        name="Wickerbough Elder",
        type_line="Creature — Treefolk Shaman",
        oracle_text="Remove a -1/-1 counter: destroy target artifact or enchantment.",
        mana_cost="{3}{G}",
        colors=["G"],
        set_code="shm",
        rarity="common",
        image_uri="https://cards.scryfall.io/normal/wickerbough-elder.jpg",
    )


# ---------------------------------------------------------------------------
# TestPaperCardModel
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPaperCardModel:
    """Tests for the PaperCard model."""

    def test_str_returns_name(self, paper_card):
        assert str(paper_card) == "Wickerbough Elder"

    def test_upsert_creates_new_card(self, db):
        """upsert_from_scryfall creates a PaperCard when none exists."""
        from cards.models import PaperCard

        data = _scryfall_response("Lightning Bolt", "bolt-0001")
        card = PaperCard.upsert_from_scryfall(data)

        assert card.name == "Lightning Bolt"
        assert card.scryfall_id == "bolt-0001"
        assert card.set_code == "tst"
        assert "cards.scryfall.io" in card.image_uri

    def test_upsert_updates_existing_card(self, paper_card):
        """upsert_from_scryfall updates an existing card with the same scryfall_id."""
        from cards.models import PaperCard

        data = _scryfall_response("Wickerbough Elder Updated", paper_card.scryfall_id)
        PaperCard.upsert_from_scryfall(data)

        paper_card.refresh_from_db()
        assert paper_card.name == "Wickerbough Elder Updated"
        assert PaperCard.objects.count() == 1

    def test_upsert_handles_double_faced_card(self, db):
        """upsert_from_scryfall extracts image_uri from card_faces for DFCs."""
        from cards.models import PaperCard

        data = {
            "id": "dfc-0001",
            "name": "Delver of Secrets // Insectile Aberration",
            "type_line": "Creature — Human Wizard // Creature — Human Insect",
            "oracle_text": "",
            "mana_cost": "{U}",
            "colors": ["U"],
            "set": "isd",
            "rarity": "common",
            "card_faces": [{"image_uris": {"normal": "https://cards.scryfall.io/dfc-front.jpg"}}],
        }
        card = PaperCard.upsert_from_scryfall(data)
        assert card.image_uri == "https://cards.scryfall.io/dfc-front.jpg"

    def test_ordering_is_by_name(self, db):
        """PaperCard default ordering is alphabetical by name."""
        from cards.models import PaperCard

        PaperCard.objects.create(scryfall_id="z-001", name="Zeal")
        PaperCard.objects.create(scryfall_id="a-001", name="Arbor Elf")
        names = list(PaperCard.objects.values_list("name", flat=True))
        assert names == sorted(names)


# ---------------------------------------------------------------------------
# TestCardIndexView
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCardIndexView:
    """Tests for the card_index view at /cards/."""

    def test_get_returns_200(self, client):
        response = client.get(reverse("cards:index"))
        assert response.status_code == 200

    def test_empty_state_message(self, client, db):
        response = client.get(reverse("cards:index"))
        assert b"No paper cards yet" in response.content

    def test_paper_cards_section_shown(self, client, paper_card):
        response = client.get(reverse("cards:index"))
        assert paper_card.name.encode() in response.content

    def test_add_by_name_link_present(self, client):
        response = client.get(reverse("cards:index"))
        assert reverse("cards:add_paper_card").encode() in response.content

    def test_no_upload_link(self, client, db):
        """Upload photo link has been removed from index."""
        response = client.get(reverse("cards:index"))
        assert b"Upload Photo" not in response.content
        assert b"Upload a Photo" not in response.content

    def test_no_phash_index_widget(self, client, db):
        """Phash index widget has been removed from the index page."""
        response = client.get(reverse("cards:index"))
        assert b"phash-progress" not in response.content
        assert b"build_phash_index" not in response.content

    def test_context_contains_paper_cards(self, client, db):
        response = client.get(reverse("cards:index"))
        assert "paper_cards" in response.context


# ---------------------------------------------------------------------------
# TestAddPaperCardView
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAddPaperCardView:
    """Tests for the add_paper_card view at /cards/add/."""

    def test_get_returns_200(self, client):
        response = client.get(reverse("cards:add_paper_card"))
        assert response.status_code == 200

    def test_post_empty_name_shows_error(self, client):
        response = client.post(reverse("cards:add_paper_card"), {"card_name": ""})
        assert response.status_code == 200
        assert b"Please enter a card name" in response.content

    def test_post_with_valid_name_creates_paper_card_and_redirects(self, client, db):
        from cards.models import PaperCard

        scryfall_data = _scryfall_response("Lightning Bolt", "bolt-0001")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = scryfall_data

        with patch("cards.views.requests.get", return_value=mock_resp):
            response = client.post(
                reverse("cards:add_paper_card"), {"card_name": "Lightning Bolt"}, follow=False
            )

        assert response.status_code == 302
        assert PaperCard.objects.filter(name="Lightning Bolt").exists()
        card = PaperCard.objects.get(scryfall_id="bolt-0001")
        assert response["Location"] == reverse("cards:paper_card_detail", kwargs={"pk": card.pk})

    def test_post_with_unknown_name_shows_error(self, client, db):
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with patch("cards.views.requests.get", return_value=mock_resp):
            response = client.post(reverse("cards:add_paper_card"), {"card_name": "Zzz Fake Card"})

        assert response.status_code == 200
        assert b"No card found" in response.content

    def test_post_upserts_existing_paper_card(self, client, paper_card):
        """Posting a name matching an existing PaperCard updates, doesn't duplicate."""
        from cards.models import PaperCard

        scryfall_data = _scryfall_response("Wickerbough Elder Updated", paper_card.scryfall_id)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = scryfall_data

        with patch("cards.views.requests.get", return_value=mock_resp):
            client.post(reverse("cards:add_paper_card"), {"card_name": "Wickerbough Elder"})

        assert PaperCard.objects.count() == 1

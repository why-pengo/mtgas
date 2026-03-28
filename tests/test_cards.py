"""
Tests for the cards Django app.

Tests the PaperCard and CardImage models, upload/detail/add views,
match_card_image Celery task, and name_lookup view.
"""

import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

import pytest
from PIL import Image as PILImage

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_png_bytes(color=(200, 100, 50), size=(32, 32)):
    """Return raw bytes of a small PNG for use in tests."""
    buf = BytesIO()
    PILImage.new("RGB", size, color=color).save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


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


@pytest.fixture
def card_image(db, settings, tmp_path):
    """CardImage stored in tmp_path MEDIA_ROOT (file not required for mocked tasks)."""
    from cards.models import CardImage

    settings.MEDIA_ROOT = str(tmp_path)
    ci = CardImage(status=CardImage.Status.PENDING)
    ci.image = "cards/test/card.png"
    ci.save()
    return ci


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
# TestCardImageModel
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCardImageModel:
    """Tests for the CardImage model."""

    def test_default_status_is_pending(self, tmp_path, settings):
        from cards.models import CardImage

        settings.MEDIA_ROOT = str(tmp_path)
        ci = CardImage()
        ci.image = "cards/test/dummy.png"
        ci.save()
        assert ci.status == CardImage.Status.PENDING

    def test_str_unmatched(self, card_image):
        assert "unmatched" in str(card_image)
        assert str(card_image.pk) in str(card_image)

    def test_str_matched(self, card_image, paper_card):
        card_image.paper_card = paper_card
        card_image.save()
        assert paper_card.name in str(card_image)

    def test_paper_card_is_nullable(self, card_image):
        assert card_image.paper_card is None

    def test_deleting_paper_card_nullifies_fk(self, card_image, paper_card):
        from cards.models import CardImage

        card_image.paper_card = paper_card
        card_image.status = CardImage.Status.MATCHED
        card_image.save()

        paper_card.delete()
        card_image.refresh_from_db()
        assert card_image.paper_card is None

    @pytest.mark.parametrize(
        "status",
        ["pending", "processing", "matched", "unmatched", "failed"],
    )
    def test_all_status_choices_are_valid(self, status, tmp_path, settings):
        from cards.models import CardImage

        settings.MEDIA_ROOT = str(tmp_path)
        ci = CardImage(status=status)
        ci.image = "cards/test/dummy.png"
        ci.save()
        ci.refresh_from_db()
        assert ci.status == status


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

    def test_recent_uploads_section_shown(self, client, card_image, paper_card):
        from cards.models import CardImage

        card_image.status = CardImage.Status.MATCHED
        card_image.paper_card = paper_card
        card_image.save()

        response = client.get(reverse("cards:index"))
        assert b"Matched" in response.content

    def test_add_by_name_link_present(self, client):
        response = client.get(reverse("cards:index"))
        assert reverse("cards:add_paper_card").encode() in response.content

    def test_upload_link_present(self, client):
        response = client.get(reverse("cards:index"))
        assert reverse("cards:upload").encode() in response.content

    def test_no_phash_index_widget(self, client, db):
        """Phash index widget has been removed from the index page."""
        response = client.get(reverse("cards:index"))
        assert b"phash-progress" not in response.content
        assert b"build_phash_index" not in response.content

    def test_shows_at_most_20_upload_entries(self, client, settings, tmp_path):
        from cards.models import CardImage

        settings.MEDIA_ROOT = str(tmp_path)
        for i in range(25):
            ci = CardImage(status=CardImage.Status.PENDING)
            ci.image = f"cards/test/card_{i}.png"
            ci.save()

        response = client.get(reverse("cards:index"))
        assert response.content.count(b"/cards/card/") == 20

    def test_context_contains_paper_cards_and_recent_uploads(self, client, db):
        response = client.get(reverse("cards:index"))
        assert "paper_cards" in response.context
        assert "recent_uploads" in response.context


# ---------------------------------------------------------------------------
# TestUploadCardView
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUploadCardView:
    """Tests for the upload_card view."""

    def test_get_returns_200(self, client):
        response = client.get(reverse("cards:upload"))
        assert response.status_code == 200

    def test_post_without_file_shows_error(self, client):
        response = client.post(reverse("cards:upload"), {})
        assert response.status_code == 200
        assert b"Please select an image" in response.content

    def test_post_with_image_creates_card_image_and_redirects(self, client, settings, tmp_path):
        from cards.models import CardImage

        settings.MEDIA_ROOT = str(tmp_path)
        image_file = SimpleUploadedFile("card.png", _make_png_bytes(), content_type="image/png")
        with patch("cards.views.match_card_image"):
            response = client.post(reverse("cards:upload"), {"image": image_file}, follow=False)

        assert response.status_code == 302
        assert CardImage.objects.count() == 1
        card = CardImage.objects.first()
        assert response["Location"] == reverse("cards:card_detail", kwargs={"pk": card.pk})

    def test_post_dispatches_celery_task(self, client, settings, tmp_path):
        settings.MEDIA_ROOT = str(tmp_path)
        image_file = SimpleUploadedFile("card.png", _make_png_bytes(), content_type="image/png")
        with patch("cards.views.transaction.on_commit", side_effect=lambda fn: fn()):
            with patch("cards.views.match_card_image") as mock_task:
                client.post(reverse("cards:upload"), {"image": image_file})
                mock_task.delay.assert_called_once()
                called_pk = mock_task.delay.call_args[0][0]

        from cards.models import CardImage

        assert CardImage.objects.filter(pk=called_pk).exists()

    def test_post_with_non_image_shows_error(self, client, settings, tmp_path):
        from cards.models import CardImage

        settings.MEDIA_ROOT = str(tmp_path)
        bad_file = SimpleUploadedFile("evil.png", b"not image bytes", content_type="image/png")
        response = client.post(reverse("cards:upload"), {"image": bad_file})

        assert response.status_code == 200
        assert b"valid image" in response.content
        assert CardImage.objects.count() == 0

    def test_post_with_oversized_image_shows_error(self, client, settings, tmp_path):
        from cards.models import CardImage

        settings.MEDIA_ROOT = str(tmp_path)
        image_bytes = _make_png_bytes()
        image_file = SimpleUploadedFile("card.png", image_bytes, content_type="image/png")
        with patch("cards.forms.MAX_UPLOAD_BYTES", len(image_bytes) - 1):
            response = client.post(reverse("cards:upload"), {"image": image_file})

        assert response.status_code == 200
        assert b"too large" in response.content
        assert CardImage.objects.count() == 0


# ---------------------------------------------------------------------------
# TestCardDetailView
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCardDetailView:
    """Tests for the card_detail view."""

    def test_pending_card_shows_refresh_meta(self, client, card_image):
        response = client.get(reverse("cards:card_detail", kwargs={"pk": card_image.pk}))
        assert response.status_code == 200
        assert b"refresh" in response.content

    def test_matched_card_shows_card_name(self, client, card_image, paper_card):
        from cards.models import CardImage

        card_image.paper_card = paper_card
        card_image.ocr_text = "Wickerbough Elder"
        card_image.status = CardImage.Status.MATCHED
        card_image.save()

        response = client.get(reverse("cards:card_detail", kwargs={"pk": card_image.pk}))
        assert response.status_code == 200
        assert paper_card.name.encode() in response.content
        assert b"Wickerbough Elder" in response.content

    def test_matched_card_shows_override_form(self, client, card_image, paper_card):
        """After matching, the name-override form is visible."""
        from cards.models import CardImage

        card_image.paper_card = paper_card
        card_image.status = CardImage.Status.MATCHED
        card_image.save()

        response = client.get(reverse("cards:card_detail", kwargs={"pk": card_image.pk}))
        lookup_url = reverse("cards:name_lookup", kwargs={"pk": card_image.pk})
        assert lookup_url.encode() in response.content

    def test_unmatched_card_shows_error_message(self, client, card_image):
        card_image.status = card_image.Status.UNMATCHED
        card_image.error = "No Scryfall match for OCR text."
        card_image.save()

        response = client.get(reverse("cards:card_detail", kwargs={"pk": card_image.pk}))
        assert b"No Scryfall match for OCR text." in response.content

    def test_failed_card_shows_error_message(self, client, card_image):
        card_image.status = card_image.Status.FAILED
        card_image.error = "Image processing error."
        card_image.save()

        response = client.get(reverse("cards:card_detail", kwargs={"pk": card_image.pk}))
        assert b"Image processing error." in response.content

    def test_nonexistent_pk_returns_404(self, client, db):
        response = client.get(reverse("cards:card_detail", kwargs={"pk": 99999}))
        assert response.status_code == 404


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


# ---------------------------------------------------------------------------
# TestNameLookupView
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestNameLookupView:
    """Tests for the name_lookup view at /cards/card/<pk>/lookup/."""

    def test_get_not_allowed(self, client, card_image):
        response = client.get(reverse("cards:name_lookup", kwargs={"pk": card_image.pk}))
        assert response.status_code == 405

    def test_post_empty_name_redirects_without_change(self, client, card_image):
        response = client.post(
            reverse("cards:name_lookup", kwargs={"pk": card_image.pk}),
            {"card_name": ""},
            follow=False,
        )
        assert response.status_code == 302
        card_image.refresh_from_db()
        assert card_image.paper_card is None

    def test_post_successful_match_updates_card_image(self, client, card_image, db):
        from cards.models import CardImage, PaperCard

        scryfall_data = _scryfall_response("Counterspell", "counter-0001")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = scryfall_data

        with patch("cards.views.requests.get", return_value=mock_resp):
            response = client.post(
                reverse("cards:name_lookup", kwargs={"pk": card_image.pk}),
                {"card_name": "Counterspell"},
                follow=False,
            )

        assert response.status_code == 302
        card_image.refresh_from_db()
        assert card_image.status == CardImage.Status.MATCHED
        assert card_image.ocr_text == "Counterspell"
        assert PaperCard.objects.filter(name="Counterspell").exists()

    def test_post_no_scryfall_match_sets_unmatched(self, client, card_image):
        from cards.models import CardImage

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with patch("cards.views.requests.get", return_value=mock_resp):
            client.post(
                reverse("cards:name_lookup", kwargs={"pk": card_image.pk}),
                {"card_name": "Zzz Fake Card"},
            )

        card_image.refresh_from_db()
        assert card_image.status == CardImage.Status.UNMATCHED
        assert "Zzz Fake Card" in card_image.error

    def test_nonexistent_pk_returns_404(self, client, db):
        response = client.post(
            reverse("cards:name_lookup", kwargs={"pk": 99999}),
            {"card_name": "Test"},
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# TestMatchCardImageTask
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMatchCardImageTask:
    """Tests for the match_card_image Celery task."""

    def test_nonexistent_card_image_returns_none(self, db):
        from cards.tasks import match_card_image

        result = match_card_image.apply(args=[999999])
        assert result.result is None

    def test_matched_when_ocr_and_scryfall_succeed(self, card_image):
        """OCR extracts a name, Scryfall returns a card — MATCHED + PaperCard created."""
        from cards.models import CardImage, PaperCard
        from cards.tasks import match_card_image

        scryfall_data = _scryfall_response("Wickerbough Elder", "weld-0001")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = scryfall_data

        with (
            patch("cards.tasks.Image.open"),
            patch("cards.tasks.pytesseract.image_to_string", return_value="Wickerbough Elder\n4/4"),
            patch("cards.tasks.requests.get", return_value=mock_resp),
        ):
            match_card_image.apply(args=[card_image.pk])

        card_image.refresh_from_db()
        assert card_image.status == CardImage.Status.MATCHED
        assert card_image.ocr_text == "Wickerbough Elder"
        assert PaperCard.objects.filter(name="Wickerbough Elder").exists()
        assert card_image.paper_card is not None

    def test_unmatched_when_scryfall_returns_404(self, card_image):
        """OCR extracts a name but Scryfall cannot find it — UNMATCHED."""
        from cards.models import CardImage
        from cards.tasks import match_card_image

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with (
            patch("cards.tasks.Image.open"),
            patch("cards.tasks.pytesseract.image_to_string", return_value="Xyzzy Fake\n"),
            patch("cards.tasks.requests.get", return_value=mock_resp),
        ):
            match_card_image.apply(args=[card_image.pk])

        card_image.refresh_from_db()
        assert card_image.status == CardImage.Status.UNMATCHED
        assert "Xyzzy Fake" in card_image.error

    def test_unmatched_when_ocr_returns_no_text(self, card_image):
        """OCR returns empty — Scryfall not called, UNMATCHED."""
        from cards.models import CardImage
        from cards.tasks import match_card_image

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with (
            patch("cards.tasks.Image.open"),
            patch("cards.tasks.pytesseract.image_to_string", return_value=""),
            patch("cards.tasks.requests.get", return_value=mock_resp),
        ):
            match_card_image.apply(args=[card_image.pk])

        card_image.refresh_from_db()
        assert card_image.status == CardImage.Status.UNMATCHED

    def test_failed_status_on_image_open_error(self, card_image):
        """Image.open raises OSError — status=FAILED."""
        from cards.models import CardImage
        from cards.tasks import match_card_image

        exc = OSError("cannot identify image file")
        with (
            patch("cards.tasks.Image.open", side_effect=exc),
            patch.object(match_card_image, "retry", side_effect=exc),
        ):
            try:
                match_card_image.apply(args=[card_image.pk])
            except Exception:
                pass

        card_image.refresh_from_db()
        assert card_image.status == CardImage.Status.FAILED
        assert "cannot identify image file" in card_image.error

    def test_ocr_text_stored_even_on_unmatched(self, card_image):
        """The OCR result is persisted even when Scryfall does not find a match."""
        from cards.tasks import match_card_image

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with (
            patch("cards.tasks.Image.open"),
            patch("cards.tasks.pytesseract.image_to_string", return_value="Partial Name\n"),
            patch("cards.tasks.requests.get", return_value=mock_resp),
        ):
            match_card_image.apply(args=[card_image.pk])

        card_image.refresh_from_db()
        assert card_image.ocr_text == "Partial Name"

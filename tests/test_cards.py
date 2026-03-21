"""
Tests for the cards Django app.

Tests the CardImage model, upload/detail views, match_card_image Celery task,
and build_phash_index management command.
"""

import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

import imagehash
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


def _make_jpeg_bytes(color=(200, 100, 50), size=(32, 32)):
    """Return raw bytes of a small JPEG."""
    buf = BytesIO()
    PILImage.new("RGB", size, color=color).save(buf, format="JPEG")
    buf.seek(0)
    return buf.read()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Create a test client."""
    return Client()


@pytest.fixture
def scryfall_card(db):
    """stats.Card with a known phash computed from a small red PIL image."""
    from stats.models import Card

    img = PILImage.new("RGB", (32, 32), color=(200, 100, 50))
    known_phash = str(imagehash.phash(img))
    return Card.objects.create(
        grp_id=99001,
        name="Phash Test Card",
        mana_cost="{1}{R}",
        type_line="Instant",
        image_uri="https://example.com/card.jpg",
        phash=known_phash,
    )


@pytest.fixture
def card_image(db, settings, tmp_path):
    """CardImage stored in tmp_path MEDIA_ROOT (file not required on disk for mocked tasks)."""
    from cards.models import CardImage

    settings.MEDIA_ROOT = str(tmp_path)
    ci = CardImage(status=CardImage.Status.PENDING)
    ci.image = "cards/test/card.png"  # fake relative path, no real file needed for mocked tests
    ci.save()
    return ci


# ---------------------------------------------------------------------------
# TestCardImageModel
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCardImageModel:
    """Tests for the CardImage model."""

    def test_default_status_is_pending(self, tmp_path, settings):
        """New CardImage has PENDING status by default."""
        from cards.models import CardImage

        settings.MEDIA_ROOT = str(tmp_path)
        ci = CardImage()
        ci.image = "cards/test/dummy.png"
        ci.save()
        assert ci.status == CardImage.Status.PENDING

    def test_str_unmatched(self, card_image):
        """__str__ contains 'unmatched' and pk when no scryfall_card."""
        result = str(card_image)
        assert "unmatched" in result
        assert str(card_image.pk) in result

    def test_str_matched(self, card_image, scryfall_card):
        """__str__ contains the card name when scryfall_card is set."""
        card_image.scryfall_card = scryfall_card
        card_image.save()
        assert scryfall_card.name in str(card_image)

    def test_scryfall_card_is_nullable(self, card_image):
        """scryfall_card defaults to None."""
        assert card_image.scryfall_card is None

    def test_deleting_scryfall_card_nullifies_fk(self, card_image, scryfall_card):
        """CASCADE SET_NULL: deleting scryfall_card sets FK to NULL."""
        from cards.models import CardImage

        card_image.scryfall_card = scryfall_card
        card_image.status = CardImage.Status.MATCHED
        card_image.save()

        scryfall_card.delete()
        card_image.refresh_from_db()
        assert card_image.scryfall_card is None

    @pytest.mark.parametrize(
        "status",
        ["pending", "processing", "matched", "unmatched", "failed"],
    )
    def test_all_status_choices_are_valid(self, status, tmp_path, settings):
        """All defined status choices can be saved to the database."""
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
        """GET /cards/ returns 200 with no uploads."""
        response = client.get(reverse("cards:index"))
        assert response.status_code == 200

    def test_empty_state_shows_no_uploads_message(self, client):
        """When no CardImages exist, the page says so."""
        response = client.get(reverse("cards:index"))
        assert b"No paper cards uploaded yet" in response.content

    def test_lists_recent_uploads(self, client, card_image, scryfall_card):
        """Existing CardImages appear in the table."""
        from cards.models import CardImage

        card_image.status = CardImage.Status.MATCHED
        card_image.scryfall_card = scryfall_card
        card_image.match_distance = 4
        card_image.save()

        response = client.get(reverse("cards:index"))
        assert response.status_code == 200
        assert scryfall_card.name.encode() in response.content
        assert b"Matched" in response.content

    def test_shows_at_most_20_entries(self, client, settings, tmp_path):
        """Only the 20 most recent uploads are shown."""
        from cards.models import CardImage

        settings.MEDIA_ROOT = str(tmp_path)
        for i in range(25):
            ci = CardImage(status=CardImage.Status.PENDING)
            ci.image = f"cards/test/card_{i}.png"
            ci.save()

        response = client.get(reverse("cards:index"))
        # 20 rows = 20 links to card_detail
        assert response.content.count(b"/cards/card/") == 20

    def test_upload_link_is_present(self, client):
        """The page always contains a link to the upload view."""
        response = client.get(reverse("cards:index"))
        upload_url = reverse("cards:upload").encode()
        assert upload_url in response.content

    def test_phash_context_values_present(self, client, db):
        """Context includes phash_total, phash_indexed, phash_missing, phash_pct."""
        response = client.get(reverse("cards:index"))
        assert "phash_total" in response.context
        assert "phash_indexed" in response.context
        assert "phash_missing" in response.context
        assert "phash_pct" in response.context

    def test_phash_counts_reflect_database(self, client, db):
        """Phash context values accurately count indexed vs. missing cards."""
        from stats.models import Card

        Card.objects.create(grp_id=9001, name="Indexed Card", phash="aabbccdd11223344")
        Card.objects.create(grp_id=9002, name="Not Indexed Card", phash=None)

        response = client.get(reverse("cards:index"))
        # These values are relative to whatever is already in the test DB, so just
        # check that indexed < total when at least one card is missing a phash.
        assert response.context["phash_indexed"] < response.context["phash_total"]
        assert response.context["phash_missing"] >= 1

    def test_phash_progress_bar_shown_when_cards_exist(self, client, db):
        """The progress bar widget appears when the Card table has rows."""
        from stats.models import Card

        Card.objects.create(grp_id=9003, name="Any Card", phash=None)
        response = client.get(reverse("cards:index"))
        assert b"phash-progress" in response.content

    def test_phash_cli_hint_shown_when_cards_missing(self, client, db):
        """A hint to run build_phash_index is shown when phash_missing > 0."""
        from stats.models import Card

        Card.objects.create(grp_id=9004, name="Missing Phash", phash=None)
        response = client.get(reverse("cards:index"))
        assert b"build_phash_index" in response.content

    def test_phash_cli_hint_hidden_when_fully_indexed(self, client, db):
        """No hint shown when every card already has a phash."""
        from stats.models import Card

        # Remove any cards without phash for this test
        Card.objects.filter(phash__isnull=True).delete()
        Card.objects.filter(phash="").delete()
        Card.objects.create(grp_id=9005, name="Fully Indexed", phash="aabb1122ccdd3344")

        response = client.get(reverse("cards:index"))
        assert b"build_phash_index" not in response.content

    def test_phash_widget_hidden_when_no_cards_in_db(self, client, db):
        """Progress bar widget is not shown when the Card table is empty."""
        from stats.models import Card

        Card.objects.all().delete()
        response = client.get(reverse("cards:index"))
        assert b"phash-progress" not in response.content


# ---------------------------------------------------------------------------
# TestUploadCardView
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUploadCardView:
    """Tests for the upload_card view."""

    def test_get_returns_200(self, client):
        """GET /cards/upload/ returns 200."""
        response = client.get(reverse("cards:upload"))
        assert response.status_code == 200

    def test_post_without_file_shows_error(self, client):
        """POST without image shows error in response."""
        response = client.post(reverse("cards:upload"), {})
        assert response.status_code == 200
        assert b"Please select an image" in response.content

    def test_post_with_image_creates_card_image_and_redirects(self, client, settings, tmp_path):
        """POST with image creates CardImage and 302 redirects to card_detail."""
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
        """POST dispatches match_card_image.delay inside a transaction.on_commit callback."""
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
        """POST with a non-image file is rejected before saving to disk."""
        from cards.models import CardImage

        settings.MEDIA_ROOT = str(tmp_path)
        bad_file = SimpleUploadedFile("evil.png", b"not image bytes", content_type="image/png")
        response = client.post(reverse("cards:upload"), {"image": bad_file})

        assert response.status_code == 200
        assert b"valid image" in response.content
        assert CardImage.objects.count() == 0

    def test_post_with_oversized_image_shows_error(self, client, settings, tmp_path):
        """POST with a file exceeding MAX_UPLOAD_BYTES is rejected before saving."""
        from cards.models import CardImage

        settings.MEDIA_ROOT = str(tmp_path)
        image_bytes = _make_png_bytes()
        image_file = SimpleUploadedFile("card.png", image_bytes, content_type="image/png")
        # Patch the limit to be smaller than the test file so rejection fires without
        # needing to allocate a real 10 MB buffer in the test suite.
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
        """Pending status page has meta refresh tag."""
        response = client.get(reverse("cards:card_detail", kwargs={"pk": card_image.pk}))
        assert response.status_code == 200
        assert b"refresh" in response.content

    def test_matched_card_shows_card_name(self, client, card_image, scryfall_card):
        """Matched card shows scryfall_card name and match_distance."""
        card_image.scryfall_card = scryfall_card
        card_image.match_distance = 3
        card_image.status = card_image.Status.MATCHED
        card_image.save()

        response = client.get(reverse("cards:card_detail", kwargs={"pk": card_image.pk}))
        assert response.status_code == 200
        assert scryfall_card.name.encode() in response.content
        assert b"3" in response.content

    def test_unmatched_card_shows_error_message(self, client, card_image):
        """Unmatched status shows card.error in response."""
        card_image.status = card_image.Status.UNMATCHED
        card_image.error = "No matching card found."
        card_image.save()

        response = client.get(reverse("cards:card_detail", kwargs={"pk": card_image.pk}))
        assert response.status_code == 200
        assert b"No matching card found." in response.content

    def test_failed_card_shows_error_message(self, client, card_image):
        """Failed status shows card.error in response."""
        card_image.status = card_image.Status.FAILED
        card_image.error = "Image processing error."
        card_image.save()

        response = client.get(reverse("cards:card_detail", kwargs={"pk": card_image.pk}))
        assert response.status_code == 200
        assert b"Image processing error." in response.content

    def test_nonexistent_pk_returns_404(self, client, db):
        """GET /cards/card/99999/ returns 404."""
        response = client.get(reverse("cards:card_detail", kwargs={"pk": 99999}))
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# TestMatchCardImageTask
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMatchCardImageTask:
    """Tests for the match_card_image Celery task (called via .apply())."""

    def test_nonexistent_card_image_returns_none(self, db):
        """Missing pk returns without raising."""
        from cards.tasks import match_card_image

        result = match_card_image.apply(args=[999999])
        assert result.result is None

    def test_matched_when_phash_distance_is_zero(self, card_image, scryfall_card):
        """Perfect hash match sets MATCHED + correct FK + distance=0."""
        from cards.models import CardImage
        from cards.tasks import match_card_image

        known_hash = imagehash.hex_to_hash(scryfall_card.phash)
        mock_img = MagicMock()

        with (
            patch("cards.tasks.Image") as mock_pil,
            patch("cards.tasks.imagehash.phash", return_value=known_hash),
        ):
            mock_pil.open.return_value.convert.return_value = mock_img
            match_card_image.apply(args=[card_image.pk])

        card_image.refresh_from_db()
        assert card_image.status == CardImage.Status.MATCHED
        assert card_image.scryfall_card_id == scryfall_card.pk
        assert card_image.match_distance == 0

    def test_unmatched_when_no_cards_have_phash(self, card_image):
        """No phashes in DB → UNMATCHED (no Card rows with phash exist)."""
        from cards.models import CardImage
        from cards.tasks import match_card_image

        some_hash = imagehash.hex_to_hash("0000000000000000")
        mock_img = MagicMock()

        with (
            patch("cards.tasks.Image") as mock_pil,
            patch("cards.tasks.imagehash.phash", return_value=some_hash),
        ):
            mock_pil.open.return_value.convert.return_value = mock_img
            match_card_image.apply(args=[card_image.pk])

        card_image.refresh_from_db()
        assert card_image.status == CardImage.Status.UNMATCHED

    def test_unmatched_when_best_distance_exceeds_threshold(self, card_image):
        """Best distance > MATCH_THRESHOLD → UNMATCHED with threshold in error."""
        from cards.models import CardImage
        from cards.tasks import MATCH_THRESHOLD, match_card_image
        from stats.models import Card

        # db_hash = all zeros; upload_hash = all ones → Hamming distance = 64, far above threshold 12
        Card.objects.create(grp_id=99099, name="Far Card", phash="0000000000000000")
        upload_hash = imagehash.hex_to_hash("ffffffffffffffff")
        mock_img = MagicMock()

        with (
            patch("cards.tasks.Image") as mock_pil,
            patch("cards.tasks.imagehash.phash", return_value=upload_hash),
        ):
            mock_pil.open.return_value.convert.return_value = mock_img
            match_card_image.apply(args=[card_image.pk])

        card_image.refresh_from_db()
        assert card_image.status == CardImage.Status.UNMATCHED
        assert str(MATCH_THRESHOLD) in card_image.error

    def test_failed_status_set_on_image_open_error(self, card_image):
        """Image.open raises OSError → status=FAILED, error contains message."""
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


# ---------------------------------------------------------------------------
# TestBuildPhashIndex
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBuildPhashIndex:
    """Tests for the build_phash_index management command."""

    def _make_mock_response(self, color=(200, 100, 50)):
        """Build a mock HTTP response carrying a small JPEG image."""
        mock_resp = MagicMock()
        mock_resp.content = _make_jpeg_bytes(color=color)
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    def test_skips_cards_without_image_uri(self, db):
        """Cards with image_uri=None are not fetched."""
        from stats.models import Card

        Card.objects.create(grp_id=88001, name="No URI Card", image_uri=None)

        with patch("cards.management.commands.build_phash_index.requests.get") as mock_get:
            call_command("build_phash_index")

        mock_get.assert_not_called()

    def test_computes_and_saves_phash_for_card_with_image_uri(self, db):
        """Successful HTTP fetch → phash saved on card."""
        from stats.models import Card

        card = Card.objects.create(
            grp_id=88002, name="Image Card", image_uri="https://example.com/img.jpg"
        )

        with patch(
            "cards.management.commands.build_phash_index.requests.get",
            return_value=self._make_mock_response(),
        ):
            call_command("build_phash_index")

        card.refresh_from_db()
        assert card.phash is not None
        assert len(card.phash) > 0

    def test_limit_option_restricts_number_processed(self, db):
        """--limit 2 with 5 cards → requests.get called at most twice."""
        from stats.models import Card

        for i in range(5):
            Card.objects.create(
                grp_id=88010 + i,
                name=f"Card {i}",
                image_uri=f"https://example.com/card{i}.jpg",
            )

        with patch(
            "cards.management.commands.build_phash_index.requests.get",
            return_value=self._make_mock_response(),
        ) as mock_get:
            call_command("build_phash_index", "--limit", "2")

        assert mock_get.call_count <= 2

    def test_overwrite_reprocesses_existing_phash(self, db):
        """--overwrite on card with existing phash recomputes it."""
        from stats.models import Card

        card = Card.objects.create(
            grp_id=88020,
            name="Already Hashed",
            image_uri="https://example.com/img.jpg",
            phash="0000000000000000",
        )

        with patch(
            "cards.management.commands.build_phash_index.requests.get",
            return_value=self._make_mock_response(color=(100, 200, 50)),
        ) as mock_get:
            call_command("build_phash_index", "--overwrite")

        mock_get.assert_called_once()
        card.refresh_from_db()
        assert card.phash is not None

    def test_http_error_increments_failed_count_and_continues(self, db):
        """requests.get raises → card phash stays None, command does not crash."""
        from stats.models import Card

        card = Card.objects.create(
            grp_id=88030,
            name="Error Card",
            image_uri="https://example.com/broken.jpg",
        )

        with patch(
            "cards.management.commands.build_phash_index.requests.get",
            side_effect=Exception("Connection refused"),
        ):
            call_command("build_phash_index")  # must not raise

        card.refresh_from_db()
        assert card.phash is None

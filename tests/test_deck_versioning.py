"""
Tests for deck versioning: DeckSnapshot model, sideboard capture, diff utility.
"""

from __future__ import annotations

from django.utils import timezone

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def card_bolt(db):
    from stats.models import Card

    return Card.objects.create(grp_id=1001, name="Lightning Bolt", type_line="Instant", cmc=1.0)


@pytest.fixture
def card_path(db):
    from stats.models import Card

    return Card.objects.create(grp_id=1002, name="Path to Exile", type_line="Instant", cmc=1.0)


@pytest.fixture
def card_plains(db):
    from stats.models import Card

    return Card.objects.create(grp_id=1003, name="Plains", type_line="Basic Land", cmc=0.0)


@pytest.fixture
def deck(db):
    from stats.models import Deck

    return Deck.objects.create(deck_id="deck-v1", name="Burn", format="Standard")


@pytest.fixture
def match_one(db, deck):
    from stats.models import Match

    return Match.objects.create(
        match_id="match-001",
        player_name="Player",
        opponent_name="Opp",
        deck=deck,
        event_id="Ladder",
        result="win",
        start_time=timezone.now(),
    )


@pytest.fixture
def match_two(db, deck):
    from stats.models import Match

    return Match.objects.create(
        match_id="match-002",
        player_name="Player",
        opponent_name="Opp",
        deck=deck,
        event_id="Ladder",
        result="loss",
        start_time=timezone.now(),
    )


# ---------------------------------------------------------------------------
# DeckSnapshot model tests
# ---------------------------------------------------------------------------


class TestDeckSnapshotModel:
    def test_snapshot_created_for_match(self, deck, match_one, card_bolt):
        from stats.models import DeckCard, DeckSnapshot

        snap = DeckSnapshot.objects.create(deck=deck, match=match_one)
        DeckCard.objects.create(snapshot=snap, card=card_bolt, quantity=4, is_sideboard=False)

        assert snap.deck == deck
        assert snap.match == match_one
        assert snap.cards.count() == 1

    def test_snapshot_accessible_from_match(self, deck, match_one):
        from stats.models import DeckSnapshot

        snap = DeckSnapshot.objects.create(deck=deck, match=match_one)
        assert match_one.deck_snapshot == snap

    def test_deck_latest_snapshot_returns_most_recent(self, deck, match_one, match_two):
        from stats.models import DeckSnapshot

        snap1 = DeckSnapshot.objects.create(deck=deck, match=match_one)
        snap2 = DeckSnapshot.objects.create(deck=deck, match=match_two)

        latest = deck.latest_snapshot()
        # latest_snapshot returns the most recently created (ordering is -created_at)
        assert latest.pk == snap2.pk
        assert latest.pk != snap1.pk

    def test_deck_total_cards_uses_latest_snapshot(self, deck, match_one, card_bolt, card_path):
        from stats.models import DeckCard, DeckSnapshot

        snap = DeckSnapshot.objects.create(deck=deck, match=match_one)
        DeckCard.objects.create(snapshot=snap, card=card_bolt, quantity=4, is_sideboard=False)
        DeckCard.objects.create(snapshot=snap, card=card_path, quantity=4, is_sideboard=False)

        assert deck.total_cards() == 8

    def test_deck_total_cards_excludes_sideboard(self, deck, match_one, card_bolt, card_plains):
        from stats.models import DeckCard, DeckSnapshot

        snap = DeckSnapshot.objects.create(deck=deck, match=match_one)
        DeckCard.objects.create(snapshot=snap, card=card_bolt, quantity=4, is_sideboard=False)
        DeckCard.objects.create(snapshot=snap, card=card_plains, quantity=1, is_sideboard=True)

        # total_cards() should count only mainboard
        assert deck.total_cards() == 4

    def test_snapshot_without_match_is_valid(self, deck, card_bolt):
        """DeckSnapshot.match is nullable — useful for test fixtures."""
        from stats.models import DeckCard, DeckSnapshot

        snap = DeckSnapshot.objects.create(deck=deck)
        DeckCard.objects.create(snapshot=snap, card=card_bolt, quantity=2)
        assert snap.match is None
        assert snap.cards.count() == 1

    def test_multiple_snapshots_per_deck(self, deck, match_one, match_two, card_bolt, card_path):
        from stats.models import DeckCard, DeckSnapshot

        snap1 = DeckSnapshot.objects.create(deck=deck, match=match_one)
        DeckCard.objects.create(snapshot=snap1, card=card_bolt, quantity=4)

        snap2 = DeckSnapshot.objects.create(deck=deck, match=match_two)
        DeckCard.objects.create(snapshot=snap2, card=card_bolt, quantity=4)
        DeckCard.objects.create(snapshot=snap2, card=card_path, quantity=4)

        assert deck.snapshots.count() == 2
        assert snap1.cards.count() == 1
        assert snap2.cards.count() == 2


# ---------------------------------------------------------------------------
# Sideboard capture tests
# ---------------------------------------------------------------------------


class TestSideboardCapture:
    def test_sideboard_cards_stored_with_flag(self, deck, match_one, card_bolt, card_plains):
        from stats.models import DeckCard, DeckSnapshot

        snap = DeckSnapshot.objects.create(deck=deck, match=match_one)
        DeckCard.objects.create(snapshot=snap, card=card_bolt, quantity=4, is_sideboard=False)
        DeckCard.objects.create(snapshot=snap, card=card_plains, quantity=2, is_sideboard=True)

        mainboard = snap.cards.filter(is_sideboard=False)
        sideboard = snap.cards.filter(is_sideboard=True)

        assert mainboard.count() == 1
        assert sideboard.count() == 1
        assert sideboard.first().card == card_plains

    def test_sideboard_default_is_false(self, deck, card_bolt):
        from stats.models import DeckCard, DeckSnapshot

        snap = DeckSnapshot.objects.create(deck=deck)
        dc = DeckCard.objects.create(snapshot=snap, card=card_bolt, quantity=4)
        assert dc.is_sideboard is False


# ---------------------------------------------------------------------------
# Diff utility tests
# ---------------------------------------------------------------------------


class TestComputeDeckDiff:
    def _make_snap(self, deck, cards_main, cards_side=None):
        """Helper: create a snapshot with given mainboard/sideboard cards.

        cards_main / cards_side are lists of (Card, quantity) tuples.
        """
        from stats.models import DeckCard, DeckSnapshot

        snap = DeckSnapshot.objects.create(deck=deck)
        for card, qty in cards_main or []:
            DeckCard.objects.create(snapshot=snap, card=card, quantity=qty, is_sideboard=False)
        for card, qty in cards_side or []:
            DeckCard.objects.create(snapshot=snap, card=card, quantity=qty, is_sideboard=True)
        return snap

    def test_diff_first_snapshot_all_added(self, deck, card_bolt):
        from stats.deck_diff import compute_deck_diff

        snap = self._make_snap(deck, [(card_bolt, 4)])
        diff = compute_deck_diff(None, snap)

        assert diff.has_changes
        assert len(diff.mainboard.added) == 1
        assert diff.mainboard.added[0].name == "Lightning Bolt"
        assert diff.mainboard.added[0].quantity_after == 4
        assert diff.mainboard.added[0].quantity_before == 0

    def test_diff_no_changes(self, deck, card_bolt):
        from stats.deck_diff import compute_deck_diff

        snap1 = self._make_snap(deck, [(card_bolt, 4)])
        snap2 = self._make_snap(deck, [(card_bolt, 4)])
        diff = compute_deck_diff(snap1, snap2)

        assert not diff.has_changes
        assert len(diff.mainboard.unchanged) == 1
        assert diff.mainboard.unchanged[0].delta == 0

    def test_diff_card_added(self, deck, card_bolt, card_path):
        from stats.deck_diff import compute_deck_diff

        snap1 = self._make_snap(deck, [(card_bolt, 4)])
        snap2 = self._make_snap(deck, [(card_bolt, 4), (card_path, 2)])
        diff = compute_deck_diff(snap1, snap2)

        assert diff.has_changes
        assert len(diff.mainboard.added) == 1
        assert diff.mainboard.added[0].name == "Path to Exile"

    def test_diff_card_removed(self, deck, card_bolt, card_path):
        from stats.deck_diff import compute_deck_diff

        snap1 = self._make_snap(deck, [(card_bolt, 4), (card_path, 2)])
        snap2 = self._make_snap(deck, [(card_bolt, 4)])
        diff = compute_deck_diff(snap1, snap2)

        assert diff.has_changes
        assert len(diff.mainboard.removed) == 1
        assert diff.mainboard.removed[0].name == "Path to Exile"
        assert diff.mainboard.removed[0].quantity_after == 0

    def test_diff_quantity_changed(self, deck, card_bolt):
        from stats.deck_diff import compute_deck_diff

        snap1 = self._make_snap(deck, [(card_bolt, 2)])
        snap2 = self._make_snap(deck, [(card_bolt, 4)])
        diff = compute_deck_diff(snap1, snap2)

        assert diff.has_changes
        assert len(diff.mainboard.changed) == 1
        assert diff.mainboard.changed[0].delta == 2

    def test_diff_sideboard_tracked_separately(self, deck, card_bolt, card_plains):
        from stats.deck_diff import compute_deck_diff

        snap1 = self._make_snap(deck, [(card_bolt, 4)], cards_side=[(card_plains, 2)])
        snap2 = self._make_snap(deck, [(card_bolt, 4)], cards_side=[])
        diff = compute_deck_diff(snap1, snap2)

        assert not diff.mainboard.has_changes
        assert diff.sideboard.has_changes
        assert len(diff.sideboard.removed) == 1
        assert diff.sideboard.removed[0].name == "Plains"

    def test_card_delta_status_properties(self):
        from stats.deck_diff import CardDelta

        assert CardDelta(grp_id=1, name="A", quantity_before=0, quantity_after=4).status == "added"
        assert (
            CardDelta(grp_id=1, name="A", quantity_before=4, quantity_after=0).status == "removed"
        )
        assert (
            CardDelta(grp_id=1, name="A", quantity_before=2, quantity_after=4).status == "changed"
        )
        assert (
            CardDelta(grp_id=1, name="A", quantity_before=4, quantity_after=4).status == "unchanged"
        )

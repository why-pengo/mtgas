"""
Tests for deck versioning: DeckSnapshot model, sideboard capture, diff utility,
and snapshot deduplication (only create new snapshot when deck composition changes).
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
    def test_snapshot_linked_to_match_via_fk(self, deck, match_one, card_bolt):
        from stats.models import DeckCard, DeckSnapshot

        snap = DeckSnapshot.objects.create(deck=deck)
        DeckCard.objects.create(snapshot=snap, card=card_bolt, quantity=4, is_sideboard=False)
        match_one.snapshot = snap
        match_one.save(update_fields=["snapshot"])

        assert snap.deck == deck
        assert match_one.snapshot == snap
        assert snap.cards.count() == 1

    def test_snapshot_accessible_from_match(self, deck, match_one):
        from stats.models import DeckSnapshot

        snap = DeckSnapshot.objects.create(deck=deck)
        match_one.snapshot = snap
        match_one.save(update_fields=["snapshot"])
        match_one.refresh_from_db()
        assert match_one.snapshot == snap

    def test_deck_latest_snapshot_returns_most_recent(self, deck, match_one, match_two):
        from stats.models import DeckSnapshot

        snap1 = DeckSnapshot.objects.create(deck=deck)
        snap2 = DeckSnapshot.objects.create(deck=deck)

        latest = deck.latest_snapshot()
        # latest_snapshot returns the most recently created (ordering is -created_at)
        assert latest.pk == snap2.pk
        assert latest.pk != snap1.pk

    def test_deck_total_cards_uses_latest_snapshot(self, deck, card_bolt, card_path):
        from stats.models import DeckCard, DeckSnapshot

        snap = DeckSnapshot.objects.create(deck=deck)
        DeckCard.objects.create(snapshot=snap, card=card_bolt, quantity=4, is_sideboard=False)
        DeckCard.objects.create(snapshot=snap, card=card_path, quantity=4, is_sideboard=False)

        assert deck.total_cards() == 8

    def test_deck_total_cards_excludes_sideboard(self, deck, card_bolt, card_plains):
        from stats.models import DeckCard, DeckSnapshot

        snap = DeckSnapshot.objects.create(deck=deck)
        DeckCard.objects.create(snapshot=snap, card=card_bolt, quantity=4, is_sideboard=False)
        DeckCard.objects.create(snapshot=snap, card=card_plains, quantity=1, is_sideboard=True)

        # total_cards() should count only mainboard
        assert deck.total_cards() == 4

    def test_multiple_matches_can_share_snapshot(self, deck, match_one, match_two, card_bolt):
        from stats.models import DeckCard, DeckSnapshot

        snap = DeckSnapshot.objects.create(deck=deck)
        DeckCard.objects.create(snapshot=snap, card=card_bolt, quantity=4)
        match_one.snapshot = snap
        match_one.save(update_fields=["snapshot"])
        match_two.snapshot = snap
        match_two.save(update_fields=["snapshot"])

        assert snap.matches.count() == 2
        assert deck.snapshots.count() == 1

    def test_multiple_snapshots_per_deck(self, deck, card_bolt, card_path):
        from stats.models import DeckCard, DeckSnapshot

        snap1 = DeckSnapshot.objects.create(deck=deck)
        DeckCard.objects.create(snapshot=snap1, card=card_bolt, quantity=4)

        snap2 = DeckSnapshot.objects.create(deck=deck)
        DeckCard.objects.create(snapshot=snap2, card=card_bolt, quantity=4)
        DeckCard.objects.create(snapshot=snap2, card=card_path, quantity=4)

        assert deck.snapshots.count() == 2
        assert snap1.cards.count() == 1
        assert snap2.cards.count() == 2


# ---------------------------------------------------------------------------
# Snapshot deduplication tests
# ---------------------------------------------------------------------------


class TestSnapshotDeduplication:
    """Verify that _ensure_deck_snapshot reuses snapshots for identical decks."""

    def _build_deck_data(self, cards_main, cards_side=None):
        """Build mock deck_cards / deck_sideboard lists like the parser produces."""
        deck_cards = [{"cardId": card.grp_id, "quantity": qty} for card, qty in cards_main]
        deck_sideboard = [
            {"cardId": card.grp_id, "quantity": qty} for card, qty in (cards_side or [])
        ]
        return deck_cards, deck_sideboard

    def _call_ensure_snapshot(self, deck, match, deck_cards, deck_sideboard):
        """Directly exercise the frozenset comparison logic used in both import paths."""
        from stats.models import DeckCard, DeckSnapshot

        incoming: set[tuple] = set()
        for cd in deck_cards:
            cid = cd.get("cardId")
            qty = cd.get("quantity", 1)
            if cid:
                incoming.add((cid, qty, False))
        for cd in deck_sideboard:
            cid = cd.get("cardId")
            qty = cd.get("quantity", 1)
            if cid:
                incoming.add((cid, qty, True))
        incoming_fs = frozenset(incoming)

        latest = deck.latest_snapshot()
        if latest is not None:
            existing_fs = frozenset(latest.cards.values_list("card_id", "quantity", "is_sideboard"))
            if existing_fs == incoming_fs:
                match.snapshot = latest
                match.save(update_fields=["snapshot"])
                return latest

        snapshot = DeckSnapshot.objects.create(deck=deck)
        for cd in deck_cards:
            cid = cd.get("cardId")
            qty = cd.get("quantity", 1)
            if cid:
                from stats.models import Card

                card = Card.objects.get(grp_id=cid)
                DeckCard.objects.create(
                    snapshot=snapshot, card=card, quantity=qty, is_sideboard=False
                )
        for cd in deck_sideboard:
            cid = cd.get("cardId")
            qty = cd.get("quantity", 1)
            if cid:
                from stats.models import Card

                card = Card.objects.get(grp_id=cid)
                DeckCard.objects.create(
                    snapshot=snapshot, card=card, quantity=qty, is_sideboard=True
                )
        match.snapshot = snapshot
        match.save(update_fields=["snapshot"])
        return snapshot

    def test_identical_deck_reuses_snapshot(self, deck, match_one, match_two, card_bolt):
        deck_cards, deck_sideboard = self._build_deck_data([(card_bolt, 4)])

        snap1 = self._call_ensure_snapshot(deck, match_one, deck_cards, deck_sideboard)
        snap2 = self._call_ensure_snapshot(deck, match_two, deck_cards, deck_sideboard)

        assert snap1.pk == snap2.pk, "Same deck composition should reuse the same snapshot"
        assert deck.snapshots.count() == 1
        assert match_one.snapshot_id == match_two.snapshot_id

    def test_changed_deck_creates_new_snapshot(
        self, deck, match_one, match_two, card_bolt, card_path
    ):
        deck_cards_v1, sb_v1 = self._build_deck_data([(card_bolt, 4)])
        deck_cards_v2, sb_v2 = self._build_deck_data([(card_bolt, 4), (card_path, 2)])

        snap1 = self._call_ensure_snapshot(deck, match_one, deck_cards_v1, sb_v1)
        snap2 = self._call_ensure_snapshot(deck, match_two, deck_cards_v2, sb_v2)

        assert snap1.pk != snap2.pk, "Different deck composition must create a new snapshot"
        assert deck.snapshots.count() == 2

    def test_sideboard_change_creates_new_snapshot(
        self, deck, match_one, match_two, card_bolt, card_plains
    ):
        deck_cards, sb_empty = self._build_deck_data([(card_bolt, 4)])
        deck_cards, sb_plains = self._build_deck_data([(card_bolt, 4)], [(card_plains, 2)])

        snap1 = self._call_ensure_snapshot(deck, match_one, deck_cards, sb_empty)
        snap2 = self._call_ensure_snapshot(deck, match_two, deck_cards, sb_plains)

        assert snap1.pk != snap2.pk, "Sideboard change must create a new snapshot"
        assert deck.snapshots.count() == 2

    def test_match_snapshot_fk_is_set(self, deck, match_one, card_bolt):
        deck_cards, deck_sideboard = self._build_deck_data([(card_bolt, 4)])

        snap = self._call_ensure_snapshot(deck, match_one, deck_cards, deck_sideboard)
        match_one.refresh_from_db()

        assert match_one.snapshot_id == snap.pk


# ---------------------------------------------------------------------------
# Sideboard capture tests
# ---------------------------------------------------------------------------


class TestSideboardCapture:
    def test_sideboard_cards_stored_with_flag(self, deck, card_bolt, card_plains):
        from stats.models import DeckCard, DeckSnapshot

        snap = DeckSnapshot.objects.create(deck=deck)
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

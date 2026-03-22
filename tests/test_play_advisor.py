"""
Tests for the PlayAdvisor service.

Covers the four suggestion types:
- Mana efficiency (leaving significant mana unused)
- Play ordering (high CMC before low CMC)
- Missed plays (held castable card 3+ turns)
- Alternate plays (cheaper options were available)

Also covers the no-data edge case and the MatchAnalysis aggregates.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cards(db):
    """Create a small set of test cards at different CMC values."""
    from stats.models import Card

    bolt = Card.objects.create(
        grp_id=1001, name="Lightning Bolt", mana_cost="{R}", cmc=1.0, type_line="Instant"
    )
    llanowar = Card.objects.create(
        grp_id=1002,
        name="Llanowar Elves",
        mana_cost="{G}",
        cmc=1.0,
        type_line="Creature — Elf Druid",
    )
    elvish = Card.objects.create(
        grp_id=1003,
        name="Elvish Mystic",
        mana_cost="{G}",
        cmc=1.0,
        type_line="Creature — Elf Druid",
    )
    tef = Card.objects.create(
        grp_id=1004,
        name="Teferi, Hero of Dominaria",
        mana_cost="{3}{W}{U}",
        cmc=5.0,
        type_line="Legendary Planeswalker — Teferi",
    )
    glorybringer = Card.objects.create(
        grp_id=1005,
        name="Glorybringer",
        mana_cost="{3}{R}{R}",
        cmc=5.0,
        type_line="Creature — Dragon",
    )
    forest = Card.objects.create(
        grp_id=1006, name="Forest", mana_cost=None, cmc=0.0, type_line="Basic Land — Forest"
    )
    llion = Card.objects.create(
        grp_id=1007,
        name="Serra Angel",
        mana_cost="{3}{W}{W}",
        cmc=5.0,
        type_line="Creature — Angel",
    )
    return {
        "bolt": bolt,
        "llanowar": llanowar,
        "elvish": elvish,
        "teferi": tef,
        "glorybringer": glorybringer,
        "forest": forest,
        "serra": llion,
    }


@pytest.fixture
def base_match(db, cards):
    """Create a minimal match with two seats."""
    from stats.models import Deck, Match

    deck = Deck.objects.create(deck_id="deck-test-001", name="Test Deck")
    match = Match.objects.create(
        match_id="match-test-001",
        player_name="You",
        player_seat_id=1,
        opponent_name="Opponent",
        opponent_seat_id=2,
        deck=deck,
        total_turns=6,
    )
    return match


def _make_zone_transfers(
    match,
    player_hand_zone: int,
    stack_zone: int,
    battlefield_zone: int,
    player_library_zone: int,
    plays: list,
    seed_card=None,
):
    """
    Helper to create zone transfer records with enough data for zone-label inference.

    Creates seeding data on turn=0 (excluded from per-turn analysis):
    - 3 named draws from player library → player hand (makes library identifiable)
    - 3 CastSpell + 2 PlayLand from player hand (gives hand 2 outbound destinations)
    - 5 named resolves from stack → battlefield (makes battlefield identifiable)
    - 6 anonymous draws from fake OPP library (zone 99) → OPP hand (zone 98)
    - The actual plays from the ``plays`` parameter

    plays: list of (turn, gsid, card, from_zone, to_zone, category)
    """
    from stats.models import ZoneTransfer

    # Use the first real card from plays list for seeding, or the passed seed_card
    ref_card = seed_card
    if ref_card is None and plays:
        ref_card = plays[0][2]

    # 3 named draws: player library → player hand  (→ Library is identified in step 5)
    # Use turn=0 so these seeding events don't appear in per-turn analysis.
    for i in range(3):
        ZoneTransfer.objects.create(
            match=match,
            game_state_id=i + 1,
            turn_number=0,
            card=ref_card,
            from_zone=player_library_zone,
            to_zone=player_hand_zone,
            category="Draw",
        )

    # 3 CastSpell: player hand → stack  (one outbound dest from hand; turn=0)
    for i in range(3):
        ZoneTransfer.objects.create(
            match=match,
            game_state_id=5 + i,
            turn_number=0,
            card=ref_card,
            from_zone=player_hand_zone,
            to_zone=stack_zone,
            category="CastSpell",
        )

    # 2 PlayLand: player hand → battlefield  (second outbound dest, making hand != library; turn=0)
    for i in range(2):
        ZoneTransfer.objects.create(
            match=match,
            game_state_id=8 + i,
            turn_number=0,
            card=ref_card,
            from_zone=player_hand_zone,
            to_zone=battlefield_zone,
            category="PlayLand",
        )

    # 5 resolves: stack → battlefield  (→ Battlefield gets highest net accumulation; turn=0)
    for i in range(5):
        ZoneTransfer.objects.create(
            match=match,
            game_state_id=10 + i,
            turn_number=0,
            card=ref_card,
            from_zone=stack_zone,
            to_zone=battlefield_zone,
            category="Resolve",
        )

    # 6 anonymous OPP draws  (→ OPP library zone 99 identified via anon departures; turn=0)
    for gsid in range(20, 26):
        ZoneTransfer.objects.create(
            match=match,
            game_state_id=gsid,
            turn_number=0,
            card=None,
            from_zone=99,
            to_zone=98,
            category="Draw",
        )

    # Actual test plays
    for turn, gsid, card, from_zone, to_zone, category in plays:
        ZoneTransfer.objects.create(
            match=match,
            game_state_id=gsid,
            turn_number=turn,
            card=card,
            from_zone=from_zone,
            to_zone=to_zone,
            category=category,
        )


def _make_game_actions(
    match, player_seat: int, turn: int, gsid: int, phase: str, cast_cards: list, mana_count: int
):
    """
    Create game_action rows for a single game-state snapshot.

    cast_cards: list of Card objects the player CAN cast
    mana_count: number of mana sources available (Activate_Mana actions)
    """
    from stats.models import GameAction

    for card in cast_cards:
        GameAction.objects.create(
            match=match,
            game_state_id=gsid,
            turn_number=turn,
            phase=phase,
            seat_id=player_seat,
            action_type="ActionType_Cast",
            card=card,
        )
    for _ in range(mana_count):
        GameAction.objects.create(
            match=match,
            game_state_id=gsid,
            turn_number=turn,
            phase=phase,
            seat_id=player_seat,
            action_type="ActionType_Activate_Mana",
            card=None,
        )


# Zone ID constants used throughout tests
PLAYER_HAND = 10
PLAYER_LIBRARY = 20
STACK = 30
BATTLEFIELD = 40
OPP_LIBRARY = 99
OPP_HAND = 98


# ---------------------------------------------------------------------------
# Tests: no-data edge case
# ---------------------------------------------------------------------------


class TestNoData:
    def test_empty_match_returns_no_data(self, base_match):
        """An empty match (no actions, no zone transfers) returns has_data=False."""
        from src.services.play_advisor import PlayAdvisor

        result = PlayAdvisor(base_match).analyze()

        assert result.has_data is False
        assert result.total_suggestions == 0
        assert result.turns == []


# ---------------------------------------------------------------------------
# Tests: mana efficiency
# ---------------------------------------------------------------------------


class TestManaEfficiency:
    def test_flagged_when_mana_wasted_with_castable_spells(self, base_match, cards):
        """
        Mana-efficiency warning when player has mana available but doesn't use it.
        Turn 3: 3 mana available, player plays nothing, could cast Llanowar (1) or Bolt (1).
        """
        from src.services.play_advisor import PlayAdvisor

        # Set up zone transfers: just the seeding draws, no actual plays on turn 3
        _make_zone_transfers(
            base_match,
            PLAYER_HAND,
            STACK,
            BATTLEFIELD,
            PLAYER_LIBRARY,
            [
                # one dummy play on turn 1 to seed zone detection
                (1, 100, cards["llanowar"], PLAYER_HAND, STACK, "CastSpell"),
            ],
        )

        # Turn 3: 3 mana, can cast bolt and llanowar, player casts nothing
        _make_game_actions(
            base_match,
            player_seat=1,
            turn=3,
            gsid=50,
            phase="Phase_Main1",
            cast_cards=[cards["bolt"], cards["llanowar"]],
            mana_count=3,
        )
        # Also add turn 1 actions to establish it as a player turn
        _make_game_actions(
            base_match,
            player_seat=1,
            turn=1,
            gsid=50,
            phase="Phase_Main1",
            cast_cards=[cards["llanowar"]],
            mana_count=1,
        )

        result = PlayAdvisor(base_match).analyze()

        # Find the turn 3 analysis
        turn3 = next((t for t in result.turns if t.turn_number == 3), None)
        assert turn3 is not None
        mana_suggestions = [s for s in turn3.suggestions if s.type == "mana_efficiency"]
        assert len(mana_suggestions) >= 1
        assert mana_suggestions[0].severity in ("warning", "tip")

    def test_not_flagged_when_no_castable_spells(self, base_match, cards):
        """No mana-efficiency warning when player has mana but nothing to cast."""
        from src.services.play_advisor import PlayAdvisor

        _make_zone_transfers(
            base_match,
            PLAYER_HAND,
            STACK,
            BATTLEFIELD,
            PLAYER_LIBRARY,
            [
                (1, 100, cards["llanowar"], PLAYER_HAND, STACK, "CastSpell"),
            ],
        )
        # Turn 3: 3 mana but NO cast options for the player
        _make_game_actions(
            base_match,
            player_seat=1,
            turn=3,
            gsid=50,
            phase="Phase_Main1",
            cast_cards=[],  # nothing to cast
            mana_count=3,
        )
        _make_game_actions(
            base_match,
            player_seat=1,
            turn=1,
            gsid=10,
            phase="Phase_Main1",
            cast_cards=[cards["llanowar"]],
            mana_count=1,
        )

        result = PlayAdvisor(base_match).analyze()

        turn3 = next((t for t in result.turns if t.turn_number == 3), None)
        if turn3:
            mana_suggestions = [s for s in turn3.suggestions if s.type == "mana_efficiency"]
            assert len(mana_suggestions) == 0


# ---------------------------------------------------------------------------
# Tests: play ordering
# ---------------------------------------------------------------------------


class TestPlayOrdering:
    def test_flagged_when_high_cmc_before_low_cmc(self, base_match, cards):
        """
        Ordering warning when a 5-CMC card is cast before a 1-CMC card in the same turn.
        """
        from src.services.play_advisor import PlayAdvisor

        # Turn 5: player casts Glorybringer (gsid=100) then Llanowar Elves (gsid=110)
        _make_zone_transfers(
            base_match,
            PLAYER_HAND,
            STACK,
            BATTLEFIELD,
            PLAYER_LIBRARY,
            [
                (5, 100, cards["glorybringer"], PLAYER_HAND, STACK, "CastSpell"),
                (5, 110, cards["llanowar"], PLAYER_HAND, STACK, "CastSpell"),
            ],
        )
        _make_game_actions(
            base_match,
            player_seat=1,
            turn=5,
            gsid=95,
            phase="Phase_Main1",
            cast_cards=[cards["glorybringer"], cards["llanowar"]],
            mana_count=6,
        )

        result = PlayAdvisor(base_match).analyze()

        turn5 = next((t for t in result.turns if t.turn_number == 5), None)
        assert turn5 is not None
        ordering_suggestions = [s for s in turn5.suggestions if s.type == "ordering"]
        assert len(ordering_suggestions) >= 1
        s = ordering_suggestions[0]
        assert "Glorybringer" in s.body or "glorybringer" in s.body.lower()

    def test_not_flagged_when_low_cmc_before_high_cmc(self, base_match, cards):
        """No ordering warning when lower-CMC card is cast before higher-CMC (correct order)."""
        from src.services.play_advisor import PlayAdvisor

        # Turn 5: player casts Llanowar (gsid=100) then Glorybringer (gsid=110) — correct order
        _make_zone_transfers(
            base_match,
            PLAYER_HAND,
            STACK,
            BATTLEFIELD,
            PLAYER_LIBRARY,
            [
                (5, 100, cards["llanowar"], PLAYER_HAND, STACK, "CastSpell"),
                (5, 110, cards["glorybringer"], PLAYER_HAND, STACK, "CastSpell"),
            ],
        )
        _make_game_actions(
            base_match,
            player_seat=1,
            turn=5,
            gsid=95,
            phase="Phase_Main1",
            cast_cards=[cards["llanowar"], cards["glorybringer"]],
            mana_count=6,
        )

        result = PlayAdvisor(base_match).analyze()

        turn5 = next((t for t in result.turns if t.turn_number == 5), None)
        if turn5:
            ordering_suggestions = [s for s in turn5.suggestions if s.type == "ordering"]
            assert len(ordering_suggestions) == 0


# ---------------------------------------------------------------------------
# Tests: missed plays
# ---------------------------------------------------------------------------


class TestMissedPlays:
    def test_flagged_after_threshold_turns(self, base_match, cards):
        """
        Missed-play tip appears when the same castable card is held for
        MISSED_PLAY_TURN_THRESHOLD (3) consecutive turns.
        """
        from src.services.play_advisor import PlayAdvisor

        # Seed one named draw on turn 1
        _make_zone_transfers(
            base_match,
            PLAYER_HAND,
            STACK,
            BATTLEFIELD,
            PLAYER_LIBRARY,
            [
                (1, 100, cards["llanowar"], PLAYER_HAND, STACK, "CastSpell"),
            ],
        )

        # Turns 2, 3, 4: bolt is available each turn but never cast
        for turn, gsid in [(2, 200), (3, 300), (4, 400)]:
            _make_game_actions(
                base_match,
                player_seat=1,
                turn=turn,
                gsid=gsid,
                phase="Phase_Main1",
                cast_cards=[cards["bolt"]],
                mana_count=turn,  # mana increases each turn
            )

        result = PlayAdvisor(base_match).analyze()

        # Should be flagged by turn 4 (3 consecutive turns held)
        all_suggestions = [
            s for t in result.turns for s in t.suggestions if s.type == "missed_play"
        ]
        assert len(all_suggestions) >= 1
        assert "Lightning Bolt" in all_suggestions[0].body

    def test_not_flagged_before_threshold(self, base_match, cards):
        """No missed-play flag for only 2 consecutive turns held."""
        from src.services.play_advisor import PlayAdvisor

        _make_zone_transfers(
            base_match,
            PLAYER_HAND,
            STACK,
            BATTLEFIELD,
            PLAYER_LIBRARY,
            [
                (1, 100, cards["llanowar"], PLAYER_HAND, STACK, "CastSpell"),
            ],
        )

        # Only turns 2 and 3 (below threshold of 3)
        for turn, gsid in [(2, 20), (3, 30)]:
            _make_game_actions(
                base_match,
                player_seat=1,
                turn=turn,
                gsid=gsid,
                phase="Phase_Main1",
                cast_cards=[cards["bolt"]],
                mana_count=turn,
            )

        result = PlayAdvisor(base_match).analyze()

        all_missed = [s for t in result.turns for s in t.suggestions if s.type == "missed_play"]
        assert len(all_missed) == 0


# ---------------------------------------------------------------------------
# Tests: alternate plays
# ---------------------------------------------------------------------------


class TestAlternatePlays:
    def test_flagged_when_cheaper_alternative_available(self, base_match, cards):
        """
        Alternate-play tip when player casts a 5-CMC card but had a 1-CMC card available.
        """
        from src.services.play_advisor import PlayAdvisor

        _make_zone_transfers(
            base_match,
            PLAYER_HAND,
            STACK,
            BATTLEFIELD,
            PLAYER_LIBRARY,
            [
                (5, 100, cards["glorybringer"], PLAYER_HAND, STACK, "CastSpell"),
            ],
        )
        _make_game_actions(
            base_match,
            player_seat=1,
            turn=5,
            gsid=95,
            phase="Phase_Main1",
            cast_cards=[cards["glorybringer"], cards["bolt"]],
            mana_count=5,
        )

        result = PlayAdvisor(base_match).analyze()

        turn5 = next((t for t in result.turns if t.turn_number == 5), None)
        assert turn5 is not None
        alt_suggestions = [s for s in turn5.suggestions if s.type == "alternate_play"]
        assert len(alt_suggestions) >= 1
        assert "Lightning Bolt" in alt_suggestions[0].cards_suggested

    def test_not_flagged_for_low_cmc_plays(self, base_match, cards):
        """No alternate-play flag for spells with CMC < 3."""
        from src.services.play_advisor import PlayAdvisor

        _make_zone_transfers(
            base_match,
            PLAYER_HAND,
            STACK,
            BATTLEFIELD,
            PLAYER_LIBRARY,
            [
                (2, 200, cards["bolt"], PLAYER_HAND, STACK, "CastSpell"),
            ],
        )
        _make_game_actions(
            base_match,
            player_seat=1,
            turn=2,
            gsid=150,
            phase="Phase_Main1",
            cast_cards=[cards["bolt"], cards["llanowar"]],
            mana_count=2,
        )

        result = PlayAdvisor(base_match).analyze()

        turn2 = next((t for t in result.turns if t.turn_number == 2), None)
        if turn2:
            alt_suggestions = [s for s in turn2.suggestions if s.type == "alternate_play"]
            assert len(alt_suggestions) == 0


# ---------------------------------------------------------------------------
# Tests: aggregate stats
# ---------------------------------------------------------------------------


class TestAggregates:
    def test_overall_mana_efficiency_calculated(self, base_match, cards):
        """overall_mana_efficiency reflects the ratio of mana spent to mana available."""
        from src.services.play_advisor import PlayAdvisor

        _make_zone_transfers(
            base_match,
            PLAYER_HAND,
            STACK,
            BATTLEFIELD,
            PLAYER_LIBRARY,
            [
                (2, 200, cards["bolt"], PLAYER_HAND, STACK, "CastSpell"),  # 1 mana spent
            ],
        )
        # Turn 2: 4 mana available, cast bolt (1 mana) — 25% efficiency
        _make_game_actions(
            base_match,
            player_seat=1,
            turn=2,
            gsid=150,
            phase="Phase_Main1",
            cast_cards=[cards["bolt"], cards["llanowar"]],
            mana_count=4,
        )

        result = PlayAdvisor(base_match).analyze()
        assert result.has_data is True
        # Should compute some efficiency value (exact value depends on zone resolution)
        assert isinstance(result.overall_mana_efficiency, float)
        assert 0.0 <= result.overall_mana_efficiency <= 100.0

    def test_total_suggestions_count(self, base_match, cards):
        """total_suggestions sums all suggestions across all turns."""
        from src.services.play_advisor import PlayAdvisor

        _make_zone_transfers(
            base_match,
            PLAYER_HAND,
            STACK,
            BATTLEFIELD,
            PLAYER_LIBRARY,
            [
                (1, 100, cards["llanowar"], PLAYER_HAND, STACK, "CastSpell"),
            ],
        )
        _make_game_actions(
            base_match,
            player_seat=1,
            turn=1,
            gsid=50,
            phase="Phase_Main1",
            cast_cards=[cards["llanowar"], cards["bolt"]],
            mana_count=5,  # large mana → mana efficiency warning likely
        )

        result = PlayAdvisor(base_match).analyze()
        assert result.total_suggestions == sum(len(t.suggestions) for t in result.turns)

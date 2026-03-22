"""
Tests for deck analysis helper functions in stats/views/decks.py.

Covers _parse_color_pips and _compute_deck_suggestions with various deck profiles.
"""

from types import SimpleNamespace

import pytest

from stats.views.decks import _compute_deck_suggestions, _parse_color_pips

# ── _parse_color_pips ─────────────────────────────────────────────────────────


class TestParseColorPips:
    def test_single_colored_pip(self):
        result = _parse_color_pips("{R}")
        assert result["R"] == 1.0
        assert result["W"] == 0.0

    def test_generic_mana_ignored(self):
        result = _parse_color_pips("{2}{U}")
        assert result["U"] == 1.0
        assert sum(result.values()) == 1.0

    def test_multiple_pips_same_color(self):
        result = _parse_color_pips("{W}{W}{W}")
        assert result["W"] == 3.0

    def test_multicolor_spell(self):
        result = _parse_color_pips("{1}{W}{U}")
        assert result["W"] == 1.0
        assert result["U"] == 1.0

    def test_hybrid_pip_split_evenly(self):
        result = _parse_color_pips("{W/U}")
        assert result["W"] == pytest.approx(0.5)
        assert result["U"] == pytest.approx(0.5)

    def test_empty_mana_cost(self):
        result = _parse_color_pips("")
        assert all(v == 0.0 for v in result.values())

    def test_colorless_only(self):
        result = _parse_color_pips("{3}")
        assert all(v == 0.0 for v in result.values())

    def test_x_cost_ignored(self):
        result = _parse_color_pips("{X}{R}{R}")
        assert result["R"] == 2.0
        assert sum(result.values()) == 2.0


# ── Helpers for building mock DeckCard objects ────────────────────────────────


def _make_card(
    name="Test Card",
    mana_cost="{1}",
    cmc=1.0,
    type_line="Instant",
    colors=None,
    oracle_text="",
):
    """Return a SimpleNamespace mimicking a Card model instance."""
    return SimpleNamespace(
        name=name,
        mana_cost=mana_cost,
        cmc=cmc,
        type_line=type_line,
        colors=colors or [],
        oracle_text=oracle_text,
    )


def _make_dc(card, quantity=1, is_sideboard=False):
    """Return a SimpleNamespace mimicking a DeckCard model instance."""
    return SimpleNamespace(card=card, quantity=quantity, is_sideboard=is_sideboard)


# ── _compute_deck_suggestions ─────────────────────────────────────────────────


class TestComputeDeckSuggestions:
    """Tests for the deck suggestion engine."""

    def _run(self, deck_cards, total_cards=None, total_lands=None, suggested_lands=None):
        """Helper that computes mana_curve and color_counts from deck_cards, then calls function."""
        mana_curve = {i: 0 for i in range(8)}
        color_counts: dict = {}
        for dc in deck_cards:
            if "Land" not in (dc.card.type_line or ""):
                cmc = int(dc.card.cmc or 0)
                mana_curve[min(cmc, 7)] += dc.quantity
            for c in dc.card.colors or []:
                color_counts[c] = color_counts.get(c, 0) + dc.quantity

        total_cards = (
            total_cards if total_cards is not None else sum(dc.quantity for dc in deck_cards)
        )
        total_lands = (
            total_lands
            if total_lands is not None
            else sum(dc.quantity for dc in deck_cards if "Land" in (dc.card.type_line or ""))
        )
        suggested_lands = (
            suggested_lands if suggested_lands is not None else round(total_cards * 17 / 40)
        )

        return _compute_deck_suggestions(
            deck_cards, mana_curve, color_counts, total_cards, total_lands, suggested_lands
        )

    # ── avg_cmc / curve_shape ─────────────────────────────────────────────────

    def test_aggro_curve_shape(self):
        deck = [_make_dc(_make_card(cmc=1.0, mana_cost="{R}", colors=["R"]), quantity=20)]
        result = self._run(deck, total_cards=37, total_lands=17, suggested_lands=16)
        assert result["curve_shape"] == "Aggro"
        assert result["avg_cmc"] == pytest.approx(1.0)

    def test_midrange_curve_shape(self):
        deck = [_make_dc(_make_card(cmc=3.0, mana_cost="{2}{G}", colors=["G"]), quantity=20)]
        result = self._run(deck, total_cards=40, total_lands=17, suggested_lands=17)
        assert result["curve_shape"] == "Midrange"

    def test_control_curve_shape(self):
        deck = [_make_dc(_make_card(cmc=4.0, mana_cost="{3}{U}", colors=["U"]), quantity=20)]
        result = self._run(deck, total_cards=40, total_lands=17, suggested_lands=17)
        assert result["curve_shape"] == "Control"

    def test_ramp_curve_shape(self):
        deck = [_make_dc(_make_card(cmc=7.0, mana_cost="{6}{G}", colors=["G"]), quantity=20)]
        result = self._run(deck, total_cards=40, total_lands=17, suggested_lands=17)
        assert result["curve_shape"] == "Ramp"

    # ── Mana curve warnings ───────────────────────────────────────────────────

    def test_high_cmc_cards_warning(self):
        """6 CMC-5+ cards in a 60-card deck should produce a warning."""
        deck = [
            _make_dc(_make_card(cmc=5.0, mana_cost="{4}{R}", colors=["R"]), quantity=6),
            _make_dc(_make_card(cmc=2.0, mana_cost="{1}{R}", colors=["R"]), quantity=18),
            _make_dc(_make_card(type_line="Basic Land — Mountain"), quantity=24),
        ]
        result = self._run(deck)
        assert "Mana Curve" in [s["category"] for s in result["suggestions"]]
        cmc_suggestion = next(s for s in result["suggestions"] if "CMC 5+" in s["title"])
        assert cmc_suggestion["severity"] == "warning"

    def test_very_high_cmc_cards_danger(self):
        """9 CMC-5+ cards in a 60-card deck should produce a danger."""
        deck = [
            _make_dc(_make_card(cmc=6.0, mana_cost="{5}{G}", colors=["G"]), quantity=9),
            _make_dc(_make_card(cmc=2.0, mana_cost="{1}{G}", colors=["G"]), quantity=15),
            _make_dc(_make_card(type_line="Basic Land — Forest"), quantity=24),
        ]
        result = self._run(deck)
        cmc_suggestion = next((s for s in result["suggestions"] if "CMC 5+" in s["title"]), None)
        assert cmc_suggestion is not None
        assert cmc_suggestion["severity"] == "danger"

    def test_curve_gap_detected(self):
        """A gap at CMC 2 (cards exist at 1 and 3+) should be flagged."""
        deck = [
            _make_dc(_make_card(cmc=1.0, mana_cost="{R}", colors=["R"]), quantity=8),
            _make_dc(_make_card(cmc=3.0, mana_cost="{2}{R}", colors=["R"]), quantity=8),
            _make_dc(_make_card(type_line="Basic Land — Mountain"), quantity=24),
        ]
        result = self._run(deck)
        gap_suggestion = next(
            (s for s in result["suggestions"] if "No 2-mana plays" in s["title"]), None
        )
        assert gap_suggestion is not None
        assert gap_suggestion["severity"] == "info"

    def test_aggro_no_one_drops_warning(self):
        """An aggro-shaped deck (avg CMC < 2) with no 1-drops should produce a warning."""
        from stats.views.decks import _compute_deck_suggestions

        # avg_cmc = (2*20) / 20 = 2.0, but non_land_count = 20, total_cards = 37, lands = 17
        # avg_cmc = 40/20 = 2.0 → Midrange, no aggro warning
        result_midrange = _compute_deck_suggestions(
            deck_cards=[
                _make_dc(_make_card(cmc=2.0, mana_cost="{1}{R}", colors=["R"]), quantity=20)
            ],
            mana_curve={0: 0, 1: 0, 2: 20, 3: 0, 4: 0, 5: 0, 6: 0, 7: 0},
            color_counts={"R": 20},
            total_cards=37,
            total_lands=17,
            suggested_lands=16,
        )
        # avg_cmc = 40/20 = 2.0 → Midrange, aggro warning should NOT fire
        assert not any(
            "1-mana plays in an aggro" in s["title"] for s in result_midrange["suggestions"]
        )

        # avg_cmc = (0*5 + 2*20) / (37-17) = 40/20 = 2.0 → Midrange (result_aggro variable unused, skip)
        # Use a mana_curve with only 0 and 2 costs but avg < 2:
        result_true_aggro = _compute_deck_suggestions(
            deck_cards=[_make_dc(_make_card(cmc=1.0, mana_cost="{R}", colors=["R"]), quantity=20)],
            mana_curve={0: 0, 1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0, 7: 0},
            color_counts={"R": 20},
            total_cards=37,
            total_lands=17,
            suggested_lands=16,
        )
        # avg_cmc = 0/20 = 0.0 → Aggro with no 1-drops (all zero) → warning fires
        assert any(
            "1-mana plays in an aggro" in s["title"] for s in result_true_aggro["suggestions"]
        )

    # ── Land count suggestions ────────────────────────────────────────────────

    def test_land_warning_two_off(self):
        deck = [_make_dc(_make_card(type_line="Basic Land — Plains"), quantity=15)]
        result = self._run(deck, total_cards=40, total_lands=15, suggested_lands=17)
        land_s = next((s for s in result["suggestions"] if s["category"] == "Lands"), None)
        assert land_s is not None
        assert land_s["severity"] == "warning"

    def test_land_danger_four_off(self):
        deck = [_make_dc(_make_card(type_line="Basic Land — Plains"), quantity=13)]
        result = self._run(deck, total_cards=40, total_lands=13, suggested_lands=17)
        land_s = next((s for s in result["suggestions"] if s["category"] == "Lands"), None)
        assert land_s is not None
        assert land_s["severity"] == "danger"

    def test_correct_land_count_no_suggestion(self):
        deck = [_make_dc(_make_card(type_line="Basic Land — Plains"), quantity=17)]
        result = self._run(deck, total_cards=40, total_lands=17, suggested_lands=17)
        assert not any(s["category"] == "Lands" for s in result["suggestions"])

    # ── Consistency ───────────────────────────────────────────────────────────

    def test_consistency_danger_many_one_ofs(self):
        """11+ one-ofs in a 60-card deck → danger."""
        deck = [
            _make_dc(
                _make_card(name=f"Card {i}", cmc=2.0, mana_cost="{1}{R}", colors=["R"]), quantity=1
            )
            for i in range(11)
        ] + [
            # Pad to reach >= 60 total cards
            _make_dc(_make_card(cmc=2.0, mana_cost="{1}{R}", colors=["R"]), quantity=4),
            _make_dc(_make_card(cmc=2.0, mana_cost="{1}{R}", colors=["R"]), quantity=4),
            _make_dc(_make_card(type_line="Basic Land — Mountain"), quantity=20),
            _make_dc(_make_card(type_line="Basic Land — Mountain"), quantity=21),
        ]
        result = self._run(deck)
        cons_s = next((s for s in result["suggestions"] if s["category"] == "Consistency"), None)
        assert cons_s is not None
        assert cons_s["severity"] == "danger"

    def test_consistency_warning_seven_one_ofs(self):
        """7–10 one-ofs in a 60-card deck → warning."""
        deck = [
            _make_dc(
                _make_card(name=f"Card {i}", cmc=2.0, mana_cost="{1}{R}", colors=["R"]), quantity=1
            )
            for i in range(7)
        ] + [
            _make_dc(_make_card(cmc=4.0, mana_cost="{3}{R}", colors=["R"]), quantity=4),
            _make_dc(_make_card(cmc=4.0, mana_cost="{3}{R}", colors=["R"]), quantity=4),
            _make_dc(_make_card(type_line="Basic Land — Mountain"), quantity=25),
            _make_dc(_make_card(type_line="Basic Land — Mountain"), quantity=20),
        ]
        result = self._run(deck)
        cons_s = next((s for s in result["suggestions"] if s["category"] == "Consistency"), None)
        assert cons_s is not None
        assert cons_s["severity"] == "warning"

    def test_no_consistency_warning_40_card_deck(self):
        """One-ofs in a 40-card deck are expected and should not trigger a warning."""
        deck = [
            _make_dc(
                _make_card(name=f"Card {i}", cmc=2.0, mana_cost="{1}{G}", colors=["G"]), quantity=1
            )
            for i in range(12)
        ] + [_make_dc(_make_card(type_line="Basic Land — Forest"), quantity=17)]
        result = self._run(deck)
        assert not any(s["category"] == "Consistency" for s in result["suggestions"])

    # ── Sideboard ─────────────────────────────────────────────────────────────

    def test_no_sideboard_info(self):
        """Deck with no sideboard should produce an info suggestion."""
        deck = [
            _make_dc(_make_card(cmc=2.0, mana_cost="{1}{U}", colors=["U"]), quantity=24),
            _make_dc(_make_card(type_line="Basic Land — Island"), quantity=24),
        ]
        result = self._run(deck)
        sb = next((s for s in result["suggestions"] if s["category"] == "Sideboard"), None)
        assert sb is not None
        assert sb["severity"] == "info"

    def test_oversized_sideboard_warning(self):
        """A 16-card sideboard should produce a warning."""
        deck = [
            _make_dc(_make_card(cmc=2.0, mana_cost="{1}{U}", colors=["U"]), quantity=10),
            _make_dc(_make_card(type_line="Basic Land — Island"), quantity=17),
            _make_dc(
                _make_card(name="SB Card", cmc=1.0, mana_cost="{U}", colors=["U"]),
                quantity=16,
                is_sideboard=True,
            ),
        ]
        result = self._run(deck)
        sb = next((s for s in result["suggestions"] if "sideboard" in s["title"].lower()), None)
        assert sb is not None
        assert sb["severity"] == "warning"

    # ── Copy-count distribution ───────────────────────────────────────────────

    def test_copy_count_totals(self):
        deck = [
            _make_dc(_make_card(cmc=1.0, mana_cost="{R}", colors=["R"]), quantity=1),  # 1-of
            _make_dc(_make_card(cmc=2.0, mana_cost="{1}{R}", colors=["R"]), quantity=2),  # 2-of
            _make_dc(_make_card(cmc=3.0, mana_cost="{2}{R}", colors=["R"]), quantity=3),  # 3-of
            _make_dc(_make_card(cmc=4.0, mana_cost="{3}{R}", colors=["R"]), quantity=4),  # 4-of
            _make_dc(_make_card(type_line="Basic Land — Mountain"), quantity=17),
        ]
        result = self._run(deck)
        assert result["one_ofs"] == 1
        assert result["two_ofs"] == 1
        assert result["three_ofs"] == 1
        assert result["four_ofs"] == 1

    def test_sideboard_excluded_from_copy_counts(self):
        deck = [
            _make_dc(_make_card(cmc=2.0, mana_cost="{1}{W}", colors=["W"]), quantity=4),
            _make_dc(_make_card(type_line="Basic Land — Plains"), quantity=17),
            _make_dc(
                _make_card(name="SB Hate", cmc=1.0, mana_cost="{W}", colors=["W"]),
                quantity=3,
                is_sideboard=True,
            ),
        ]
        result = self._run(deck)
        assert result["four_ofs"] == 1
        assert result["three_ofs"] == 0  # sideboard 3-of not counted

    # ── pip_summary ───────────────────────────────────────────────────────────

    def test_pip_summary_content(self):
        deck = [
            _make_dc(_make_card(cmc=2.0, mana_cost="{W}{U}", colors=["W", "U"]), quantity=10),
            _make_dc(_make_card(type_line="Basic Land"), quantity=17),
        ]
        result = self._run(deck)
        # Both W and U should appear in pip_summary
        assert "W:" in result["pip_summary"]
        assert "U:" in result["pip_summary"]

    def test_pip_summary_no_colored_pips(self):
        deck = [
            _make_dc(_make_card(cmc=3.0, mana_cost="{3}", colors=[]), quantity=10),
            _make_dc(_make_card(type_line="Basic Land"), quantity=17),
        ]
        result = self._run(deck)
        assert result["pip_summary"] == "No colored pips"

    # ── Empty deck ────────────────────────────────────────────────────────────

    def test_empty_deck_no_crash(self):
        """An empty deck should return sensible defaults without crashing."""
        result = _compute_deck_suggestions(
            deck_cards=[],
            mana_curve={i: 0 for i in range(8)},
            color_counts={},
            total_cards=0,
            total_lands=0,
            suggested_lands=0,
        )
        assert result["avg_cmc"] == 0.0
        assert result["curve_shape"] == "Aggro"
        assert isinstance(result["suggestions"], list)

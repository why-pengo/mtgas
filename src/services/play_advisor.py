"""
Play Advisor Service — per-match strategic play analysis.

Analyses a completed MTGA match and produces per-turn suggestions covering:

- **Mana efficiency**: turns where the player left significant mana unused while
  having castable spells available.
- **Play ordering**: cases where the player cast a higher-CMC spell before a
  lower-CMC spell in the same turn (suboptimal sequencing).
- **Missed plays (persistent)**: cards that appeared in the player's available
  options for 3+ consecutive turns without being played when mana permitted.
- **Alternate plays**: turns where a cheaper castable alternative existed but was
  not chosen.

Data model notes
----------------
``GameAction`` rows represent *available options* at each game state — not taken
actions. Each time MTGA sends a ``GREMessageType_GameStateMessage`` it re-broadcasts
the full legal-action menu. ``ActionType_Cast`` / ``ActionType_Play`` rows show what
the player *could* cast; ``ActionType_Activate_Mana`` rows count untapped mana
sources (proxy for available mana).

``ZoneTransfer`` rows with ``category IN ('CastSpell', 'PlayLand')`` originating
from the player's Hand zone represent *actual decisions* taken.

Zone IDs are dynamic per-match integers; ``build_zone_labels()`` infers roles.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stats.models import Match

from stats.utils.zone_utils import build_zone_labels, get_player_hand_zone

logger = logging.getLogger(__name__)

# Minimum mana left unused to trigger a mana-efficiency suggestion
MANA_UNDERSPEND_THRESHOLD = 2

# How many consecutive turns a card must be held (and be castable) to flag it
MISSED_PLAY_TURN_THRESHOLD = 3

# Maximum CMC for a card to be considered a "cheap alternative" vs a more
# expensive card that was actually played.
CHEAP_ALTERNATIVE_MAX_CMC_RATIO = 0.6  # e.g. 2-drop vs 4-drop


@dataclass
class Suggestion:
    """A single actionable suggestion for a turn."""

    type: str  # "mana_efficiency" | "ordering" | "missed_play" | "alternate_play"
    severity: str  # "tip" | "warning"
    title: str
    body: str
    cards_played: list[str] = field(default_factory=list)
    cards_suggested: list[str] = field(default_factory=list)


@dataclass
class TurnAnalysis:
    """Analysis of a single turn."""

    turn_number: int
    is_player_turn: bool
    life_you: int
    life_opp: int
    mana_available: int
    mana_spent: float  # sum of CMC of spells cast (float because Card.cmc is float)
    plays_made: list[str]  # card names actually played / cast
    suggestions: list[Suggestion] = field(default_factory=list)


@dataclass
class MatchAnalysis:
    """Top-level analysis result for a single match."""

    match_id: int
    total_turns: int
    turns: list[TurnAnalysis] = field(default_factory=list)
    overall_mana_efficiency: float = 0.0  # weighted % of available mana spent
    total_suggestions: int = 0
    has_data: bool = False


class PlayAdvisor:
    """
    Generates strategic play suggestions for a single Match.

    Usage::

        from src.services.play_advisor import PlayAdvisor
        analysis = PlayAdvisor(match).analyze()
    """

    def __init__(self, match: Match) -> None:
        self.match = match

    def analyze(self) -> MatchAnalysis:
        """
        Run full analysis and return a MatchAnalysis.

        Returns a MatchAnalysis with ``has_data=False`` when the match has no
        GameAction or ZoneTransfer data (e.g. imported from an older log file).
        """
        from stats.models import GameAction, ZoneTransfer

        match = self.match

        # --- Load data ---------------------------------------------------
        zone_transfers = list(
            ZoneTransfer.objects.filter(match=match)
            .select_related("card")
            .order_by("game_state_id", "id")
        )
        game_actions = list(
            GameAction.objects.filter(
                match=match,
                action_type__in=["ActionType_Cast", "ActionType_Play", "ActionType_Activate_Mana"],
                seat_id=match.player_seat_id,
            )
            .select_related("card")
            .order_by("game_state_id", "id")
        )
        life_changes = list(match.life_changes.order_by("game_state_id", "id"))

        result = MatchAnalysis(match_id=match.pk, total_turns=match.total_turns or 0)

        if not zone_transfers and not game_actions:
            return result

        result.has_data = True

        # --- Zone label inference -----------------------------------------
        zone_labels = build_zone_labels(zone_transfers)
        player_hand_zone = get_player_hand_zone(zone_transfers, zone_labels)

        if player_hand_zone is None:
            # Cannot determine hand zone — analysis not possible
            logger.debug("PlayAdvisor: could not identify player hand zone for match %d", match.pk)
            return result

        # --- Index game actions by (turn, main-phase) -------------------
        # Collect the earliest game_state_id per turn in Main1/Main2.
        # Available spells = Cast/Play options at those states.
        # Available mana   = count of Activate_Mana options at those states.

        # Build: turn -> list of (game_state_id, action_type, card_name, cmc)
        turn_available: dict[int, list[tuple[int, str, str, float]]] = defaultdict(list)
        # Build: turn -> max mana available (from earliest main-phase snapshot)
        turn_mana: dict[int, int] = {}

        # For missed-play tracking: (turn, card_name, cmc) triples from Cast options
        turn_cast_options: dict[int, set[tuple[str, float]]] = defaultdict(set)

        # Find first main-phase game_state_id per turn
        first_main_gsid: dict[int, int] = {}

        for ga in game_actions:
            turn = ga.turn_number or 0
            gsid = ga.game_state_id or 0
            phase = ga.phase or ""

            is_main = phase in ("Phase_Main1", "Phase_Main2", "")
            if is_main and (turn not in first_main_gsid or gsid < first_main_gsid[turn]):
                first_main_gsid[turn] = gsid

        # Index options at each main-phase game state
        for ga in game_actions:
            turn = ga.turn_number or 0
            gsid = ga.game_state_id or 0
            phase = ga.phase or ""
            is_main = phase in ("Phase_Main1", "Phase_Main2", "")
            if not is_main:
                continue

            # Only use the earliest snapshot per turn (avoids counting options
            # that disappear after mana is spent mid-turn)
            if first_main_gsid.get(turn) != gsid:
                continue

            card_name = ga.card.name if ga.card else None
            cmc = float(ga.card.cmc) if ga.card and ga.card.cmc is not None else 0.0

            if ga.action_type == "ActionType_Activate_Mana":
                turn_mana[turn] = turn_mana.get(turn, 0) + 1
            elif ga.action_type in ("ActionType_Cast", "ActionType_Play") and card_name:
                turn_available[turn].append((gsid, ga.action_type, card_name, cmc))
                turn_cast_options[turn].add((card_name, cmc))

        # --- Identify actual plays from zone transfers ------------------
        # CastSpell: Hand → Stack  (spell was cast)
        # PlayLand:  Hand → Battlefield  (land was played)

        stack_zone = next((z for z, l in zone_labels.items() if l == "Stack"), None)
        battlefield_zone = next((z for z, l in zone_labels.items() if l == "Battlefield"), None)

        # turn -> list of (game_state_id, card_name, cmc, is_land)
        turn_plays: dict[int, list[tuple[int, str, float, bool]]] = defaultdict(list)

        for zt in zone_transfers:
            if zt.card_id is None:
                continue
            if str(zt.from_zone) != player_hand_zone:
                continue
            card_name = zt.card.name if zt.card else None
            if not card_name:
                continue
            cmc = float(zt.card.cmc) if zt.card and zt.card.cmc is not None else 0.0
            turn = zt.turn_number or 0
            gsid = zt.game_state_id or 0

            to_zone = str(zt.to_zone) if zt.to_zone is not None else ""
            if to_zone == stack_zone:
                turn_plays[turn].append((gsid, card_name, cmc, False))
            elif to_zone == battlefield_zone:
                # Land plays or permanents played directly (e.g. flash, auras)
                is_land = zt.card.type_line is not None and "Land" in (zt.card.type_line or "")
                turn_plays[turn].append((gsid, card_name, cmc, is_land))

        # --- Life total tracker ----------------------------------------
        life_events = sorted(
            [(lc.game_state_id or 0, lc.seat_id, lc.life_total) for lc in life_changes],
            key=lambda x: x[0],
        )

        def life_at_turn(turn: int) -> tuple[int, int]:
            """Return (life_you, life_opp) at the start of given turn."""
            you = 20
            opp = 20
            # Find the game_state_id range for this turn
            target_gsid = first_main_gsid.get(turn, 0)
            for gsid, seat, life in life_events:
                if gsid > target_gsid:
                    break
                if seat == match.player_seat_id:
                    you = life
                elif seat == match.opponent_seat_id:
                    opp = life
            return you, opp

        # --- Determine active player per turn --------------------------
        # Odd turns are usually player 1's; even turns player 2's. But seat IDs
        # vary, so we use the majority-action player seat in each turn as a proxy.
        # Simpler heuristic: player's turn = turns where they have available plays.
        def is_player_turn(turn: int) -> bool:
            """Heuristic: if the player had Cast/Play options on this turn it was their priority."""
            return turn in turn_cast_options and len(turn_cast_options[turn]) > 0

        # --- Per-turn analysis -----------------------------------------
        all_turns = sorted(set(list(turn_plays.keys()) + list(turn_cast_options.keys())))

        # Track how many consecutive turns each card has been available-but-unplayed
        card_held_turns: dict[str, int] = defaultdict(int)
        flagged_missed: set[str] = set()  # avoid re-flagging same card

        total_mana_avail = 0
        total_mana_spent = 0.0

        for turn in all_turns:
            if turn == 0:
                continue

            plays = sorted(turn_plays.get(turn, []), key=lambda p: p[0])  # sort by gsid
            plays_made = [p[1] for p in plays]
            mana_spent = sum(p[2] for p in plays if not p[3])  # exclude lands from mana cost
            mana_available = turn_mana.get(turn, 0)
            life_you, life_opp = life_at_turn(turn)
            player_turn = is_player_turn(turn)

            total_mana_avail += mana_available
            total_mana_spent += mana_spent

            suggestions: list[Suggestion] = []

            if not player_turn:
                # Skip opponent turns for suggestions
                result.turns.append(
                    TurnAnalysis(
                        turn_number=turn,
                        is_player_turn=False,
                        life_you=life_you,
                        life_opp=life_opp,
                        mana_available=mana_available,
                        mana_spent=mana_spent,
                        plays_made=plays_made,
                    )
                )
                continue

            available_cast = [(name, cmc) for _, _, name, cmc in turn_available.get(turn, [])]
            played_names = set(plays_made)
            unplayed = [(name, cmc) for name, cmc in available_cast if name not in played_names]

            # --- Rule 1: Mana Underspend -----------------------------------
            mana_left = mana_available - mana_spent
            if mana_left >= MANA_UNDERSPEND_THRESHOLD and unplayed:
                # Filter to spells that would have been affordable
                affordable = [name for name, cmc in unplayed if cmc <= mana_left and cmc > 0]
                if affordable:
                    unique_affordable = list(dict.fromkeys(affordable))[:4]
                    body = (
                        f"You spent {int(mana_spent)} of {mana_available} available mana "
                        f"(left {int(mana_left)} unused). "
                        f"Could have also cast: {', '.join(unique_affordable)}."
                    )
                    suggestions.append(
                        Suggestion(
                            type="mana_efficiency",
                            severity="warning" if mana_left >= 3 else "tip",
                            title=f"Left {int(mana_left)} mana unused",
                            body=body,
                            cards_played=plays_made,
                            cards_suggested=unique_affordable,
                        )
                    )

            # --- Rule 2: Play Ordering ------------------------------------
            # Flag if the player cast a higher-CMC spell before a lower-CMC spell
            # in the same main phase (and both are non-instant non-land spells).
            non_land_plays = [
                (gsid, name, cmc) for gsid, name, cmc, is_land in plays if not is_land
            ]
            for i in range(len(non_land_plays) - 1):
                gsid_a, name_a, cmc_a = non_land_plays[i]
                gsid_b, name_b, cmc_b = non_land_plays[i + 1]
                if gsid_a < gsid_b and cmc_a > cmc_b and cmc_b > 0 and cmc_a > cmc_b + 1:
                    suggestions.append(
                        Suggestion(
                            type="ordering",
                            severity="tip",
                            title="Consider playing lower-cost spells first",
                            body=(
                                f"You cast {name_a} ({int(cmc_a)} mana) before "
                                f"{name_b} ({int(cmc_b)} mana). Playing cheaper spells first "
                                "preserves mana flexibility for instant-speed responses."
                            ),
                            cards_played=[name_a, name_b],
                            cards_suggested=[name_b, name_a],
                        )
                    )

            # --- Rule 3: Missed Plays (persistent) -----------------------
            castable_this_turn = {
                name for name, cmc in available_cast if cmc <= mana_available and cmc > 0
            }
            for name in castable_this_turn:
                if name not in played_names:
                    card_held_turns[name] += 1
                else:
                    card_held_turns[name] = 0  # reset if they played it

            for name in list(card_held_turns.keys()):
                if name in played_names:
                    # Played this turn — reset and clear flag
                    card_held_turns.pop(name, None)
                    flagged_missed.discard(name)

            for name, held in card_held_turns.items():
                if held >= MISSED_PLAY_TURN_THRESHOLD and name not in flagged_missed:
                    flagged_missed.add(name)
                    suggestions.append(
                        Suggestion(
                            type="missed_play",
                            severity="tip",
                            title=f"Held {name} for {held} turns",
                            body=(
                                f"You've had {name} in hand for {held} consecutive turns "
                                "while having enough mana to cast it. "
                                "Consider whether holding it is part of a deliberate strategy."
                            ),
                            cards_played=[],
                            cards_suggested=[name],
                        )
                    )

            # --- Rule 4: Alternate Play ----------------------------------
            # If player cast a spell with CMC >= 3 and had at least one cheaper
            # alternative that would also have been affordable.
            for gsid, played_name, played_cmc, is_land in plays:
                if is_land or played_cmc < 3:
                    continue
                cheaper = [
                    name
                    for name, cmc in available_cast
                    if cmc < played_cmc * CHEAP_ALTERNATIVE_MAX_CMC_RATIO
                    and cmc > 0
                    and name != played_name
                    and name not in played_names  # not already played this turn
                ]
                if cheaper:
                    unique_cheaper = list(dict.fromkeys(cheaper))[:3]
                    suggestions.append(
                        Suggestion(
                            type="alternate_play",
                            severity="tip",
                            title=f"Cheaper alternatives to {played_name}",
                            body=(
                                f"When you cast {played_name} ({int(played_cmc)} mana), "
                                f"you also had cheaper options available: "
                                f"{', '.join(unique_cheaper)}. "
                                "Playing these first can improve your board presence and "
                                "leave mana up for responses."
                            ),
                            cards_played=[played_name],
                            cards_suggested=unique_cheaper,
                        )
                    )

            result.turns.append(
                TurnAnalysis(
                    turn_number=turn,
                    is_player_turn=True,
                    life_you=life_you,
                    life_opp=life_opp,
                    mana_available=mana_available,
                    mana_spent=mana_spent,
                    plays_made=plays_made,
                    suggestions=suggestions,
                )
            )

        # --- Overall stats -------------------------------------------
        result.overall_mana_efficiency = (
            round(total_mana_spent / total_mana_avail * 100, 1) if total_mana_avail > 0 else 0.0
        )
        result.total_suggestions = sum(len(t.suggestions) for t in result.turns)

        return result

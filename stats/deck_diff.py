"""
Deck diff utility.

Computes the card-level difference between two DeckSnapshots so the UI
can display which cards were added, removed, or unchanged between matches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import DeckSnapshot


@dataclass
class CardDelta:
    """A single card entry in a deck diff."""

    grp_id: int
    name: str
    quantity_before: int = 0
    quantity_after: int = 0

    @property
    def delta(self) -> int:
        return self.quantity_after - self.quantity_before

    @property
    def status(self) -> str:
        """'added', 'removed', or 'unchanged'."""
        if self.quantity_before == 0:
            return "added"
        if self.quantity_after == 0:
            return "removed"
        if self.delta != 0:
            return "changed"
        return "unchanged"


@dataclass
class ZoneDiff:
    """Diff for mainboard or sideboard."""

    added: list[CardDelta] = field(default_factory=list)
    removed: list[CardDelta] = field(default_factory=list)
    changed: list[CardDelta] = field(default_factory=list)
    unchanged: list[CardDelta] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.changed)


@dataclass
class DeckDiff:
    """Full diff between two DeckSnapshots."""

    mainboard: ZoneDiff = field(default_factory=ZoneDiff)
    sideboard: ZoneDiff = field(default_factory=ZoneDiff)

    @property
    def has_changes(self) -> bool:
        return self.mainboard.has_changes or self.sideboard.has_changes


def compute_deck_diff(snap_before: DeckSnapshot | None, snap_after: DeckSnapshot) -> DeckDiff:
    """
    Compute the diff between two snapshots.

    snap_before may be None (first version of the deck), in which case all
    cards in snap_after are treated as "added".
    """
    diff = DeckDiff()
    _compute_zone_diff(snap_before, snap_after, is_sideboard=False, zone_diff=diff.mainboard)
    _compute_zone_diff(snap_before, snap_after, is_sideboard=True, zone_diff=diff.sideboard)
    return diff


def _compute_zone_diff(
    snap_before: DeckSnapshot | None,
    snap_after: DeckSnapshot,
    *,
    is_sideboard: bool,
    zone_diff: ZoneDiff,
) -> None:
    before_map: dict[int, tuple[int, str]] = {}  # grp_id → (quantity, name)
    if snap_before is not None:
        for dc in snap_before.cards.filter(is_sideboard=is_sideboard).select_related("card"):
            before_map[dc.card.grp_id] = (dc.quantity, dc.card.name or f"({dc.card.grp_id})")

    after_map: dict[int, tuple[int, str]] = {}
    for dc in snap_after.cards.filter(is_sideboard=is_sideboard).select_related("card"):
        after_map[dc.card.grp_id] = (dc.quantity, dc.card.name or f"({dc.card.grp_id})")

    all_grp_ids = set(before_map) | set(after_map)

    for grp_id in sorted(all_grp_ids):
        qty_before, name_before = before_map.get(grp_id, (0, ""))
        qty_after, name_after = after_map.get(grp_id, (0, ""))
        name = name_after or name_before

        delta = CardDelta(
            grp_id=grp_id,
            name=name,
            quantity_before=qty_before,
            quantity_after=qty_after,
        )
        status = delta.status
        if status == "added":
            zone_diff.added.append(delta)
        elif status == "removed":
            zone_diff.removed.append(delta)
        elif status == "changed":
            zone_diff.changed.append(delta)
        else:
            zone_diff.unchanged.append(delta)

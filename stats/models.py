"""
Django models for MTG Arena Statistics Tracker.
"""

from __future__ import annotations

from django.db import models


class Card(models.Model):
    """Card information cached from Scryfall bulk data."""

    grp_id = models.IntegerField(primary_key=True, help_text="Arena's card group ID")
    name = models.CharField(max_length=255, null=True, blank=True)
    mana_cost = models.CharField(max_length=100, null=True, blank=True)
    cmc = models.FloatField(null=True, blank=True, help_text="Converted mana cost")
    type_line = models.CharField(max_length=255, null=True, blank=True)
    colors = models.JSONField(default=list, blank=True)
    color_identity = models.JSONField(default=list, blank=True)
    set_code = models.CharField(max_length=10, null=True, blank=True)
    rarity = models.CharField(max_length=20, null=True, blank=True)
    oracle_text = models.TextField(null=True, blank=True)
    power = models.CharField(max_length=10, null=True, blank=True)
    toughness = models.CharField(max_length=10, null=True, blank=True)
    scryfall_id = models.CharField(max_length=50, null=True, blank=True)
    image_uri = models.URLField(max_length=500, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "cards"
        verbose_name_plural = "Cards"

    def __str__(self) -> str:
        return self.name or f"Unknown ({self.grp_id})"


class Deck(models.Model):
    """Stores information about each deck."""

    deck_id = models.CharField(max_length=100, unique=True, help_text="UUID from MTG Arena")
    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    format = models.CharField(max_length=50, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "decks"

    def __str__(self) -> str:
        return self.name

    def total_cards(self) -> int:
        return sum(dc.quantity for dc in self.deck_cards.filter(is_sideboard=False))

    def win_rate(self) -> float:
        games = self.matches.filter(result__isnull=False).count()
        wins = self.matches.filter(result="win").count()
        return round(wins / games * 100, 1) if games > 0 else 0


class DeckCard(models.Model):
    """Cards in a deck."""

    deck = models.ForeignKey(Deck, on_delete=models.CASCADE, related_name="deck_cards")
    card = models.ForeignKey(Card, on_delete=models.CASCADE, db_column="card_grp_id")
    quantity = models.IntegerField(default=1)
    is_sideboard = models.BooleanField(default=False)

    class Meta:
        db_table = "deck_cards"
        unique_together = ("deck", "card", "is_sideboard")

    def __str__(self) -> str:
        return f"{self.quantity}x {self.card.name}"


class Match(models.Model):
    """Stores information about each match/game."""

    RESULT_CHOICES = [
        ("win", "Win"),
        ("loss", "Loss"),
        ("draw", "Draw"),
        ("incomplete", "Incomplete"),
    ]

    # Primary identifier - using match_id from Arena
    match_id = models.CharField(
        max_length=100, unique=True, primary_key=False, help_text="UUID from MTG Arena"
    )
    game_number = models.IntegerField(default=1, help_text="Game number within match (Bo3)")

    # Player info (the user)
    player_seat_id = models.IntegerField(null=True, blank=True)
    player_name = models.CharField(max_length=255, null=True, blank=True)
    player_user_id = models.CharField(max_length=100, null=True, blank=True)

    # Opponent info
    opponent_seat_id = models.IntegerField(null=True, blank=True)
    opponent_name = models.CharField(max_length=255, null=True, blank=True)
    opponent_user_id = models.CharField(max_length=100, null=True, blank=True)

    # Match details
    deck = models.ForeignKey(
        Deck, on_delete=models.SET_NULL, null=True, blank=True, related_name="matches"
    )
    event_id = models.CharField(
        max_length=100, null=True, blank=True, help_text="Ladder, Traditional_Standard, etc."
    )
    format = models.CharField(max_length=50, null=True, blank=True)
    match_type = models.CharField(max_length=50, null=True, blank=True)

    # Result
    result = models.CharField(max_length=20, choices=RESULT_CHOICES, null=True, blank=True)
    winning_team_id = models.IntegerField(null=True, blank=True)
    winning_reason = models.CharField(max_length=100, null=True, blank=True)

    # Player states at end
    player_final_life = models.IntegerField(null=True, blank=True)
    opponent_final_life = models.IntegerField(null=True, blank=True)

    # Timing
    start_time = models.DateTimeField(null=True, blank=True)
    end_time = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.IntegerField(null=True, blank=True)
    total_turns = models.IntegerField(null=True, blank=True)

    # Import tracking
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "matches"
        ordering = ["-start_time"]
        indexes = [
            models.Index(fields=["match_id"]),
            models.Index(fields=["start_time"]),
            models.Index(fields=["result"]),
            models.Index(fields=["opponent_name"]),
        ]

    def __str__(self) -> str:
        result_str = self.result or "incomplete"
        return f"{self.match_id[:8]}... vs {self.opponent_name} ({result_str})"

    def duration_display(self) -> str | None:
        if not self.duration_seconds:
            return None
        mins = self.duration_seconds // 60
        secs = self.duration_seconds % 60
        return f"{mins}m {secs}s"


class GameAction(models.Model):
    """Stores each action/play during a game."""

    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="actions")

    # Action context
    game_state_id = models.IntegerField(null=True, blank=True)
    turn_number = models.IntegerField(null=True, blank=True)
    phase = models.CharField(max_length=50, null=True, blank=True)
    step = models.CharField(max_length=50, null=True, blank=True)
    active_player_seat = models.IntegerField(null=True, blank=True)

    # Action details
    seat_id = models.IntegerField(null=True, blank=True, help_text="Who performed the action")
    action_type = models.CharField(max_length=50)
    instance_id = models.IntegerField(null=True, blank=True)
    card = models.ForeignKey(
        Card, on_delete=models.SET_NULL, null=True, blank=True, db_column="card_grp_id"
    )
    ability_grp_id = models.IntegerField(null=True, blank=True)

    # Mana information
    mana_cost = models.JSONField(null=True, blank=True)

    # Targeting
    target_ids = models.JSONField(null=True, blank=True)

    # Timestamp
    timestamp_ms = models.BigIntegerField(null=True, blank=True)

    class Meta:
        db_table = "game_actions"
        ordering = ["game_state_id", "id"]
        indexes = [
            models.Index(fields=["match", "turn_number"]),
        ]

    def __str__(self) -> str:
        card_name = self.card.name if self.card else "Unknown"
        return f"{self.action_type}: {card_name}"


class LifeChange(models.Model):
    """Stores life total changes during the game."""

    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="life_changes")
    game_state_id = models.IntegerField(null=True, blank=True)
    turn_number = models.IntegerField(null=True, blank=True)
    seat_id = models.IntegerField()
    life_total = models.IntegerField()
    change_amount = models.IntegerField(null=True, blank=True)
    source_instance_id = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = "life_changes"
        ordering = ["game_state_id", "id"]

    def __str__(self) -> str:
        return f"Seat {self.seat_id}: {self.life_total} life"


class ZoneTransfer(models.Model):
    """Stores zone transfers (cards moving between zones)."""

    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="zone_transfers")
    game_state_id = models.IntegerField(null=True, blank=True)
    turn_number = models.IntegerField(null=True, blank=True)
    instance_id = models.IntegerField(null=True, blank=True)
    card = models.ForeignKey(
        Card, on_delete=models.SET_NULL, null=True, blank=True, db_column="card_grp_id"
    )
    from_zone = models.CharField(max_length=50, null=True, blank=True)
    to_zone = models.CharField(max_length=50, null=True, blank=True)
    category = models.CharField(max_length=50, null=True, blank=True)

    class Meta:
        db_table = "zone_transfers"
        ordering = ["game_state_id", "id"]

    def __str__(self) -> str:
        card_name = self.card.name if self.card else "Unknown"
        return f"{card_name}: {self.from_zone} → {self.to_zone}"


class ImportSession(models.Model):
    """Tracks log file import sessions for batch processing."""

    log_file = models.CharField(max_length=500)
    file_size = models.BigIntegerField(null=True, blank=True)
    file_modified = models.DateTimeField(null=True, blank=True)
    matches_imported = models.IntegerField(default=0)
    matches_skipped = models.IntegerField(default=0)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        default="pending",
        choices=[
            ("pending", "Pending"),
            ("running", "Running"),
            ("completed", "Completed"),
            ("failed", "Failed"),
        ],
    )
    error_message = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "import_sessions"
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"Import {self.started_at.strftime('%Y-%m-%d %H:%M')} - {self.status}"


class UnknownCard(models.Model):
    """Tracks unknown cards discovered during import for manual resolution."""

    card = models.ForeignKey(
        Card, on_delete=models.CASCADE, related_name="unknown_occurrences", db_column="grp_id"
    )
    match = models.ForeignKey(
        Match, on_delete=models.SET_NULL, null=True, blank=True, related_name="unknown_cards"
    )
    deck = models.ForeignKey(
        Deck, on_delete=models.SET_NULL, null=True, blank=True, related_name="unknown_cards"
    )
    import_session = models.ForeignKey(
        ImportSession,
        on_delete=models.CASCADE,
        related_name="unknown_cards",
        help_text="Import session where this unknown card was discovered",
    )
    raw_data = models.JSONField(
        default=dict,
        blank=True,
        help_text="Raw card data from log file if available (for debugging)",
    )
    is_resolved = models.BooleanField(
        default=False, help_text="Whether card has been manually resolved"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "unknown_cards"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["is_resolved"]),
            models.Index(fields=["card", "is_resolved"]),
        ]

    def __str__(self) -> str:
        return f"Unknown: {self.card.name} (grp_id: {self.card.grp_id})"

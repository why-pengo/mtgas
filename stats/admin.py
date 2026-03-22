"""
Django admin configuration for MTG Arena Statistics.
"""

from django.contrib import admin

from .models import (
    Card,
    CardToken,
    CardTokenRef,
    Deck,
    DeckCard,
    DeckSnapshot,
    ImportSession,
    Match,
    UnknownCard,
)


@admin.register(Card)
class CardAdmin(admin.ModelAdmin):
    list_display = ("grp_id", "name", "mana_cost", "type_line", "rarity")
    search_fields = ("name", "grp_id")
    list_filter = ("rarity", "set_code")


@admin.register(CardToken)
class CardTokenAdmin(admin.ModelAdmin):
    list_display = ("scryfall_id", "name", "type_line", "power", "toughness")
    search_fields = ("name", "scryfall_id")


@admin.register(CardTokenRef)
class CardTokenRefAdmin(admin.ModelAdmin):
    list_display = ("card", "token")
    raw_id_fields = ("card", "token")
    search_fields = ("card__name", "token__name")


class DeckCardInline(admin.TabularInline):
    model = DeckCard
    extra = 0
    raw_id_fields = ("card",)


class DeckSnapshotInline(admin.TabularInline):
    model = DeckSnapshot
    extra = 0
    fields = ("created_at", "total_cards_display", "sideboard_count_display")
    readonly_fields = ("created_at", "total_cards_display", "sideboard_count_display")
    show_change_link = True

    def total_cards_display(self, obj):
        return obj.total_cards()

    total_cards_display.short_description = "Mainboard"

    def sideboard_count_display(self, obj):
        return obj.sideboard_count()

    sideboard_count_display.short_description = "Sideboard"


@admin.register(Deck)
class DeckAdmin(admin.ModelAdmin):
    list_display = ("name", "format", "snapshot_count", "created_at")
    search_fields = ("name", "deck_id")
    list_filter = ("format",)
    inlines = [DeckSnapshotInline]

    def snapshot_count(self, obj):
        return obj.snapshots.count()

    snapshot_count.short_description = "Versions"


@admin.register(DeckSnapshot)
class DeckSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        "deck",
        "match_count_display",
        "total_cards_display",
        "sideboard_count_display",
        "created_at",
    )
    search_fields = ("deck__name", "deck__deck_id")
    list_filter = ("deck__format",)
    raw_id_fields = ("deck",)
    inlines = [DeckCardInline]

    def match_count_display(self, obj):
        return obj.matches.count()

    match_count_display.short_description = "Matches"

    def total_cards_display(self, obj):
        return obj.total_cards()

    total_cards_display.short_description = "Mainboard"

    def sideboard_count_display(self, obj):
        return obj.sideboard_count()

    sideboard_count_display.short_description = "Sideboard"


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = ("match_id_short", "opponent_name", "result", "deck", "event_id", "start_time")
    search_fields = ("match_id", "opponent_name", "player_name")
    list_filter = ("result", "event_id")
    raw_id_fields = ("deck",)
    date_hierarchy = "start_time"

    def match_id_short(self, obj):
        return obj.match_id[:8] + "..." if obj.match_id else "-"

    match_id_short.short_description = "Match ID"


@admin.register(ImportSession)
class ImportSessionAdmin(admin.ModelAdmin):
    list_display = ("started_at", "status", "matches_imported", "matches_skipped", "log_file_short")
    list_filter = ("status",)
    date_hierarchy = "started_at"

    def log_file_short(self, obj):
        return obj.log_file[-50:] if obj.log_file else "-"

    log_file_short.short_description = "Log File"


@admin.register(UnknownCard)
class UnknownCardAdmin(admin.ModelAdmin):
    list_display = (
        "card_grp_id",
        "card_name",
        "deck",
        "import_session",
        "is_resolved",
        "created_at",
    )
    list_filter = ("is_resolved", "created_at")
    search_fields = ("card__grp_id", "card__name")
    raw_id_fields = ("card", "match", "deck", "import_session")
    date_hierarchy = "created_at"

    def card_grp_id(self, obj):
        return obj.card.grp_id

    card_grp_id.short_description = "Card GRP ID"

    def card_name(self, obj):
        return obj.card.name

    card_name.short_description = "Card Name"

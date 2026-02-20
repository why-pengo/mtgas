"""
Django admin configuration for MTG Arena Statistics.
"""

from django.contrib import admin

from .models import Card, Deck, DeckCard, ImportSession, Match, UnknownCard


@admin.register(Card)
class CardAdmin(admin.ModelAdmin):
    list_display = ("grp_id", "name", "mana_cost", "type_line", "rarity")
    search_fields = ("name", "grp_id")
    list_filter = ("rarity", "set_code")


class DeckCardInline(admin.TabularInline):
    model = DeckCard
    extra = 0
    raw_id_fields = ("card",)


@admin.register(Deck)
class DeckAdmin(admin.ModelAdmin):
    list_display = ("name", "format", "created_at")
    search_fields = ("name", "deck_id")
    list_filter = ("format",)
    inlines = [DeckCardInline]


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

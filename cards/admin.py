from django.contrib import admin

from .models import CardImage


@admin.register(CardImage)
class CardImageAdmin(admin.ModelAdmin):
    list_display   = ("pk", "status", "scryfall_card", "match_distance", "uploaded_at")
    list_filter    = ("status",)
    readonly_fields = (
        "status",
        "scryfall_card",
        "match_distance",
        "error",
        "uploaded_at",
        "updated_at",
    )


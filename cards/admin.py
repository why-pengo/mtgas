from django.contrib import admin

from .models import CardImage, PaperCard


@admin.register(PaperCard)
class PaperCardAdmin(admin.ModelAdmin):
    list_display = ("name", "set_code", "rarity", "fetched_at")
    search_fields = ("name", "scryfall_id")
    readonly_fields = ("fetched_at",)


@admin.register(CardImage)
class CardImageAdmin(admin.ModelAdmin):
    list_display = ("pk", "status", "paper_card", "ocr_text", "uploaded_at")
    list_filter = ("status",)
    readonly_fields = (
        "status",
        "paper_card",
        "ocr_text",
        "error",
        "uploaded_at",
        "updated_at",
    )


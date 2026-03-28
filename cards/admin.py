from django.contrib import admin

from .models import PaperCard


@admin.register(PaperCard)
class PaperCardAdmin(admin.ModelAdmin):
    list_display = ("name", "set_code", "rarity", "fetched_at")
    search_fields = ("name", "scryfall_id")
    readonly_fields = ("fetched_at",)


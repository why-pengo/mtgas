from django.db import models


class PaperCard(models.Model):
    """A physical MTG card identified via Scryfall API lookup."""

    scryfall_id = models.CharField(max_length=50, unique=True)
    name        = models.CharField(max_length=255)
    type_line   = models.CharField(max_length=255, blank=True)
    oracle_text = models.TextField(blank=True)
    mana_cost   = models.CharField(max_length=100, blank=True)
    colors      = models.JSONField(default=list)
    set_code    = models.CharField(max_length=10, blank=True)
    rarity      = models.CharField(max_length=20, blank=True)
    image_uri   = models.URLField(max_length=500, blank=True)
    quantity    = models.PositiveIntegerField(default=1)
    fetched_at  = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "paper_cards"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @classmethod
    def upsert_from_scryfall(cls, data: dict) -> "PaperCard":
        """Create a PaperCard from a Scryfall API card object, or increment its quantity if it exists."""
        image_uri = ""
        if "image_uris" in data:
            image_uri = data["image_uris"].get("normal", "")
        elif "card_faces" in data and data["card_faces"]:
            image_uri = data["card_faces"][0].get("image_uris", {}).get("normal", "")

        metadata = {
            "name": data.get("name", ""),
            "type_line": data.get("type_line", ""),
            "oracle_text": data.get("oracle_text", ""),
            "mana_cost": data.get("mana_cost", ""),
            "colors": data.get("colors", []),
            "set_code": data.get("set", ""),
            "rarity": data.get("rarity", ""),
            "image_uri": image_uri,
        }

        paper_card, created = cls.objects.get_or_create(
            scryfall_id=data["id"],
            defaults={**metadata, "quantity": 1},
        )
        if not created:
            for field, value in metadata.items():
                setattr(paper_card, field, value)
            paper_card.quantity = models.F("quantity") + 1
            paper_card.save()
            paper_card.refresh_from_db()
        return paper_card


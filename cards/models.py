from django.db import models


class PaperCard(models.Model):
    """A physical MTG card identified via photo OCR and Scryfall API lookup."""

    scryfall_id = models.CharField(max_length=50, unique=True)
    name        = models.CharField(max_length=255)
    type_line   = models.CharField(max_length=255, blank=True)
    oracle_text = models.TextField(blank=True)
    mana_cost   = models.CharField(max_length=100, blank=True)
    colors      = models.JSONField(default=list)
    set_code    = models.CharField(max_length=10, blank=True)
    rarity      = models.CharField(max_length=20, blank=True)
    image_uri   = models.URLField(max_length=500, blank=True)
    fetched_at  = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "paper_cards"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @classmethod
    def upsert_from_scryfall(cls, data: dict) -> "PaperCard":
        """Create or update a PaperCard from a Scryfall API card object."""
        image_uri = ""
        if "image_uris" in data:
            image_uri = data["image_uris"].get("normal", "")
        elif "card_faces" in data and data["card_faces"]:
            image_uri = data["card_faces"][0].get("image_uris", {}).get("normal", "")

        paper_card, _ = cls.objects.update_or_create(
            scryfall_id=data["id"],
            defaults={
                "name": data.get("name", ""),
                "type_line": data.get("type_line", ""),
                "oracle_text": data.get("oracle_text", ""),
                "mana_cost": data.get("mana_cost", ""),
                "colors": data.get("colors", []),
                "set_code": data.get("set", ""),
                "rarity": data.get("rarity", ""),
                "image_uri": image_uri,
            },
        )
        return paper_card


class CardImage(models.Model):
    """An uploaded photo of a physical MTG card, with OCR matching result."""

    class Status(models.TextChoices):
        PENDING    = "pending",    "Pending"
        PROCESSING = "processing", "Processing"
        MATCHED    = "matched",    "Matched"
        UNMATCHED  = "unmatched",  "Unmatched"
        FAILED     = "failed",     "Failed"

    image      = models.ImageField(upload_to="cards/%Y/%m/")
    status     = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING
    )
    ocr_text   = models.CharField(
        max_length=512,
        blank=True,
        help_text="Card name text extracted from the image via OCR",
    )
    paper_card = models.ForeignKey(
        PaperCard,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="card_images",
    )
    error      = models.TextField(blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        name = self.paper_card.name if self.paper_card else "unmatched"
        return f"CardImage {self.pk} [{self.status}] {name}"


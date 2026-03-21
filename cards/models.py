from django.db import models


class CardImage(models.Model):
    class Status(models.TextChoices):
        PENDING    = "pending",    "Pending"
        PROCESSING = "processing", "Processing"
        MATCHED    = "matched",    "Matched"
        UNMATCHED  = "unmatched",  "Unmatched"
        FAILED     = "failed",     "Failed"

    image          = models.ImageField(upload_to="cards/%Y/%m/")
    status         = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING
    )
    scryfall_card  = models.ForeignKey(
        "stats.Card",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="card_images",
    )
    match_distance = models.IntegerField(
        null=True,
        blank=True,
        help_text="Hamming distance from phash match (lower = better)",
    )
    error          = models.TextField(blank=True)
    uploaded_at    = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        name = self.scryfall_card.name if self.scryfall_card else "unmatched"
        return f"CardImage {self.pk} [{self.status}] {name}"


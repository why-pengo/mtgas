from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("stats", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="card",
            name="phash",
            field=models.CharField(
                max_length=64,
                null=True,
                blank=True,
                help_text="Perceptual hash of card image for visual matching",
            ),
        ),
        migrations.AddIndex(
            model_name="card",
            index=models.Index(fields=["phash"], name="scryfall_phash_idx"),
        ),
    ]

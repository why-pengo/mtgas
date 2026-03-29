# Backup & Restore

MTG Arena Stats includes a web UI for backing up and restoring the full database.
Backups use Django's `dumpdata` format — a JSON file containing all app data —
which is portable across both SQLite and PostgreSQL and is compatible with Django
migrations (safe to restore after a schema upgrade when combined with a migration run).

## Accessing the UI

Navigate to **`/backup/`** (Backup link in the top nav).

The page has two sections:

- **Download Backup** — exports all data as a JSON file to your browser
- **Restore from Backup** — uploads a previously downloaded backup and replaces all current data

---

## Downloading a Backup

1. Go to `/backup/`
2. Click **Download Backup**

The file is saved to your browser's default download folder with the name:

```
mtgas_backup_YYYYMMDD_HHMMSS.json
```

The backup includes all records from the `stats` and `cards` apps:

| App | Models included |
|-----|----------------|
| `stats` | Match, Deck, DeckSnapshot, DeckCard, Card, CardToken, CardTokenRef, GameAction, LifeChange, ZoneTransfer, ImportSession, UnknownCard |
| `cards` | PaperCard |

Django internal tables (`auth`, `sessions`, `admin`, `contenttypes`) are **not** included.

---

## Restoring a Backup

> ⚠️ **Restore permanently deletes all existing data** before loading the backup.
> Download a fresh backup first if you want to preserve your current data.

1. Go to `/backup/`
2. Under **Restore from Backup**, select a `.json` backup file
3. Check the confirmation box
4. Click **Restore Backup**

The restore process:
1. Validates the uploaded file is well-formed JSON
2. Deletes all existing app data in foreign-key-safe order (within a transaction)
3. Loads the backup with `loaddata`
4. Redirects back to `/backup/` with a success or error message

If the restore fails for any reason the transaction is rolled back and your existing
data is left intact.

---

## Recommended Workflow: Migrating to a New Schema

When the database schema changes (new migration), follow this order:

```bash
# 1. Download a backup via the UI at /backup/
#    → saves mtgas_backup_YYYYMMDD_HHMMSS.json

# 2. Apply the new migrations
python manage.py migrate

# 3. Restore the backup via the UI at /backup/
#    The backup JSON uses natural keys so Django maps records to the
#    updated schema automatically.
```

---

## Backup Format

Backups are produced by `django.core.management.call_command('dumpdata', ...)` with
`natural_foreign=True` and `natural_primary=True`. Each record is a JSON object:

```json
[
  {
    "model": "stats.deck",
    "fields": {
      "deck_id": "abc123",
      "name": "Red Deck Wins",
      "format": "Standard",
      ...
    }
  },
  ...
]
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Invalid file type" error | Ensure the file has a `.json` extension |
| "not valid JSON" error | The file may be corrupted; re-download a fresh backup |
| Restore fails with integrity error | The backup may be from an incompatible schema version — run `python manage.py migrate` first, then retry |
| Download is empty `[]` | The database has no data yet; this is expected on a fresh install |

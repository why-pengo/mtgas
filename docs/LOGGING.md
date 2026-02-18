# Import Logging Configuration

## Overview

The import system now includes comprehensive logging to help debug issues and track progress.

## Logger Information

- **Logger Name**: `stats.views`
- **Logging Levels**:
  - `DEBUG`: Detailed per-operation logging (card lookups, bulk creates, etc.)
  - `INFO`: Progress and summary information (match imports, session status)
  - `WARNING`: Non-fatal issues (missing cards, skipped matches)
  - `ERROR`: Failures with full stack traces

## Quick Start

### View Logs in Development

Run the Django development server to see logs in console:

```bash
python manage.py runserver
```

Then visit `/import/` and upload a log file. You'll see detailed logs in the terminal.

### Example Log Output

```
21:49:12 [INFO ] stats.views: Starting import from uploaded file: Player.log (2580432 bytes)
21:49:12 [INFO ] stats.views: Created import session: 42
21:49:12 [INFO ] stats.views: Card data ready
21:49:12 [INFO ] stats.views: Found 150 existing matches in database
21:49:12 [INFO ] stats.views: Parsing log file...
21:49:12 [INFO ] stats.views: Importing match: c7173af6-...
21:49:12 [DEBUG] stats.views: [c7173af6] Starting import
21:49:12 [DEBUG] stats.views: [c7173af6] Processing deck: d42abc-xyz
21:49:12 [DEBUG] stats.views: [c7173af6] Found 60 unique cards
21:49:12 [DEBUG] stats.views: [c7173af6] All 60 cards already exist in database
21:49:12 [DEBUG] stats.views: [c7173af6] Creating match record
21:49:12 [DEBUG] stats.views: [c7173af6] Match created with ID: 305
21:49:12 [DEBUG] stats.views: [c7173af6] Importing game actions
21:49:12 [DEBUG] stats.views: [c7173af6] Created 142 game actions
21:49:12 [DEBUG] stats.views: [c7173af6] Importing life changes
21:49:12 [DEBUG] stats.views: [c7173af6] Created 28 life changes
21:49:12 [DEBUG] stats.views: [c7173af6] Importing zone transfers
21:49:12 [DEBUG] stats.views: [c7173af6] Created 237 zone transfers
21:49:12 [INFO ] stats.views: [c7173af6] Import complete
21:49:12 [INFO ] stats.views: Import complete: 5 imported, 145 skipped, 0 errors
```

## Advanced Configuration

### Option 1: File Logging

Add to `mtgas_project/settings.py`:

```python
import os

# Create logs directory if needed
LOGS_DIR = BASE_DIR / 'logs'
LOGS_DIR.mkdir(exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {name} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(LOGS_DIR / 'import.log'),
            'maxBytes': 10485760,  # 10MB
            'backupCount': 5,
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'stats.views': {
            'handlers': ['console', 'file'],
            'level': 'INFO',  # Change to 'DEBUG' for more detail
            'propagate': False,
        },
    },
}
```

Then create the logs directory:

```bash
mkdir -p logs
echo "logs/*.log" >> .gitignore
```

### Option 2: Separate Debug Log

For detailed debugging without cluttering the main log:

```python
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'detailed': {
            'format': '{levelname} {asctime} {name}:{lineno} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'level': 'INFO',
        },
        'debug_file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': 'logs/import_debug.log',
            'maxBytes': 20971520,  # 20MB
            'backupCount': 3,
            'formatter': 'detailed',
            'level': 'DEBUG',
        },
    },
    'loggers': {
        'stats.views': {
            'handlers': ['console', 'debug_file'],
            'level': 'DEBUG',
            'propagate': False,
        },
    },
}
```

### Option 3: Production Logging (JSON)

For production deployments with log aggregation:

```python
import json
import logging

class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            'timestamp': self.formatTime(record),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno,
        }
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)
        return json.dumps(log_data)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'json': {
            '()': JsonFormatter,
        },
    },
    'handlers': {
        'json_file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': 'logs/import.json',
            'maxBytes': 52428800,  # 50MB
            'backupCount': 10,
            'formatter': 'json',
        },
    },
    'loggers': {
        'stats.views': {
            'handlers': ['json_file'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}
```

## Debugging Import Errors

### Common Error Patterns

#### 1. Card Not Found
```
[ERROR] stats.views: Failed to import match d8234bfe: Card matching query does not exist
Card.DoesNotExist: Card matching query does not exist. grp_id=99999
```

**Solution**: Download Scryfall card data via `/card-data/` page

#### 2. Field Name Mismatch (Fixed)
```
[ERROR] stats.views: Failed to import match c7173af6: LifeChange() got unexpected keyword arguments: 'change'
```

**Solution**: Already fixed in this update (changed `change` to `change_amount`)

#### 3. Missing Required Field
```
[DEBUG] stats.views: Skipping life change with missing data: seat_id=None, life_total=20
```

**Not an error**: The system gracefully skips incomplete data

### Viewing Error Details

1. **Web UI**: First 3 errors shown as messages after import
2. **Import Session**: Visit `/import-history/` to see stored errors
3. **Console/File**: Full stack traces with context data
4. **Match ID**: All log messages prefixed with `[match_id]` for tracing

## Testing the Logging

### Test Basic Logging

```bash
python -c "
import logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('stats.views')
logger.info('Test message')
logger.debug('Debug message')
logger.error('Error message')
"
```

### Test with Development Server

1. Start server: `python manage.py runserver`
2. Visit: `http://localhost:8000/import/`
3. Upload a log file
4. Watch console for detailed logs

## Performance Considerations

- **DEBUG level**: Generates significant log output (~100 messages per match)
- **INFO level**: Moderate output (~10 messages per match)
- **Recommended for production**: INFO or WARNING

## Integration with Monitoring Services

### Sentry

```python
import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration

sentry_sdk.init(
    dsn="your-sentry-dsn",
    integrations=[DjangoIntegration()],
    traces_sample_rate=1.0,
)
```

### CloudWatch (AWS)

```python
LOGGING = {
    'handlers': {
        'cloudwatch': {
            'class': 'watchtower.CloudWatchLogHandler',
            'log_group': 'mtgas-import',
            'stream_name': 'stats-views',
        },
    },
    'loggers': {
        'stats.views': {
            'handlers': ['cloudwatch'],
            'level': 'INFO',
        },
    },
}
```

## Summary

- ✅ Comprehensive logging added to entire import process
- ✅ Match ID prefixes for easy tracing
- ✅ DEBUG/INFO/ERROR levels for different verbosity
- ✅ Full exception details with stack traces
- ✅ Ready for development, production, and monitoring

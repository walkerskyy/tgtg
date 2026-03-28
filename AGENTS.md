# TGTG Scanner - Agent Guidelines

Python backend service for monitoring Too Good To Go magic bags and sending notifications. Includes a Kivy-based Android mobile app.

## Project Structure

```
tgtg_scanner/          # Python backend
├── __main__.py        # CLI entry point
├── errors.py          # Custom exceptions
├── scanner.py         # Main polling loop
├── tgtg/tgtg_client.py # API client with DataDome handling
├── models/            # Config, Item, Favorites, Reservations, Location, Cron, Metrics
└── notifiers/         # Notification plugins (telegram, discord, etc.)

tests/                 # Pytest test suite

tgtg_mobile/           # Kivy Android app
├── main.py            # ScreenManager entry point
├── screens/           # login, favorites, item_detail, settings
├── services/          # token_storage, android_service
├── tgtg_api/client.py # TGTG API client
└── buildozer.spec     # Build configuration
```

## Commands (Python Backend)

```bash
# Development
make install          # Install deps + pre-commit hooks
poetry install        # Dependencies only

# Running
make server           # Start TGTG dev API proxy
make start            # Run scanner with debug + proxy
poetry run scanner    # Run scanner directly

# Testing
make test             # Run tests (excludes live API tests)
pytest -v             # Verbose
pytest -m "not tgtg_api"  # Skip live API tests
pytest tests/test_item.py # Single file
pytest tests/test_item.py::test_item_name # Single test
pytest -k "pattern"   # Match test names

# Linting & Type Checking
make lint             # Run pre-commit (ruff, mypy)
poetry run ruff check .       # Linter
poetry run ruff format .      # Formatter
poetry run mypy tgtg_scanner # Type check

# Building
make executable       # Build pyinstaller binary
make images           # Build Docker images
tox                   # Run test environments
```

## Commands (Mobile App)

```bash
cd tgtg_mobile

# Development
pip install -r requirements.txt
python main.py        # Run on desktop

# Building APK
pip install buildozer
buildozer android debug
buildozer android release
buildozer android debug deploy install  # Build + install to device
```

## Code Style (Python)

- **Line length**: 130 characters
- **Formatter**: Ruff with ruff-format
- **Linter**: E, F, B, I, UP (errors, pyflakes, bugs, imports, pyupgrade)
- **Type checking**: mypy with types-requests

### Imports
```python
# Standard library first, then third-party, then local
import logging
import sys
from datetime import datetime
from typing import Any

import requests
from requests.adapters import HTTPAdapter

from tgtg_scanner.errors import TgtgAPIError
from tgtg_scanner.models import Config, Item
```

### Type Annotations
- Use modern hints (Python 3.10+): `str | None` not `Optional[str]`
- Property return types required
```python
@property
def name(self) -> str:
    return self.__class__.__name__
```

### Naming
- Classes: `PascalCase` (TgtgClient, Item)
- Functions: `snake_case` (get_items, set_favorite)
- Constants: `SCREAMING_SNAKE_CASE` (BASE_URL)
- Private: prefix underscore (_create_session)

### Docstrings
- Google-style for public classes/methods
```python
def get_credentials(self) -> dict:
    """Returns current tgtg api credentials.

    Returns:
        dict: Dictionary containing access token, refresh token and user id
    """
```

### Error Handling
- Custom exceptions in `errors.py` extending `Error` base class
```python
class TgtgAPIError(Error):
    pass

class ConfigurationError(Error):
    pass

raise TgtgAPIError(response.status_code, response.content)
```

### Logging
```python
log = logging.getLogger("tgtg")
log.debug("Waiting %s seconds.", wait)
log.info("Logged in!")
log.warning("Using custom tgtg base url: %s", base_url)
log.error("Failed sending %s: %s", self.name, exc)
```

## Code Style (Mobile/Kivy)

- **Line length**: 130 characters
- **Threading**: `Thread(target=..., daemon=True)` for API calls
- **UI updates**: `@mainthread` decorator or `Clock.schedule_once`
- **Kivy properties**: `StringProperty`, `BooleanProperty`, `NumericProperty`, `ObjectProperty`, `ListProperty`
- **Navigation**: `self.manager.current = "screen_name"`

## Testing Conventions

- Fixtures in `tests/conftest.py`
- Descriptive names: `test_item_pickupdate_24h_format`
- Skip live API tests: `@pytest.mark.tgtg_api`
```python
def test_item_pickupdate_24h_format(tgtg_item: dict):
    """Test pickup date formatting with 24-hour time format."""
    item = Item(tgtg_item, time_format="24h")
    assert ":" in item.pickupdate
```

## Key Architecture

- **TgtgClient**: API communication including DataDome cookies
- **Scanner**: Polling loop monitoring items, triggering notifications
- **Notifiers**: Plugin system for multiple channels
- **Token persistence**: Tokens saved to file to avoid repeated logins

## Mobile-Specific Notes

- Settings: `from kivy.storage.jsonstore import JsonStore`
- Token storage: `tokens.json` in app directory
- Background scan: polls every 60 seconds (configurable)
- Android 13+: requires notification permission
- Sync changes to `.buildozer/android/app/` after modifying source

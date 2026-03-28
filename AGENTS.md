# TGTG Scanner - Agent Guidelines

This is a Python backend service for monitoring Too Good To Go magic bags and sending notifications.

## Project Structure

```
tgtg_scanner/
├── __init__.py           # Package init
├── __main__.py           # CLI entry point
├── errors.py             # Custom exception classes
├── scanner.py            # Main scanner loop
├── tgtg/
│   └── tgtg_client.py    # TGTG API client with DataDome handling
├── models/
│   ├── config.py         # Configuration management
│   ├── item.py           # Item data model
│   ├── favorites.py      # Favorites handling
│   ├── reservations.py   # Order management
│   ├── location.py      # Distance/time calculations
│   ├── cron.py           # Schedule handling
│   └── metrics.py        # Prometheus metrics
├── notifiers/            # Notification plugins (base, telegram, discord, etc.)
tests/
├── conftest.py           # Pytest fixtures
└── test_*.py             # Unit tests
```

## Commands

### Development Setup
```bash
make install          # Install deps + pre-commit hooks
poetry install        # Install dependencies only
```

### Running the Scanner
```bash
make server           # Start TGTG dev API proxy
make start            # Run scanner with debug + proxy
poetry run scanner    # Run scanner directly
```

### Testing
```bash
make test             # Run all tests (excludes tgtg_api marker)
pytest                # Run all tests
pytest -v             # Verbose output
pytest -m "not tgtg_api"  # Skip live API tests
pytest tests/test_item.py  # Run single test file
pytest tests/test_item.py::test_item  # Run single test
pytest -k "test_item"      # Run tests matching pattern
```

### Linting & Type Checking
```bash
make lint             # Run pre-commit hooks (ruff, mypy, etc.)
poetry run pre-commit run -a   # Manual pre-commit run
poetry run ruff check .         # Ruff linter only
poetry run ruff format .        # Ruff formatter only
poetry run mypy tgtg_scanner    # Type checking
```

### Building
```bash
make executable       # Build pyinstaller binary
make images           # Build Docker images
```

## Code Style

### Formatting & Linting
- **Line length**: 130 characters (configured in pyproject.toml)
- **Formatter**: Ruff with ruff-format
- **Linter rules**: E, F, B, I, UP (errors, pyflakes, bugs, imports, pyupgrade)
- **Type checking**: mypy with types-requests

### Imports
- Standard library first, then third-party, then local
- Use absolute imports (`from tgtg_scanner.models import Item`)
- Sort alphabetically within groups

```python
# Standard library
import logging
import sys
from datetime import datetime
from typing import Any

# Third-party
import requests
from requests.adapters import HTTPAdapter

# Local
from tgtg_scanner.errors import TgtgAPIError
from tgtg_scanner.models import Config, Item
```

### Type Annotations
- Use modern type hints (Python 3.10+): `str | None` instead of `Optional[str]`
- Use `from typing import NoReturn` for functions that never return
- Property types: explicit return types required

```python
def __init__(
    self,
    email: str | None = None,
    access_token: str | None = None,
) -> None:
    ...

@property
def name(self) -> str:
    return self.__class__.__name__

def _run(self) -> None:
    ...
```

### Naming Conventions
- **Classes**: PascalCase (`TgtgClient`, `Item`, `Notifiers`)
- **Functions/methods**: snake_case (`get_items`, `set_favorite`)
- **Constants**: SCREAMING_SNAKE_CASE (`BASE_URL`, `DEFAULT_TIMEOUT`)
- **Private methods**: prefix with underscore (`_create_session`, `_post`)
- **Module-private**: single underscore prefix (`_internal_helper`)

### Docstrings
- Use docstrings for public classes and methods
- Google-style with Args/Returns sections

```python
def get_credentials(self) -> dict:
    """Returns current tgtg api credentials.

    Returns:
        dict: Dictionary containing access token, refresh token and user id
    """
```

### Error Handling
- Custom exceptions in `errors.py` extending `Error` base class
- Use specific exception types for different error categories
- Log errors with appropriate level (debug, warning, error)
- Configuration errors include helpful messages

```python
class TgtgAPIError(Error):
    pass

class ConfigurationError(Error):
    pass

class MaskConfigurationError(ConfigurationError):
    def __init__(self, variable):
        self.message = f"Unrecognized variable {variable}..."
        super().__init__(self.message)
```

### Logging
- Use module-level logger pattern
- Log levels: debug (details), info (important events), warning (recoverable issues), error (failures)

```python
log = logging.getLogger("tgtg")

log.debug("Waiting %s seconds.", wait)
log.info("Logged in!")
log.warning("Using custom tgtg base url: %s", base_url)
log.error("Failed sending %s: %s", self.name, exc)
```

### Exception Patterns
```python
# Raising custom errors
raise TgtgAPIError(response.status_code, response.content)
raise TgtgConfigurationError("You must provide at least email or tokens")

# Catching and re-raising
try:
    response = self.session.post(url, **kwargs)
except requests.RequestException as e:
    log.error("Request failed: %s", e)
    raise TgtgAPIError from e
```

### Threading
- Use `threading.Thread` with daemon threads for background tasks
- Queue-based communication between threads

```python
self.thread = threading.Thread(target=self._run)
self.queue: Queue[Item | Reservation | None] = Queue()
```

## Testing Conventions

- Fixtures defined in `tests/conftest.py`
- Use descriptive test names: `test_item_pickupdate_24h_format`
- Test public interfaces, not internals
- Skip live API tests with `@pytest.mark.tgtg_api`
- Use monkeypatch for mocking
- Use fixtures from conftest for test data

```python
def test_item_pickupdate_24h_format(tgtg_item: dict):
    """Test pickup date formatting with 24-hour time format."""
    item = Item(tgtg_item, time_format="24h")
    pickupdate = item.pickupdate
    assert ":" in pickupdate
```

## Key Architecture Notes

- **TgtgClient**: Handles all TGTG API communication including DataDome cookie management
- **Scanner**: Main polling loop that monitors items and triggers notifications
- **Notifiers**: Plugin system for multiple notification channels
- **Models**: Data classes for items, config, favorites, reservations, location, metrics
- **Token persistence**: Tokens saved to file to avoid repeated logins

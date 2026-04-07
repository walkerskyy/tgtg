import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("tgtg_mobile")

TOKEN_FILE = Path(__file__).parent.parent.parent / "tokens.json"


class TokenStorage:
    def __init__(self):
        self.token_file = TOKEN_FILE
        self._tokens: dict[str, Any] | None = None

    def load(self) -> dict[str, Any]:
        if not self.token_file.exists():
            log.debug("No token file found")
            return {}
        try:
            with open(self.token_file, "r") as f:
                self._tokens = json.load(f)
            log.debug("Tokens loaded successfully")
            return self._tokens
        except (json.JSONDecodeError, IOError) as e:
            log.error(f"Failed to load tokens: {e}")
            return {}

    def save(
        self,
        access_token: str | None = None,
        refresh_token: str | None = None,
        datadome_cookie: str | None = None,
    ) -> None:
        tokens = self.load()
        if access_token:
            tokens["access_token"] = access_token
        if refresh_token:
            tokens["refresh_token"] = refresh_token
        if datadome_cookie:
            tokens["datadome_cookie"] = datadome_cookie
        try:
            with open(self.token_file, "w") as f:
                json.dump(tokens, f, indent=2)
            log.debug("Tokens saved successfully")
        except IOError as e:
            log.error(f"Failed to save tokens: {e}")

    def clear(self) -> None:
        if self.token_file.exists():
            self.token_file.unlink()
            log.debug("Tokens cleared")

    def get(self, key: str, default: Any = None) -> Any:
        tokens = self.load()
        return tokens.get(key, default)

# Captcha Paste Prompt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in interactive terminal prompt that lets the user paste a fresh `datadome` cookie when a DataDome captcha challenge is pending, making captcha recovery work on mobile where browser-cookie auto-read cannot.

**Architecture:** New `prompt_datadome()` helper in `captcha.py` (TTY check, one-line read, normalize via existing `normalize_datadome_value`, never raises). `_recover_captcha` tries the prompt (when `CaptchaPrompt` config is enabled) before the existing browser/inbox candidates; the downstream probe/apply/persist pipeline is untouched.

**Tech Stack:** Python 3.12, pytest + pytest-mock, ruff + mypy via pre-commit. Spec: `docs/superpowers/specs/2026-10-16-captcha-paste-prompt-design.md`.

**Conventions:** Run all commands from the repo root (`/home/tosh/Documents/tgtg`) with `poetry run`. TDD is mandatory: write the failing test, watch it fail, implement, watch it pass, commit.

---

### Task 1: CaptchaPrompt configuration flag

**Files:**
- Modify: `tgtg_scanner/models/config.py` (TgtgConfig fields ~line 492, `_read_ini` ~line 505, `_read_env` ~line 519)
- Test: `tests/test_config.py` (append after `test_captcha_config_ini`, ~line 182)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`, matching the existing captcha-config test style in that file:

```python
def test_captcha_prompt_defaults(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TGTG_CAPTCHA_PROMPT", raising=False)
    config = Config(disable_tests=True)
    assert config.tgtg.captcha_prompt is False


def test_captcha_prompt_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TGTG_CAPTCHA_PROMPT", "true")
    config = Config(disable_tests=True)
    assert config.tgtg.captcha_prompt is True


def test_captcha_prompt_ini(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TGTG_CAPTCHA_PROMPT", raising=False)
    ini = tmp_path / "config.ini"
    ini.write_text("[TGTG]\nCaptchaPrompt = true\n")
    config = Config(str(ini), disable_tests=True)
    assert config.tgtg.captcha_prompt is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_config.py -k captcha_prompt -v`
Expected: FAIL — `AttributeError: 'TgtgConfig' object has no attribute 'captcha_prompt'`

- [ ] **Step 3: Implement the config flag**

In `tgtg_scanner/models/config.py`, `TgtgConfig`: add the field directly after `captcha_cooldown: int = 600`:

```python
    captcha_prompt: bool = False
```

In `TgtgConfig._read_ini`, add directly after the `CaptchaCooldown` line:

```python
        self._ini_get_boolean(parser, "TGTG", "CaptchaPrompt", "captcha_prompt")
```

In `TgtgConfig._read_env`, add directly after the `TGTG_CAPTCHA_COOLDOWN` line:

```python
        self._env_get_boolean("TGTG_CAPTCHA_PROMPT", "captcha_prompt")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_config.py -k captcha_prompt -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add tgtg_scanner/models/config.py tests/test_config.py
git commit -m "feat: add CaptchaPrompt configuration flag"
```

---

### Task 2: Interactive `prompt_datadome` helper

**Files:**
- Modify: `tgtg_scanner/captcha.py` (add `import sys` to imports ~line 5; append `prompt_datadome` after `normalize_datadome_value` ~line 55)
- Test: `tests/test_captcha.py` (append at end)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_captcha.py`. Add `prompt_datadome` to the existing `from tgtg_scanner.captcha import (...)` block and `import io` next to the existing `import json`. Then append:

```python
class _FakeStdin:
    def __init__(self, line: str = "", tty: bool = True, raise_eof: bool = False):
        self._line = line
        self._tty = tty
        self._raise_eof = raise_eof

    def isatty(self) -> bool:
        return self._tty

    def readline(self) -> str:
        if self._raise_eof:
            raise EOFError
        return self._line


def _prompt(monkeypatch, line: str = "", tty: bool = True, raise_eof: bool = False):
    import sys

    from tgtg_scanner.captcha import prompt_datadome

    monkeypatch.setattr(sys, "stdin", _FakeStdin(line, tty, raise_eof))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    return prompt_datadome(CaptchaChallenge(url=CAPTCHA_URL, first_seen=datetime.now(timezone.utc)))


def test_prompt_datadome_bare_token(monkeypatch):
    assert _prompt(monkeypatch, line="abc123token\n") == "abc123token"


def test_prompt_datadome_cookie_header_fragment(monkeypatch):
    assert _prompt(monkeypatch, line="datadome=wrappedtoken; Path=/; Secure\n") == "wrappedtoken"


def test_prompt_datadome_empty_line(monkeypatch):
    assert _prompt(monkeypatch, line="\n") is None


def test_prompt_datadome_eof(monkeypatch):
    assert _prompt(monkeypatch, raise_eof=True) is None


def test_prompt_datadome_non_tty(monkeypatch):
    assert _prompt(monkeypatch, line="abc123token\n", tty=False) is None


def test_prompt_datadome_rejects_control_chars(monkeypatch):
    assert _prompt(monkeypatch, line="bad\x00cookie\n") is None


def test_prompt_datadome_prints_challenge_url(monkeypatch):
    import sys

    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", _FakeStdin("abc\n"))
    monkeypatch.setattr(sys, "stdout", stdout)
    from tgtg_scanner.captcha import prompt_datadome

    prompt_datadome(CaptchaChallenge(url=CAPTCHA_URL, first_seen=datetime.now(timezone.utc)))
    assert CAPTCHA_URL in stdout.getvalue()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_captcha.py -k prompt_datadome -v`
Expected: FAIL — `ImportError: cannot import name 'prompt_datadome'`

- [ ] **Step 3: Implement the helper**

In `tgtg_scanner/captcha.py`: add `import sys` to the stdlib imports (after `import re`). Append after `normalize_datadome_value`:

```python
def prompt_datadome(challenge: CaptchaChallenge | None = None) -> str | None:
    """Interactively ask the user to paste a fresh datadome cookie value.

    Returns the normalized token, or None when prompting is not possible
    (non-TTY stdin, empty/invalid input, stdin errors). Never raises.
    """
    try:
        if not sys.stdin.isatty():
            if challenge is not None:
                log.debug("Captcha prompt skipped - stdin is not a TTY (solve: %s)", challenge.url)
            return None
        url = f":\n  {challenge.url}" if challenge is not None else ""
        sys.stdout.write(
            f"DataDome captcha challenge - solve it in your browser{url}\n"
            "Then copy the 'datadome' cookie value (DevTools -> Application -> Cookies) and paste it here:\n> "
        )
        sys.stdout.flush()
        try:
            raw = sys.stdin.readline()
        except (EOFError, KeyboardInterrupt, OSError):
            sys.stdout.write("\n")
            return None
        value = normalize_datadome_value(raw)
        if value is None:
            if raw.strip():
                log.warning("Pasted value is not a plausible datadome cookie - ignoring")
            return None
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F or ch == "�" for ch in value):
            log.warning("Pasted value does not contain a plausible datadome cookie - ignoring")
            return None
        return value
    except Exception as exc:
        log.warning("Captcha prompt failed: %s", exc)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_captcha.py -v`
Expected: all captcha tests pass (existing normalize tests included)

- [ ] **Step 5: Commit**

```bash
git add tgtg_scanner/captcha.py tests/test_captcha.py
git commit -m "feat: add interactive datadome cookie paste prompt"
```

---

### Task 3: Use the prompt first in captcha recovery

**Files:**
- Modify: `tgtg_scanner/scanner.py:11` (import) and `tgtg_scanner/scanner.py:_recover_captcha` (~line 268, candidate selection block)
- Modify: `config.sample.ini` (`[TGTG]` captcha block, after `CaptchaBrowser` rows ~line 68)
- Test: `tests/test_scanner.py` (append after the last `test_recover_captcha_*` test)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scanner.py` (reuse the existing `_pending_captcha()` helper already defined in that file):

```python
def test_recover_captcha_prompt_applies_pasted_cookie(scanner: Scanner, tmp_path: Path, mocker: MockerFixture):
    scanner.config.token_path = str(tmp_path)
    scanner.config.tgtg.captcha_prompt = True
    scanner.tgtg_client.captcha = _pending_captcha()
    mock_prompt = mocker.patch("tgtg_scanner.scanner.prompt_datadome", return_value="pastedcookie")
    mock_browser = mocker.patch("tgtg_scanner.scanner.read_browser_datadome")
    mock_favorites = mocker.patch.object(scanner.tgtg_client, "get_favorites", return_value=[])
    mock_save = mocker.patch.object(scanner, "_save_tokens")

    scanner._recover_captcha()

    mock_prompt.assert_called_once()
    mock_browser.assert_not_called()
    assert scanner.tgtg_client.captcha is None
    assert scanner.tgtg_client.cookie == "datadome=pastedcookie"
    mock_favorites.assert_called_once()
    mock_save.assert_called_once()


def test_recover_captcha_prompt_none_falls_back_to_browser(scanner: Scanner, tmp_path: Path, mocker: MockerFixture):
    scanner.config.token_path = str(tmp_path)
    scanner.config.tgtg.captcha_prompt = True
    scanner.tgtg_client.captcha = _pending_captcha()
    mocker.patch("tgtg_scanner.scanner.prompt_datadome", return_value=None)
    mock_browser = mocker.patch("tgtg_scanner.scanner.read_browser_datadome", return_value="browsercookie")
    mocker.patch.object(scanner.tgtg_client, "get_favorites", return_value=[])
    mocker.patch.object(scanner, "_save_tokens")

    scanner._recover_captcha()

    mock_browser.assert_called_once()
    assert scanner.tgtg_client.captcha is None
    assert scanner.tgtg_client.cookie == "datadome=browsercookie"


def test_recover_captcha_prompt_disabled_skips_prompt(scanner: Scanner, tmp_path: Path, mocker: MockerFixture):
    scanner.config.token_path = str(tmp_path)
    scanner.config.tgtg.captcha_prompt = False
    scanner.tgtg_client.captcha = _pending_captcha()
    mock_prompt = mocker.patch("tgtg_scanner.scanner.prompt_datadome")
    mocker.patch("tgtg_scanner.scanner.read_browser_datadome", return_value="browsercookie")
    mocker.patch.object(scanner.tgtg_client, "get_favorites", return_value=[])
    mocker.patch.object(scanner, "_save_tokens")

    scanner._recover_captcha()

    mock_prompt.assert_not_called()
    assert scanner.tgtg_client.cookie == "datadome=browsercookie"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_scanner.py -k "recover_captcha_prompt" -v`
Expected: FAIL — `AttributeError: <MagicMock ...> has no attribute 'prompt_datadome'` (module attribute missing) or equivalent; the first test must fail because the prompt is never consulted.

- [ ] **Step 3: Implement the wiring**

In `tgtg_scanner/scanner.py`, change the import at line 11 from:

```python
from tgtg_scanner.captcha import read_browser_datadome, read_inbox_datadome
```

to:

```python
from tgtg_scanner.captcha import prompt_datadome, read_browser_datadome, read_inbox_datadome
```

In `_recover_captcha`, replace the candidate-selection block:

```python
            candidate: str | None = None
            from_inbox = False
            browser = self.config.tgtg.captcha_browser
            if browser and browser.lower() != "off":
                candidate = read_browser_datadome(browser, challenge=challenge)
```

with:

```python
            candidate: str | None = None
            from_inbox = False
            if self.config.tgtg.captcha_prompt:
                candidate = prompt_datadome(challenge)
            if candidate is None:
                browser = self.config.tgtg.captcha_browser
                if browser and browser.lower() != "off":
                    candidate = read_browser_datadome(browser, challenge=challenge)
```

In `config.sample.ini`, in the `[TGTG]` section, after the `CaptchaCooldown = 600` line and its comment, add:

```ini
## Prompt on the terminal to paste the datadome cookie after solving a captcha.
## Recommended on mobile / where CaptchaBrowser auto-read is unavailable.
; CaptchaPrompt = true
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_scanner.py -k "recover_captcha" -v`
Expected: all recover_captcha tests pass (13 existing + 3 new)

- [ ] **Step 5: Commit**

```bash
git add tgtg_scanner/scanner.py config.sample.ini tests/test_scanner.py
git commit -m "feat: use paste prompt first in captcha recovery"
```

---

### Task 4: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `poetry run pytest -m "not tgtg_api" -q`
Expected: all tests pass (308 + 10 new = ~318), exit code 0

- [ ] **Step 2: Run lint/typecheck**

Run: `poetry run pre-commit run -a`
Expected: every hook `Passed`, exit code 0

- [ ] **Step 3: Fix any failures and re-run until green, then report completion**

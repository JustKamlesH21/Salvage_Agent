"""`.env.example` and `api/config.py` must not silently drift apart.

api/config.py claims every variable the API reads is declared as a Settings
field, with two named exceptions (frontend-only, and an unused parts-builder
pair). This test derives both sides from the actual files rather than trusting
the docstring, so a new .env.example entry with no Settings field — or a
Settings field with no .env.example entry — fails loudly instead of rotting.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Documented in api/config.py's module docstring. Keep the two lists in sync;
# a change to one without the other is exactly the drift this test exists to
# catch.
DOCUMENTED_EXCEPTIONS = frozenset({"MONGODB_USERNAME", "MONGODB_PASSWORD", "VITE_API_BASE_URL"})


def env_example_keys() -> set[str]:
    keys = set()
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Z_][A-Z0-9_]*)=", line)
        if match:
            keys.add(match.group(1))
    return keys


def settings_field_keys() -> set[str]:
    keys = set()
    for line in (ROOT / "api" / "config.py").read_text(encoding="utf-8").splitlines():
        match = re.match(r"^    ([a-z_][a-z0-9_]*):\s", line)
        if match:
            keys.add(match.group(1).upper())
    return keys


class TestParity:
    def test_every_env_example_key_is_declared_or_documented_as_exempt(self) -> None:
        undeclared = env_example_keys() - settings_field_keys() - DOCUMENTED_EXCEPTIONS
        assert undeclared == set(), (
            f"{sorted(undeclared)} are in .env.example but neither a Settings field nor a "
            f"documented exception in api/config.py's docstring. Add the field, or add the "
            f"name to DOCUMENTED_EXCEPTIONS here and to the docstring, with a reason."
        )

    def test_every_settings_field_is_named_in_env_example(self) -> None:
        undocumented = settings_field_keys() - env_example_keys()
        assert undocumented == set(), (
            f"{sorted(undocumented)} are Settings fields with no .env.example entry. "
            f"NFR-06 requires every variable the code reads to be named there."
        )

    def test_the_exception_list_is_not_stale(self) -> None:
        # If an "exempt" name stops appearing in .env.example, the exception is
        # dead weight and should be removed from both places.
        stale = DOCUMENTED_EXCEPTIONS - env_example_keys()
        assert stale == set(), (
            f"{sorted(stale)} are documented exceptions no longer in .env.example"
        )

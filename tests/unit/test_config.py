"""Unit tests for the Config model and load_config loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from inso_dumper._result import Err, Ok
from inso_dumper.config import Config, load_config


def test_default_config_matches_spec() -> None:
    cfg = Config()
    assert str(cfg.base_url).rstrip("/") == "https://app.inso.pl"
    assert cfg.rate_limit_per_second == 3.0
    assert cfg.request_timeout_seconds == 30.0


def test_config_rejects_unknown_fields() -> None:
    from typing import Any, cast

    with pytest.raises((TypeError, ValueError)):
        # type checker knows "extra" is not a field; runtime also rejects.
        cast(Any, Config)(base_url="https://x", rate_limit_per_second=1.0, foobar=42)  # type: ignore[call-arg]


def test_load_config_returns_defaults_when_file_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("INSO_DUMPER_CONFIG", raising=False)
    result = load_config()
    assert isinstance(result, Ok)
    assert result.value.base_url is not None
    assert result.value.rate_limit_per_second == 3.0


def test_load_config_reads_toml_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        'base_url = "https://example.invalid"\n'
        "rate_limit_per_second = 5.0\n"
        "request_timeout_seconds = 60.0\n"
    )
    monkeypatch.setenv("INSO_DUMPER_CONFIG", str(cfg_file))
    result = load_config()
    assert isinstance(result, Ok)
    assert str(result.value.base_url).rstrip("/") == "https://example.invalid"
    assert result.value.rate_limit_per_second == 5.0
    assert result.value.request_timeout_seconds == 60.0


def test_load_config_bad_toml_returns_err(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("this is not = valid TOML = [unterminated")
    monkeypatch.setenv("INSO_DUMPER_CONFIG", str(cfg_file))
    result = load_config()
    assert isinstance(result, Err)
    from inso_dumper.errors import CliErrorKind

    assert result.error.kind is CliErrorKind.CONFIG
    assert result.error.subject == "config_parse"


def test_load_config_never_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """load_config returns Result; it must never propagate exceptions to callers."""
    # Missing INSO_DUMPER_CONFIG and missing XDG: must not raise.
    monkeypatch.delenv("INSO_DUMPER_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    result = load_config()
    assert isinstance(result, Ok)

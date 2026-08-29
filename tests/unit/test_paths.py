"""Unit tests for the path resolver."""

from __future__ import annotations

from pathlib import Path

import pytest

from inso_dumper import paths


def test_default_config_file_is_xdg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/xdg-cfg")
    monkeypatch.setenv("HOME", "/home/test")
    # No INSO_DUMPER_CONFIG override.
    monkeypatch.delenv("INSO_DUMPER_CONFIG", raising=False)

    resolved = paths.config_file()
    assert resolved == Path("/tmp/xdg-cfg/inso-dumper/config.toml")


def test_default_config_file_falls_back_to_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", "/home/test")
    monkeypatch.delenv("INSO_DUMPER_CONFIG", raising=False)

    resolved = paths.config_file()
    assert resolved == Path("/home/test/.config/inso-dumper/config.toml")


def test_state_dir_uses_xdg_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", "/tmp/xdg-state")
    monkeypatch.setenv("HOME", "/home/test")
    resolved = paths.state_dir()
    assert resolved == Path("/tmp/xdg-state/inso-dumper")


def test_state_dir_falls_back_to_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", "/home/test")
    resolved = paths.state_dir()
    assert resolved == Path("/home/test/.local/state/inso-dumper")


def test_session_file_path_is_state_dir_plus_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", "/tmp/xdg-state")
    monkeypatch.setenv("HOME", "/home/test")
    resolved = paths.session_file()
    assert resolved == Path("/tmp/xdg-state/inso-dumper/session.json")


def test_paths_resolver_is_pure() -> None:
    """Same inputs → same outputs; no IO performed."""
    # Call twice with identical env state and assert identical results.
    a = paths.config_file()
    b = paths.config_file()
    assert a == b

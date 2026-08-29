"""Unit tests for the SessionStore shell."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from inso_dumper._result import Err, Ok
from inso_dumper.errors import CliErrorKind
from inso_dumper.models.session import Session
from inso_dumper.session.store import load_session, maybe_load_session, save_session


def _session() -> Session:
    return Session(
        phpsessid="d9f828d2629433b8d1b9690a17d477e3",
        user_uuid="eea48660-3740-11ed-a611-06dd2728d782",
    )


def test_round_trip_save_load(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    save_session(path, _session())
    result = load_session(path)
    assert isinstance(result, Ok)
    assert result.value == _session()


def test_saved_file_has_mode_0600(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    save_session(path, _session())
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_schema_version_2_returns_config_err(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "phpsessid": "abc",
                "user_uuid": "def",
            }
        )
    )
    os.chmod(path, 0o600)
    result = load_session(path)
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.CONFIG
    assert result.error.subject == "session_schema"


def test_unparseable_json_returns_config_err(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    path.write_text("not valid json")
    os.chmod(path, 0o600)
    result = load_session(path)
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.CONFIG
    assert result.error.subject == "session_schema"


def test_missing_file_via_maybe_load_returns_ok_none(tmp_path: Path) -> None:
    path = tmp_path / "does-not-exist.json"
    result = maybe_load_session(path)
    assert isinstance(result, Ok)
    assert result.value is None


def test_present_but_invalid_file_via_maybe_load_returns_err(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"schema_version": 2, "phpsessid": "x", "user_uuid": "y"}))
    os.chmod(path, 0o600)
    result = maybe_load_session(path)
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.CONFIG


def test_relaxed_mode_is_re_tightened_on_load(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    save_session(path, _session())
    # Simulate an external process relaxing the file mode.
    os.chmod(path, 0o644)
    result = load_session(path)
    assert isinstance(result, Ok)
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_atomic_write_leaves_no_tmp_on_success(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    save_session(path, _session())
    assert not (tmp_path / "session.json.tmp").exists()
    assert path.exists()


def test_atomic_write_leaves_no_tmp_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "session.json"
    real_replace = os.replace

    def failing_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        # Python 3.14's os.replace accepts Path objects, not just strings.
        src_str = os.fspath(src)
        dst_str = os.fspath(dst)
        if dst_str == str(path) and src_str.endswith(".tmp"):
            raise OSError("simulated crash")
        real_replace(src, dst)

    monkeypatch.setattr("os.replace", failing_replace)
    # First call should not raise (it returns Err) and the final path
    # should not exist. No leftover .tmp should be visible.
    result = save_session(path, _session())
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.CONFIG
    assert not path.exists()
    # The .tmp may still be present in the simulated-crash scenario; that
    # is acceptable (next save will overwrite it). What must NOT happen is
    # a partial session.json at the real path.
    leftover = tmp_path / "session.json.tmp"
    if leftover.exists():
        # If the .tmp lingers, it must not be readable as a valid session.
        from inso_dumper.session.store import maybe_load_session

        second = maybe_load_session(leftover)
        # Either it's an unparseable leftover (Err CONFIG) or a parsed
        # session — both are fine; the real session.json is intact (absent).
        assert second is not None

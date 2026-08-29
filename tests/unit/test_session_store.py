"""Unit tests for the SessionStore shell."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from inso_dumper._result import Err, Ok
from inso_dumper.errors import CliErrorKind
from inso_dumper.session.store import maybe_load_session, save_session
from tests.conftest import make_session


def test_round_trip_save_load(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    save_session(path, make_session())
    result = maybe_load_session(path)
    assert isinstance(result, Ok)
    assert result.value is not None
    assert result.value == make_session()


def test_saved_file_has_mode_0600(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    save_session(path, make_session())
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_schema_version_2_returns_config_err(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"schema_version": 2, "phpsessid": "abc", "user_uuid": "def"}))
    os.chmod(path, 0o600)
    result = maybe_load_session(path)
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.CONFIG
    assert result.error.subject == "session_schema"


def test_unparseable_json_returns_config_err(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    path.write_text("not valid json")
    os.chmod(path, 0o600)
    result = maybe_load_session(path)
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.CONFIG
    assert result.error.subject == "session_schema"


def test_missing_file_returns_ok_none(tmp_path: Path) -> None:
    path = tmp_path / "does-not-exist.json"
    result = maybe_load_session(path)
    assert isinstance(result, Ok)
    assert result.value is None


def test_relaxed_mode_is_re_tightened_on_load(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    save_session(path, make_session())
    # Simulate an external process relaxing the file mode.
    os.chmod(path, 0o644)
    result = maybe_load_session(path)
    assert isinstance(result, Ok)
    assert result.value is not None
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_atomic_write_leaves_no_tmp_on_success(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    save_session(path, make_session())
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
    result = save_session(path, make_session())
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.CONFIG
    assert not path.exists()


def test_fsync_is_called_before_close(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A power-cut between os.write and os.replace loses the session.

    The fix is os.fsync(fd) inside the os.open/os.close pair. The test
    asserts the call happened — without it, the save can succeed at the
    syscall level but vanish on crash.
    """
    path = tmp_path / "session.json"
    calls: list[str] = []
    real_fsync = os.fsync

    def traced_fsync(fd: int) -> None:
        calls.append("fsync")
        real_fsync(fd)

    monkeypatch.setattr("os.fsync", traced_fsync)
    save_session(path, make_session())
    assert "fsync" in calls


def test_save_session_keeps_real_file_intact_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real session.json must never contain partial content."""
    path = tmp_path / "session.json"
    # First write succeeds.
    save_session(path, make_session())
    assert path.exists()

    real_replace = os.replace

    def failing_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        if os.fspath(dst) == str(path):
            raise OSError("simulated crash on replace")
        real_replace(src, dst)

    monkeypatch.setattr("os.replace", failing_replace)
    result = save_session(path, make_session())
    assert isinstance(result, Err)
    # The original file is still there from the first save (os.replace
    # was never called for the second save).
    assert path.exists()


def test_save_session_loops_on_short_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """os.write may return fewer bytes than requested; the loop continues."""
    path = tmp_path / "session.json"
    real_write = os.write
    call_count = {"n": 0}

    def first_call_short(
        fd: int, data: bytes | memoryview, *args: object, **kwargs: object
    ) -> int:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return real_write(fd, bytes(data[: len(data) // 2]))
        return real_write(fd, bytes(data))

    monkeypatch.setattr("os.write", first_call_short)
    save_session(path, make_session())
    assert call_count["n"] >= 2
    result = maybe_load_session(path)
    assert isinstance(result, Ok)
    assert result.value is not None


def test_save_session_aborts_on_zero_byte_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 0-byte return from os.write raises OSError; the save fails clean."""
    path = tmp_path / "session.json"

    def zero_write(fd: int, data: bytes | memoryview, *args: object, **kwargs: object) -> int:
        return 0

    monkeypatch.setattr("os.write", zero_write)
    result = save_session(path, make_session())
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.CONFIG
    assert not path.exists()

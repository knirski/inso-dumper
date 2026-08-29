"""Unit tests for ensure_session_loaded."""

from __future__ import annotations

import json
import os
from pathlib import Path

from inso_dumper._result import Err, Ok
from inso_dumper.errors import CliErrorKind
from inso_dumper.http.session_expiry import ensure_session_loaded
from inso_dumper.models.session import Session


def test_missing_file_returns_session_expired(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    result = ensure_session_loaded(path)
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.SESSION_EXPIRED


def test_valid_file_returns_session(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    session = Session(
        phpsessid="d9f828d2629433b8d1b9690a17d477e3",
        user_uuid="eea48660-3740-11ed-a611-06dd2728d782",
    )
    path.write_text(session.model_dump_json())
    os.chmod(path, 0o600)
    result = ensure_session_loaded(path)
    assert isinstance(result, Ok)
    assert result.value == session


def test_schema_version_mismatch_returns_config(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"schema_version": 99, "phpsessid": "x", "user_uuid": "y"}))
    os.chmod(path, 0o600)
    result = ensure_session_loaded(path)
    assert isinstance(result, Err)
    assert result.error.kind is CliErrorKind.CONFIG

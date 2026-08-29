"""Re-export the shared test helpers from tests._fakes."""

from inso_dumper._result import Err, Ok
from tests._fakes import (
    SPIKE_PHPSESSID,
    SPIKE_USER_UUID,
    FakeHttpClient,
    make_attachment_dict,
    make_photo_dict,
    make_post_dict,
    make_session,
    make_video_dict,
)

__all__ = [
    "SPIKE_PHPSESSID",
    "SPIKE_USER_UUID",
    "Err",
    "FakeHttpClient",
    "Ok",
    "make_attachment_dict",
    "make_photo_dict",
    "make_post_dict",
    "make_session",
    "make_video_dict",
]

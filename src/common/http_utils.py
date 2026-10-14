"""HTTP session helpers that keep timeout policy local to each API client.

Avoid changing ``requests.sessions.Session.request`` globally. Global monkey patches
can leak into unrelated requests and race across concurrent workers. YTMusic accepts
an injected Requests session, so callers can give it a session with a default timeout.
"""
from __future__ import annotations

from functools import partial

import requests


def make_timeout_session(seconds: int) -> requests.Session:
    """Create an independent Requests session with a default request timeout."""
    timeout = max(5, int(seconds))
    session = requests.Session()
    session.request = partial(session.request, timeout=timeout)  # type: ignore[method-assign]
    return session

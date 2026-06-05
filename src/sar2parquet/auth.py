"""Earthdata / ASF credential handling.

Credentials can be supplied (in priority order) via:
1. explicit arguments,
2. an ``ASF_TOKEN`` (or ``ASF_USERNAME``/``ASF_PASSWORD``) environment variable,
3. a ``~/.netrc`` entry for ``urs.earthdata.nasa.gov``.
"""

from __future__ import annotations

import os
import netrc as _netrc
from typing import Optional, Tuple


def get_creds(
    cli_user: Optional[str] = None,
    cli_pass: Optional[str] = None,
    cli_token: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Resolve credentials, returning ``(user, password, token)``.

    A token takes precedence over a user/password pair. Raises ``RuntimeError``
    if no usable credentials can be found.
    """
    if cli_token:
        return None, None, cli_token

    token = os.environ.get("ASF_TOKEN")
    if token:
        return None, None, token

    if cli_user and cli_pass:
        return cli_user, cli_pass, None

    u = os.environ.get("ASF_USERNAME")
    p = os.environ.get("ASF_PASSWORD")
    if u and p:
        return u, p, None

    try:
        auth = _netrc.netrc().authenticators("urs.earthdata.nasa.gov")
        if auth:
            u, _, p = auth
            if u and p:
                return u, p, None
    except Exception:
        pass

    raise RuntimeError(
        "No ASF credentials found. Provide a token/username+password argument, "
        "set ASF_TOKEN or ASF_USERNAME/ASF_PASSWORD, or add a ~/.netrc entry for "
        "urs.earthdata.nasa.gov."
    )


def build_asf_session(
    user: Optional[str] = None,
    pwd: Optional[str] = None,
    token: Optional[str] = None,
):
    """Build an authenticated :class:`asf_search.ASFSession`."""
    import asf_search as asf

    sess = asf.ASFSession()
    if token:
        sess.auth_with_token(token)
    elif user and pwd:
        sess.auth_with_creds(user, pwd)
    else:
        raise RuntimeError("No credentials available to build an ASF session.")
    return sess

"""One HTTP client for every outbound request that carries a credential.

The defect this replaces
------------------------
Three code paths checked a URL against an allow-list (or a hard-coded host), then
called ``urllib.request.urlopen``. ``urlopen`` follows 3xx redirects by default
and — for the same-origin case — re-sends the original headers, including
``Authorization`` / ``x-api-key``. A 302 from an allow-listed host was therefore
enough to deliver the credential to a host nobody had approved. It was
reproduced against integrations, the OpenAI-compatible provider and the sync
blob store with a dummy token.

The rule now
------------
Requests that carry a secret are sent through :func:`open_once`, which installs a
handler that **raises instead of following** any redirect. A server that answers
a credentialed request with a 3xx is not "helpfully moving us"; it is either
misconfigured or trying to relocate the credential. The caller sees
:class:`RedirectRefused` and the audit line says so.

Plain, credential-free GETs (health checks) may still use :func:`open_follow`
when a redirect is harmless.
"""
from __future__ import annotations

import urllib.error
import urllib.request
from typing import Any, Optional
from urllib.parse import urlparse


class RedirectRefused(urllib.error.HTTPError):
    """A credentialed request was answered with a redirect; it was not followed."""

    def __init__(self, url: str, code: int, location: str):
        super().__init__(url, code, f"refused to follow redirect to {location or '(none)'}",
                         {}, None)          # type: ignore[arg-type]
        self.url = url
        self.code = code
        self.location = location

    def __str__(self) -> str:
        return (f"refused to follow a {self.code} redirect from "
                f"{urlparse(self.url).hostname or self.url} to "
                f"{self.location or '(none)'}: a credentialed request is never "
                f"redirected to another location")


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        raise RedirectRefused(req.full_url, code, newurl)


_STRICT = urllib.request.build_opener(_RefuseRedirects)
_DEFAULT = urllib.request.build_opener()


def open_once(req: urllib.request.Request, timeout: float,
              context: Optional[Any] = None):
    """Open a request, refusing every redirect (use for anything with a secret).

    ``context`` is an ``ssl.SSLContext`` when the caller supplies one.
    """
    if context is not None:
        opener = urllib.request.build_opener(
            _RefuseRedirects, urllib.request.HTTPSHandler(context=context))
        return opener.open(req, timeout=timeout)
    return _STRICT.open(req, timeout=timeout)


def open_follow(req: urllib.request.Request, timeout: float,
                context: Optional[Any] = None):
    """Open a request and let it follow redirects (no credential attached)."""
    if context is not None:
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=context))
        return opener.open(req, timeout=timeout)
    return _DEFAULT.open(req, timeout=timeout)


__all__ = ["RedirectRefused", "open_once", "open_follow"]

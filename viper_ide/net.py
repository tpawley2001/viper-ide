"""Process-wide HTTPS settings for urllib."""
from __future__ import annotations

import ssl
import urllib.request


def https_context() -> ssl.SSLContext:
    """Default verification minus Python 3.13+'s VERIFY_X509_STRICT.

    Antivirus HTTPS scanning (Avast, ESET, Kaspersky...) and corporate proxies re-sign traffic with
    their own root, and those roots often lack an Authority Key Identifier, so strict mode fails with
    "Missing Authority Key Identifier" even though Windows and browsers trust them. Chain and hostname
    checks stay on.
    """
    ctx = ssl.create_default_context()
    ctx.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)
    return ctx


def install() -> None:
    """Make every plain urllib.request.urlopen() call use https_context()."""
    urllib.request.install_opener(urllib.request.build_opener(urllib.request.HTTPSHandler(context=https_context())))

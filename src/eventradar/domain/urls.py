"""URL canonicalization for stable identifiers and deduplication."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAMS = frozenset(
    {"aff", "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "source"}
)


def canonical_url(url: str) -> str:
    """
    Normalize a URL so equivalent links compare equal.

    Lowercases scheme and host, drops fragments, default ports, and
    tracking parameters (`utm_*`, `aff`, ...), and sorts the query.

    Parameters:
      url: Absolute URL.
    Returns:
      Canonical URL.
    """
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    default = {"http": 80, "https": 443}.get(scheme)
    netloc = host if parts.port in (None, default) else f"{host}:{parts.port}"
    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS
        and not k.lower().startswith("utm_")
    )
    return urlunsplit((scheme, netloc, parts.path or "/", urlencode(query), ""))

"""Strip credentials from URLs before they are logged or stored."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SECRET_PARAMS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "client_secret",
        "key",
        "password",
        "secret",
        "sig",
        "signature",
        "token",
    }
)


def redact_url(url: str) -> str:
    """
    Replace credential-like query values and userinfo with `REDACTED`.

    Parameters:
      url: Absolute URL.
    Returns:
      The URL with secrets masked.
    """
    parts = urlsplit(url)
    netloc = parts.netloc
    if "@" in netloc:
        netloc = "REDACTED@" + netloc.rsplit("@", 1)[1]
    query = urlencode(
        [
            (k, "REDACTED" if k.lower() in _SECRET_PARAMS else v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
        ]
    )
    return urlunsplit(parts._replace(netloc=netloc, query=query))

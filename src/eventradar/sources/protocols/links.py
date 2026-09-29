"""Collect hyperlinks from HTML pages."""

from html.parser import HTMLParser
from urllib.parse import urljoin


class _AnchorCollector(HTMLParser):
    """Gathers the `href` of every `<a>` tag."""

    def __init__(self) -> None:
        """Start with no links."""
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        """
        Record an anchor's target.

        Parameters:
          tag: Tag name.
          attrs: Tag attributes.
        """
        href = dict(attrs).get("href") if tag == "a" else None
        if href:
            self.hrefs.append(href.strip())


def page_links(html: str, base_url: str) -> list[str]:
    """
    List a page's absolute http(s) links in document order, deduplicated.

    Parameters:
      html: Page HTML.
      base_url: Page URL, for resolving relative links.
    Returns:
      Absolute URLs.
    """
    collector = _AnchorCollector()
    collector.feed(html)
    seen: dict[str, None] = {}
    for href in collector.hrefs:
        url = urljoin(base_url, href)
        if url.startswith(("http://", "https://")):
            seen.setdefault(url, None)
    return list(seen)

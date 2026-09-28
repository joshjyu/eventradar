"""Find JSON-LD blocks in HTML and walk their nodes."""

import json
from collections.abc import Iterator
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

_EVENT_TYPES_EXTRA = frozenset({"Festival"})


class _ScriptCollector(HTMLParser):
    """Collects the text of `<script type="application/ld+json">` tags."""

    def __init__(self) -> None:
        """Start with no blocks."""
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._inside = False
        self._buffer: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        """
        Enter a JSON-LD script tag.

        Parameters:
          tag: Tag name.
          attrs: Tag attributes.
        """
        kind = (dict(attrs).get("type") or "").split(";")[0].strip().lower()
        if tag == "script" and kind == "application/ld+json":
            self._inside = True
            self._buffer = []

    def handle_endtag(self, tag: str) -> None:
        """
        Leave a script tag, keeping its text.

        Parameters:
          tag: Tag name.
        """
        if tag == "script" and self._inside:
            self.blocks.append("".join(self._buffer))
            self._inside = False

    def handle_data(self, data: str) -> None:
        """
        Buffer text inside a JSON-LD tag.

        Parameters:
          data: Text chunk.
        """
        if self._inside:
            self._buffer.append(data)


def jsonld_documents(html: str) -> list[Any]:
    """
    Parse every JSON-LD block in a page; malformed blocks are skipped.

    Parameters:
      html: Page HTML.
    Returns:
      Decoded JSON documents.
    """
    collector = _ScriptCollector()
    collector.feed(html)
    docs = []
    for block in collector.blocks:
        text = block.strip()
        for wrapper in (("<!--", "-->"), ("<![CDATA[", "]]>")):
            if text.startswith(wrapper[0]) and text.endswith(wrapper[1]):
                text = text[len(wrapper[0]) : -len(wrapper[1])].strip()
        try:
            docs.append(json.loads(text))
        except ValueError:
            continue
    return docs


def walk(node: Any) -> Iterator[dict[str, Any]]:
    """
    Yield every object in a JSON-LD document, depth first.

    Parameters:
      node: Document or fragment.
    Returns:
      Iterator over dict nodes, including nested and `@graph` members.
    """
    if isinstance(node, list):
        for item in node:
            yield from walk(item)
    elif isinstance(node, dict):
        yield node
        for value in node.values():
            if isinstance(value, dict | list):
                yield from walk(value)


def types_of(node: dict[str, Any]) -> set[str]:
    """
    Read a node's `@type` values without namespace prefixes.

    Parameters:
      node: JSON-LD object.
    Returns:
      Bare type names.
    """
    raw = node.get("@type", [])
    values = raw if isinstance(raw, list) else [raw]
    return {str(v).rsplit("/", 1)[-1].rsplit(":", 1)[-1] for v in values}


def is_event(node: dict[str, Any]) -> bool:
    """
    Check for schema.org Event or any subtype (BusinessEvent, ...).

    Parameters:
      node: JSON-LD object.
    Returns:
      True for event nodes.
    """
    return any(
        t.endswith("Event") or t in _EVENT_TYPES_EXTRA for t in types_of(node)
    )


def events(documents: list[Any]) -> list[dict[str, Any]]:
    """
    Collect top-level event nodes, skipping events nested in other events.

    Parameters:
      documents: Decoded JSON-LD documents.
    Returns:
      Event nodes in document order.
    """
    found: list[dict[str, Any]] = []
    nested: set[int] = set()
    for node in walk(documents):
        if not is_event(node) or id(node) in nested:
            continue
        found.append(node)
        nested.update(id(n) for n in walk(list(node.values())))
    return found


def item_list_entries(
    documents: list[Any], base_url: str
) -> list[tuple[str, dict[str, Any] | None]]:
    """
    Read URLs (and inline items, if any) from every ItemList.

    Parameters:
      documents: Decoded JSON-LD documents.
      base_url: Page URL for resolving relative links.
    Returns:
      (absolute URL, inline item or None) pairs in list order.
    """
    entries = []
    for node in walk(documents):
        if "ItemList" not in types_of(node):
            continue
        elements = node.get("itemListElement", [])
        for element in elements if isinstance(elements, list) else []:
            item = (
                element.get("item", element)
                if isinstance(element, dict)
                else element
            )
            if isinstance(item, str):
                entries.append((urljoin(base_url, item), None))
            elif isinstance(item, dict) and isinstance(item.get("url"), str):
                entries.append((urljoin(base_url, item["url"]), item))
    return entries

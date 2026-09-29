"""Decide whether two events are the same, and group duplicates."""

import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from rapidfuzz import fuzz

from eventradar.storage.repositories import ResolveCandidate

# Dates, years, and hashtags vary between postings of the same event.
_DATE = re.compile(r"\b\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?\b")
_YEAR = re.compile(r"\b20\d{2}\b")
_HASHTAG = re.compile(r"#\w+")
_NON_WORD = re.compile(r"[^\w\s]")


@dataclass(frozen=True)
class Thresholds:
    """Tunable limits for duplicate detection."""

    # Title similarity (0-100) when both events are located and close.
    title_with_place: float = 85.0
    # Stricter similarity when either event has no coordinates.
    title_without_place: float = 92.0
    max_start_delta_min: float = 90.0
    # Within this distance the loose title rule applies.
    max_distance_km: float = 1.0
    # Up to this distance (geocoding noise, e.g. ZIP centroids) only the
    # strict title rule applies; beyond it events never match.
    max_noisy_distance_km: float = 5.0


def normalize_title(title: str) -> str:
    """
    Reduce a title to comparable words.

    Parameters:
      title: Event title.
    Returns:
      Lower-case words without dates, years, hashtags, or symbols.
    """
    text = unicodedata.normalize("NFKC", title).lower()
    for pattern in (_HASHTAG, _DATE, _YEAR, _NON_WORD):
        text = pattern.sub(" ", text)
    return " ".join(text.split())


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Great-circle distance.

    Parameters:
      lat1: First latitude.
      lon1: First longitude.
      lat2: Second latitude.
      lon2: Second longitude.
    Returns:
      Distance in kilometers.
    """
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = (
        math.sin(dp / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    )
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def is_duplicate(
    a: ResolveCandidate, b: ResolveCandidate, limits: Thresholds
) -> bool:
    """
    Decide whether two events describe the same occurrence.

    Parameters:
      a: First event.
      b: Second event.
      limits: Thresholds.
    Returns:
      True when start times, places, and titles all agree.
    """
    delta = abs((a.start_utc - b.start_utc).total_seconds()) / 60
    if delta > limits.max_start_delta_min:
        return False
    if a.url and a.url == b.url:
        return True
    ta, tb = normalize_title(a.title), normalize_title(b.title)
    if not ta or not tb:
        return False
    loose = _title_score(ta, tb, loose=True) >= limits.title_with_place
    strict = _title_score(ta, tb, loose=False) >= limits.title_without_place
    if (
        a.lat is not None
        and a.lon is not None
        and b.lat is not None
        and b.lon is not None
    ):
        km = distance_km(a.lat, a.lon, b.lat, b.lon)
        if km <= limits.max_distance_km:
            return loose
        return km <= limits.max_noisy_distance_km and strict
    return strict


def _title_score(a: str, b: str, loose: bool) -> float:
    """
    Score title similarity, also comparing with spaces removed.

    Titles equal once spaces are removed ("OCC Hacks" vs "OCCHacks",
    "Hack-a-thon" vs "Hackathon") score 100. That check is exact: fuzzy
    matching without word boundaries merges "Tech" with "Fintech".

    Parameters:
      a: Normalized title.
      b: Normalized title.
      loose: Allow one title's words to be a subset of the other's.
    Returns:
      Similarity from 0 to 100.
    """
    words = fuzz.token_set_ratio(a, b) if loose else fuzz.token_sort_ratio(a, b)
    if a.replace(" ", "") == b.replace(" ", ""):
        return 100.0
    return words


def clusters(
    candidates: Sequence[ResolveCandidate], limits: Thresholds
) -> list[list[str]]:
    """
    Group duplicates; only events close in start time are compared.

    Parameters:
      candidates: Events sorted by start time.
      limits: Thresholds.
    Returns:
      Groups of two or more event ids, each sorted.
    """
    parent = {c.event_id: c.event_id for c in candidates}

    def find(x: str) -> str:
        """
        Find a group's representative.

        Parameters:
          x: Event id.
        Returns:
          Representative id.
        """
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    window = limits.max_start_delta_min * 60
    ordered = sorted(candidates, key=lambda c: c.start_utc)
    for i, a in enumerate(ordered):
        for b in ordered[i + 1 :]:
            if (b.start_utc - a.start_utc).total_seconds() > window:
                break
            if is_duplicate(a, b, limits):
                parent[find(b.event_id)] = find(a.event_id)
    groups: dict[str, list[str]] = {}
    for c in candidates:
        groups.setdefault(find(c.event_id), []).append(c.event_id)
    return sorted(sorted(g) for g in groups.values() if len(g) > 1)

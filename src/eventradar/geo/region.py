"""Region membership by point-in-polygon over GeoJSON boundaries."""

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

type Ring = list[tuple[float, float]]


@dataclass(frozen=True)
class _Polygon:
    """An outer ring with holes, plus its bounding box."""

    rings: tuple[Ring, ...]
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    def contains(self, lat: float, lon: float) -> bool:
        """
        Test a point with the even-odd rule across all rings.

        Parameters:
          lat: Latitude.
          lon: Longitude.
        Returns:
          True if inside the outer ring and outside every hole.
        """
        if not (
            self.min_lat <= lat <= self.max_lat
            and self.min_lon <= lon <= self.max_lon
        ):
            return False
        inside = False
        for ring in self.rings:
            for (x1, y1), (x2, y2) in zip(
                ring, ring[1:] + ring[:1], strict=True
            ):
                crosses = (y1 > lat) != (y2 > lat)
                if crosses and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                    inside = not inside
        return inside


class Region:
    """A set of polygons loaded from a GeoJSON file."""

    def __init__(self, geojson: dict[str, Any]) -> None:
        """
        Index the polygons of a Feature, FeatureCollection, or geometry.

        Parameters:
          geojson: Parsed GeoJSON.
        """
        self._polygons = tuple(_polygons(geojson))
        if not self._polygons:
            raise ValueError("region has no polygons")

    def contains(self, lat: float, lon: float) -> bool:
        """
        Test whether a point lies in the region.

        Parameters:
          lat: Latitude.
          lon: Longitude.
        Returns:
          True if any polygon contains the point.
        """
        return any(p.contains(lat, lon) for p in self._polygons)


def _polygons(node: dict[str, Any]) -> list[_Polygon]:
    """
    Flatten GeoJSON into polygons.

    Parameters:
      node: GeoJSON object.
    Returns:
      Polygons with bounding boxes.
    """
    kind = node.get("type")
    if kind == "FeatureCollection":
        return [p for f in node.get("features", []) for p in _polygons(f)]
    if kind == "Feature":
        return _polygons(node.get("geometry") or {})
    if kind == "Polygon":
        shapes = [node["coordinates"]]
    elif kind == "MultiPolygon":
        shapes = node["coordinates"]
    else:
        return []
    polygons = []
    for shape in shapes:
        rings = tuple([(float(x), float(y)) for x, y, *_ in r] for r in shape)
        xs = [x for x, _ in rings[0]]
        ys = [y for _, y in rings[0]]
        polygons.append(_Polygon(rings, min(xs), min(ys), max(xs), max(ys)))
    return polygons


@cache
def load_region(path: Path) -> Region:
    """
    Load and cache a region file.

    Parameters:
      path: GeoJSON file.
    Returns:
      The region.
    """
    return Region(json.loads(path.read_text()))

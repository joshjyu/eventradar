"""Tests for region membership."""

from pathlib import Path

import pytest

from eventradar.geo.region import Region, load_region

SOCAL = Path(__file__).resolve().parents[2] / "config/regions/socal.geojson"


@pytest.mark.parametrize(
    ("name", "lat", "lon", "inside"),
    [
        ("Santa Monica Pier", 34.0092, -118.4973, True),
        ("La Jolla Cove", 32.8505, -117.2713, True),
        ("Newport Pier", 33.6073, -117.9295, True),
        ("Avalon, Catalina Island", 33.3428, -118.3282, True),
        ("Santa Barbara Harbor", 34.4038, -119.6935, True),
        ("Palm Springs", 33.8303, -116.5453, True),
        ("El Centro", 32.7920, -115.5631, True),
        ("Bakersfield (Kern County)", 35.3733, -119.0187, False),
        ("Tijuana", 32.5149, -117.0382, False),
        ("Las Vegas", 36.1699, -115.1398, False),
        ("Pacific, 20 km offshore", 33.9, -118.8, False),
    ],
)
def test_socal_boundary(
    name: str, lat: float, lon: float, inside: bool
) -> None:
    """
    Coastal and border landmarks fall on the right side.

    Parameters:
      name: Landmark.
      lat: Latitude.
      lon: Longitude.
      inside: Expected membership.
    """
    assert load_region(SOCAL).contains(lat, lon) is inside, name


def test_holes_are_excluded() -> None:
    """A point in a polygon's hole is outside."""
    square = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
    hole = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]
    region = Region({"type": "Polygon", "coordinates": [square, hole]})
    assert region.contains(2, 2)
    assert not region.contains(5, 5)
    assert not region.contains(20, 20)


def test_empty_region_is_rejected() -> None:
    """A region file without polygons is a config error."""
    with pytest.raises(ValueError, match="no polygons"):
        Region({"type": "FeatureCollection", "features": []})

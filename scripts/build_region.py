"""
Build a region GeoJSON from U.S. Census county boundaries (TIGERweb).

Boundaries are simplified to about 50 m, which keeps coastal venues inside.
Usage:

  uv run python scripts/build_region.py socal "Southern California" 06 \\
      "Los Angeles" Orange "San Diego" Riverside "San Bernardino" \\
      Ventura "Santa Barbara" Imperial
"""

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

QUERY = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
    "State_County/MapServer/1/query"
)
USER_AGENT = "eventradar/0.1 (+https://github.com/joshjyu/eventradar)"
REGIONS = Path(__file__).resolve().parents[1] / "config" / "regions"


def build(region_id: str, name: str, state: str, counties: list[str]) -> int:
    """
    Download county polygons and write `config/regions/<id>.geojson`.

    Parameters:
      region_id: Region id (file stem).
      name: Display name.
      state: Two-digit state FIPS code.
      counties: County base names.
    Returns:
      Number of counties written.
    """
    quoted = ", ".join("'" + c.replace("'", "''") + "'" for c in counties)
    params = urllib.parse.urlencode(
        {
            "where": f"STATE='{state}' AND BASENAME IN ({quoted})",
            "outFields": "BASENAME,GEOID",
            "outSR": "4326",
            "maxAllowableOffset": "0.0005",
            "geometryPrecision": "5",
            "f": "geojson",
        }
    )
    # QUERY is a fixed https URL.
    request = urllib.request.Request(  # noqa: S310
        f"{QUERY}?{params}", headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=120) as resp:  # noqa: S310
        data = json.load(resp)
    features = sorted(
        (
            {
                "type": "Feature",
                "properties": {
                    "county": f["properties"]["BASENAME"],
                    "geoid": f["properties"]["GEOID"],
                },
                "geometry": f["geometry"],
            }
            for f in data["features"]
        ),
        key=lambda f: f["properties"]["geoid"],
    )
    if len(features) != len(counties):
        raise SystemExit(f"expected {len(counties)} counties, got {features}")
    doc = {
        "type": "FeatureCollection",
        "properties": {
            "id": region_id,
            "name": name,
            "source": "U.S. Census Bureau TIGERweb, simplified to ~50 m",
        },
        "features": features,
    }
    path = REGIONS / f"{region_id}.geojson"
    path.write_text(json.dumps(doc, separators=(",", ":")) + "\n")
    return len(features)


if __name__ == "__main__":
    print(build(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]))

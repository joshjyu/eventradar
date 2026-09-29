"""
Build the city/place centroid table shipped with eventradar.

Source: U.S. Census Bureau Gazetteer, Places (public domain). Usage:

  uv run python scripts/build_place_centroids.py 2025
"""

import csv
import gzip
import io
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
    "{year}_Gazetteer/{year}_Gaz_place_national.zip"
)
OUT = (
    Path(__file__).resolve().parents[1]
    / "src/eventradar/geo/data/place_centroids.csv.gz"
)
# Gazetteer names end in the place's legal type ("Irvine city",
# "East Los Angeles CDP"); addresses never include it.
_TYPE = re.compile(
    r"\s+(?:(?:city and borough|city|town|village|borough|CDP|comunidad|"
    r"zona urbana|municipality|corporation|urban county|"
    r"(?:unified|consolidated|metropolitan) government)"
    r"(?:\s+\(balance\))?|\(balance\))$",
    re.IGNORECASE,
)


def place_name(name: str) -> str:
    """
    Drop the legal type from a gazetteer place name.

    Parameters:
      name: Gazetteer `NAME`, e.g. `Los Angeles city`.
    Returns:
      The name as addresses write it, e.g. `Los Angeles`.
    """
    return _TYPE.sub("", name.strip())


def build(year: str) -> int:
    """
    Download the gazetteer and write `state,place,lat,lon` rows, gzipped.

    Where one state has two places with the same name (a city and a CDP),
    the one with more land area wins.

    Parameters:
      year: Gazetteer vintage, e.g. `2025`.
    Returns:
      Number of rows written.
    """
    with urllib.request.urlopen(URL.format(year=year), timeout=60) as resp:  # noqa: S310
        archive = zipfile.ZipFile(io.BytesIO(resp.read()))
    name = next(n for n in archive.namelist() if n.endswith(".txt"))
    text = archive.read(name).decode("utf-8")
    rows = csv.DictReader(io.StringIO(text), delimiter="|")
    rows.fieldnames = [f.strip() for f in rows.fieldnames or []]
    best: dict[tuple[str, str], tuple[int, str, str]] = {}
    for r in rows:
        key = (r["USPS"].strip(), place_name(r["NAME"]))
        area = int(r["ALAND"])
        point = (
            f"{float(r['INTPTLAT']):.5f}",
            f"{float(r['INTPTLONG'].strip()):.5f}",
        )
        if key not in best or area > best[key][0]:
            best[key] = (area, *point)
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(("state", "place", "lat", "lon"))
    writer.writerows(
        (state, place, lat, lon)
        for (state, place), (_, lat, lon) in sorted(best.items())
    )
    # mtime=0 keeps the output byte-identical across rebuilds.
    with (
        OUT.open("wb") as raw,
        gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as fh,
    ):
        fh.write(buffer.getvalue().encode("utf-8"))
    return len(best)


if __name__ == "__main__":
    print(build(sys.argv[1] if len(sys.argv) > 1 else "2025"))

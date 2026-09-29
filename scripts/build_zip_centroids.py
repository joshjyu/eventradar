"""
Build the ZIP code centroid table shipped with eventradar.

Source: U.S. Census Bureau Gazetteer, ZIP Code Tabulation Areas (public
domain). Usage:

  uv run python scripts/build_zip_centroids.py 2025
"""

import csv
import gzip
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
    "{year}_Gazetteer/{year}_Gaz_zcta_national.zip"
)
OUT = (
    Path(__file__).resolve().parents[1]
    / "src/eventradar/geo/data/zip_centroids.csv.gz"
)


def build(year: str) -> int:
    """
    Download the gazetteer and write `zip,lat,lon` rows, gzipped.

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
    out = sorted(
        (
            r["GEOID"],
            f"{float(r['INTPTLAT']):.5f}",
            f"{float(r['INTPTLONG']):.5f}",
        )
        for r in rows
    )
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(("zip", "lat", "lon"))
    writer.writerows(out)
    # mtime=0 keeps the output byte-identical across rebuilds.
    with (
        OUT.open("wb") as raw,
        gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as fh,
    ):
        fh.write(buffer.getvalue().encode("utf-8"))
    return len(out)


if __name__ == "__main__":
    print(build(sys.argv[1] if len(sys.argv) > 1 else "2025"))

"""Tests for the normalize stage's ingest region filter."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from eventradar.domain.models import EventDraft, RawRecord
from eventradar.geo.region import Region
from eventradar.pipeline.normalize import parse_and_upsert
from eventradar.storage.db import connect, migrate
from eventradar.storage.repositories import EventRepository

NOW = datetime(2026, 10, 1, tzinfo=UTC)
SQUARE = Region(
    {
        "type": "Polygon",
        "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]],
    }
)


class _Echo:
    """Adapter stand-in whose records carry their draft fields."""

    def parse(self, raw: RawRecord) -> list[EventDraft]:
        """
        Build a draft from the record payload.

        Parameters:
          raw: Record.
        Returns:
          One draft.
        """
        return [
            EventDraft(
                source_id=raw.source_id,
                native_id=raw.native_id,
                title=raw.native_id,
                start_utc=NOW + timedelta(days=1),
                **raw.payload,
            )
        ]


def test_keep_region_drops_outside_and_unlocated(tmp_path: Path) -> None:
    """
    Only drafts located inside the region are written.

    Parameters:
      tmp_path: Pytest temporary directory.
    """
    conn = connect(tmp_path / "s.db")
    migrate(conn)
    repo = EventRepository(conn)
    payloads = {
        "inside": {"lat": 5.0, "lon": 5.0},
        "outside": {"lat": 50.0, "lon": 50.0},
        "unlocated": {},
    }
    records = [
        RawRecord(source_id="s", native_id=k, payload=v, fetched_at=NOW)
        for k, v in payloads.items()
    ]
    counts = parse_and_upsert(_Echo(), records, repo, NOW, keep=SQUARE)  # type: ignore[arg-type]
    titles = [r[0] for r in conn.execute("SELECT title FROM events")]
    assert titles == ["inside"]
    assert (counts.parsed, counts.errors) == (1, 0)

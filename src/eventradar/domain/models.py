"""Typed records passed between pipeline stages."""

from typing import Any, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    model_validator,
)

from eventradar.domain.enums import AttendanceMode, EventKind, EventStatus
from eventradar.domain.ids import content_hash

SCHEMA_VERSION = "1.0"


class _Frozen(BaseModel):
    """Base for immutable domain records."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class RawRecord(_Frozen):
    """Unparsed upstream payload, exactly as an adapter fetched it."""

    source_id: str
    native_id: str
    url: str | None = None
    payload: dict[str, Any]
    fetched_at: AwareDatetime

    @property
    def content_hash(self) -> str:
        """
        Hash of the payload, used to skip unchanged records.

        Returns:
          Hex-encoded SHA-256 digest of the payload.
        """
        return content_hash(self.payload)


class _EventFields(_Frozen):
    """Descriptive fields shared by drafts and published events."""

    title: str = Field(min_length=1)
    description: str | None = None
    start_utc: AwareDatetime
    end_utc: AwareDatetime | None = None
    tz: str | None = None
    venue: str | None = None
    address: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    attendance_mode: AttendanceMode = AttendanceMode.UNKNOWN
    status: EventStatus = EventStatus.SCHEDULED
    organizer: str | None = None
    url: str | None = None
    kinds: frozenset[EventKind] = frozenset()
    size_signal: int | None = Field(default=None, ge=0)

    @field_serializer("kinds")
    def _sorted_kinds(self, kinds: frozenset[EventKind]) -> list[str]:
        """
        Serialize kinds in a stable order.

        Parameters:
          kinds: Event kinds.
        Returns:
          Sorted kind values.
        """
        return sorted(k.value for k in kinds)

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        """
        Reject events that end before they start or have half a coordinate.

        Returns:
          The validated model.
        """
        if self.end_utc is not None and self.end_utc < self.start_utc:
            raise ValueError("end_utc precedes start_utc")
        if (self.lat is None) != (self.lon is None):
            raise ValueError("lat and lon must be set together")
        return self


class EventDraft(_EventFields):
    """An event as parsed from a single source record."""

    source_id: str
    native_id: str


class EventSourceRef(_Frozen):
    """Provenance link from a published event to a source record."""

    source_id: str
    native_id: str
    url: str | None = None


class Event(_EventFields):
    """A resolved event as published under the versioned API."""

    schema_version: str = SCHEMA_VERSION
    event_id: str
    first_seen: AwareDatetime
    last_seen: AwareDatetime
    sources: tuple[EventSourceRef, ...] = ()

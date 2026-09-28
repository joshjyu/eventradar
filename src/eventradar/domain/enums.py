"""Enumerations used by the event domain model."""

from enum import StrEnum


class EventKind(StrEnum):
    """Coarse event category, independent of topic."""

    HACKATHON = "hackathon"
    CONFERENCE = "conference"
    MEETUP = "meetup"
    WORKSHOP = "workshop"
    OTHER = "other"


class AttendanceMode(StrEnum):
    """Mirrors schema.org EventAttendanceModeEnumeration."""

    IN_PERSON = "in_person"
    ONLINE = "online"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class EventStatus(StrEnum):
    """Mirrors schema.org EventStatusType."""

    SCHEDULED = "scheduled"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"

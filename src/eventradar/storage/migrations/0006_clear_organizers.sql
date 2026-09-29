-- One-off work a run performs after migrating, then deletes.
CREATE TABLE maintenance_tasks (
    task         TEXT PRIMARY KEY,
    requested_at TEXT NOT NULL
);

-- Organizer names partly came from iCal ORGANIZER fields, which often name
-- a person. Clear them all; the requested reparse restores organization
-- names from the sources that provide them.
UPDATE events SET organizer = NULL;
INSERT INTO maintenance_tasks VALUES (
    'reparse', strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
);

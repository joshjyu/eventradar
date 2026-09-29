-- Kinds assigned by title rules, recomputed each run; published kinds are
-- the union of these and the kinds sources report.
ALTER TABLE events ADD COLUMN rule_kinds TEXT NOT NULL DEFAULT '[]';

-- Health tracking per source: consecutive unhealthy runs, whether an alert
-- is open, and when a disabled source was last probed.
ALTER TABLE source_state ADD COLUMN unhealthy_runs INTEGER NOT NULL DEFAULT 0;
ALTER TABLE source_state ADD COLUMN alerted INTEGER NOT NULL DEFAULT 0;
ALTER TABLE source_state ADD COLUMN since TEXT;
ALTER TABLE source_state ADD COLUMN last_probe TEXT;

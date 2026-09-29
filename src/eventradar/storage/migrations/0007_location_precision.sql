-- How the enrich stage located an event: 'address', 'postal' (ZIP
-- centroid), or 'place' (city centroid). NULL when a source supplied the
-- coordinates, or for events located before this column existed.
ALTER TABLE events ADD COLUMN geo_precision TEXT;

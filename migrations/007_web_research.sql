-- Artist facts found by web research (user request, 2026-09-29) for artists MusicBrainz couldn't
-- match. They get their own status/source so they stay distinguishable from MusicBrainz matches
-- and from the user's own decisions, and source_url records the page each one came from.

ALTER TABLE artists DROP CONSTRAINT artists_match_status_check,
    ADD CONSTRAINT artists_match_status_check CHECK (match_status IN ('auto', 'ambiguous', 'not_found', 'manual', 'web'));
ALTER TABLE artists DROP CONSTRAINT artists_demographics_source_check,
    ADD CONSTRAINT artists_demographics_source_check CHECK (demographics_source IN ('musicbrainz', 'wikidata', 'user', 'web'));
ALTER TABLE artists ADD COLUMN source_url text;

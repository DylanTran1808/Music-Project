-- Artist demographics, looked up on MusicBrainz (music_project.db.artists.enrich_artists).
-- Unclear matches keep their MusicBrainz + Wikidata candidates for the user to decide;
-- 'manual' rows (the user's choice) are never overwritten by a lookup.

ALTER TABLE artists
    ADD COLUMN mb_id               text,
    ADD COLUMN wikidata_id         text,
    ADD COLUMN artist_type         text CHECK (artist_type IN ('person', 'group', 'orchestra', 'choir', 'character', 'other')),
    ADD COLUMN gender              text CHECK (gender IN ('female', 'male', 'non_binary', 'other', 'not_applicable')),
    ADD COLUMN country             text CHECK (country ~ '^[A-Z]{2}$'),     -- ISO 3166-1 alpha-2, like users.country
    ADD COLUMN area                text,                                   -- where they're based
    ADD COLUMN birth_area          text,                                   -- birthplace, or where a group formed
    ADD COLUMN begin_date          date,                                   -- birth / formation (partial dates -> 1st)
    ADD COLUMN end_date            date,                                   -- death / split
    ADD COLUMN release_languages   text[] NOT NULL DEFAULT '{}',           -- ISO 639-1, most frequent first
    ADD COLUMN first_release_year  smallint,                               -- earliest release MusicBrainz knows
    ADD COLUMN tags                text[] NOT NULL DEFAULT '{}',
    ADD COLUMN match_status        text CHECK (match_status IN ('auto', 'ambiguous', 'not_found', 'manual')),
    ADD COLUMN demographics_source text CHECK (demographics_source IN ('musicbrainz', 'wikidata', 'user')),
    ADD COLUMN candidates          jsonb,                                  -- {"musicbrainz": [...], "wikidata": [...]}
    ADD COLUMN fetched_at          timestamptz;                            -- NULL = not looked up yet

-- Age is never stored (it would go stale): persons get age (at death if they died),
-- groups and other ensembles get years_active.
CREATE VIEW artist_demographics AS
SELECT a.*,
       CASE WHEN a.artist_type = 'person'
            THEN extract(year FROM age(coalesce(a.end_date, current_date), a.begin_date))::int END AS age,
       CASE WHEN a.artist_type IS DISTINCT FROM 'person'
            THEN extract(year FROM age(coalesce(a.end_date, current_date), a.begin_date))::int END AS years_active
FROM artists a;

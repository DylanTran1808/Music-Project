-- Spotify extended streaming history: one row per play, per account.
-- Loads merge (a partial or overlapping export only adds the plays not seen before),
-- so the natural key has to tell apart real plays that end in the same second:
-- a quick skip and the replay after it share ts + uri but differ in ms_played /
-- reasons, and two sub-second plays differ only in offline_timestamp.
-- ip_addr and the other PII fields are dropped by spotify_insights.enrich_events.
-- Derived flags (stream >= 30 s, forward skip, session) are computed at query time.

CREATE TABLE listening_events (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id        bigint NOT NULL REFERENCES user_accounts ON DELETE RESTRICT,
    track_id          bigint REFERENCES tracks ON DELETE RESTRICT,   -- NULL for podcasts / audiobooks
    kind              text NOT NULL CHECK (kind IN ('music', 'podcast', 'audiobook', 'unknown')),
    ts                timestamptz NOT NULL,                          -- when playback ended
    ms_played         integer NOT NULL CHECK (ms_played >= 0),
    spotify_uri       text,                                          -- track, episode or audiobook URI
    title             text,                                          -- track / episode / audiobook title
    artist_credit     text,                                          -- album artist, or the show name
    album             text,
    reason_start      text NOT NULL,
    reason_end        text NOT NULL,
    shuffle           boolean NOT NULL,
    offline           boolean NOT NULL,
    incognito         boolean NOT NULL,
    offline_timestamp bigint,
    platform          text,
    conn_country      text,
    CONSTRAINT listening_events_natural_key UNIQUE NULLS NOT DISTINCT
        (account_id, ts, spotify_uri, ms_played, reason_start, reason_end, offline_timestamp)
);
CREATE INDEX ON listening_events (track_id);

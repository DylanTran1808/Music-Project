-- Core schema: people + demographics, their service accounts, and the shared
-- artist/track catalogue that Apple Music and Spotify data both point at.

CREATE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END $$;

-- One row per real person. Demographics are optional and entered by hand.
-- Age is derived from birth_year at query time; the connector also rejects future years.
CREATE TABLE users (
    id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    handle               text NOT NULL UNIQUE CHECK (handle ~ '^[a-z0-9_]+$'),
    display_name         text,
    gender               text CHECK (gender IN ('female', 'male', 'non_binary', 'other', 'prefer_not_to_say')),
    birth_year           smallint CHECK (birth_year BETWEEN 1900 AND 2100),
    city                 text,
    country              text CHECK (country ~ '^[A-Z]{2}$'),          -- ISO 3166-1 alpha-2, same as artist data
    timezone             text,                                         -- IANA name, e.g. Asia/Ho_Chi_Minh
    occupation           text,
    education_level      text,
    native_language      text CHECK (native_language ~ '^[a-z]{2,3}$'), -- ISO 639 code, e.g. vi
    languages            text[] NOT NULL DEFAULT '{}',                 -- other languages spoken
    musical_background   text CHECK (musical_background IN
                             ('none', 'listener_only', 'self_taught', 'formal_training', 'professional')),
    instruments          text[] NOT NULL DEFAULT '{}',
    music_training_years smallint CHECK (music_training_years BETWEEN 0 AND 100),
    favorite_genres      text[] NOT NULL DEFAULT '{}',
    notes                text,
    extra                jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(extra) = 'object'),
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- A person's login on one service. Someone moving Spotify -> Apple Music gets a second row.
CREATE TABLE user_accounts (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         bigint NOT NULL REFERENCES users ON DELETE RESTRICT,
    source          text NOT NULL CHECK (source IN ('apple_music', 'spotify')),
    source_username text NOT NULL,                                     -- name used in the HF file paths
    active_from     date,
    active_to       date,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, source_username),
    CHECK (active_to >= active_from)
);
CREATE INDEX ON user_accounts (user_id);

CREATE TABLE artists (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name       text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX artists_name_key ON artists (lower(name));

-- One row per song across sources. match_key = primary artist | base title, casefolded
-- (track_key in analysis/apple_insights.py and spotify_insights.py).
CREATE TABLE tracks (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    match_key    text NOT NULL UNIQUE,
    title        text NOT NULL,
    album        text,
    duration_ms  integer CHECK (duration_ms > 0),
    genre        text,
    release_year smallint,
    lang         text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- Credited artists in order; position 0 is the primary artist.
CREATE TABLE track_artists (
    track_id  bigint NOT NULL REFERENCES tracks ON DELETE CASCADE,
    artist_id bigint NOT NULL REFERENCES artists ON DELETE RESTRICT,
    position  smallint NOT NULL CHECK (position >= 0),
    PRIMARY KEY (track_id, position),
    UNIQUE (track_id, artist_id)
);
CREATE INDEX ON track_artists (artist_id);

-- Source ids (Spotify track URI, Apple persistent id) for exact matching.
CREATE TABLE track_external_ids (
    source      text NOT NULL CHECK (source IN ('apple_music', 'spotify')),
    external_id text NOT NULL,
    track_id    bigint NOT NULL REFERENCES tracks ON DELETE CASCADE,
    PRIMARY KEY (source, external_id)
);
CREATE INDEX ON track_external_ids (track_id);

-- One row per load of an export; hf_path stays NULL until the file is pushed to Hugging Face.
CREATE TABLE ingest_runs (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES user_accounts ON DELETE RESTRICT,
    file_hash  text NOT NULL,
    hf_path    text,
    row_count  integer,
    loaded_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON ingest_runs (account_id);

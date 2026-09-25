-- Apple Music library exports (Library.xml): one row per library item, per account.
-- Duplicate items of the same song are kept as separate rows pointing at one track,
-- so play counts add up instead of overwriting each other.

CREATE TABLE library_items (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id          bigint NOT NULL REFERENCES user_accounts ON DELETE RESTRICT,
    track_id            bigint NOT NULL REFERENCES tracks ON DELETE RESTRICT,
    apple_persistent_id text NOT NULL,              -- only unique within one library
    title               text,                       -- as in the library, e.g. "Hit (Remix)"
    artist_credit       text,                       -- raw credit, e.g. "A & C"
    album               text,
    play_count          integer NOT NULL DEFAULT 0 CHECK (play_count >= 0),
    skip_count          integer NOT NULL DEFAULT 0 CHECK (skip_count >= 0),
    loved               boolean NOT NULL DEFAULT false,
    playlist_only       boolean NOT NULL DEFAULT false,
    date_added          timestamptz,
    last_played         timestamptz,
    last_skipped        timestamptz,
    version_tags        text[] NOT NULL DEFAULT '{}',
    UNIQUE (account_id, apple_persistent_id)
);
CREATE INDEX ON library_items (track_id);

CREATE TABLE playlists (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id          bigint NOT NULL REFERENCES user_accounts ON DELETE RESTRICT,
    apple_persistent_id text NOT NULL,
    name                text,
    description         text,
    is_user_curated     boolean NOT NULL,           -- false for built-in, folder and smart playlists
    UNIQUE (account_id, apple_persistent_id)
);

CREATE TABLE playlist_tracks (
    playlist_id     bigint NOT NULL REFERENCES playlists ON DELETE CASCADE,
    position        integer NOT NULL CHECK (position >= 0),
    library_item_id bigint NOT NULL REFERENCES library_items ON DELETE CASCADE,
    PRIMARY KEY (playlist_id, position)
);
CREATE INDEX ON playlist_tracks (library_item_id);

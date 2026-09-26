-- tracks become recordings: "Hit" and "Hit (Slowed + Reverb)" sound different, so audio
-- analysis needs them apart. match_key is now the recording key
--     primary artist | base title [| v:<audio-changing version tags>]
-- and song_key (primary artist | base title) groups a song's versions.
-- Rows loaded before this migration were keyed by song, so on an existing DB reload the
-- exports (scripts/backfill.py on a fresh database) to split them.

ALTER TABLE tracks ADD COLUMN song_key text;
UPDATE tracks SET song_key = match_key;
ALTER TABLE tracks ALTER COLUMN song_key SET NOT NULL;
CREATE INDEX ON tracks (song_key);

# Tasks: `p_music` database, intake app, connector + query tools

See `tasks/plan.md` for decisions, risks, and open questions.
Test command (all tasks): `uv run python tests/<file>.py`. No build step. `uv sync` must succeed.

---

## Task 1: DB connection + migration runner ✅

_Done 2026-09-25. No `000_init.sql`: the runner creates `schema_migrations` itself._

**Description:** Add `music_project/db/__init__.py` with `connect()` (reads `DATABASE_URL` from `.env`)
and `migrate(conn)`, which applies `migrations/*.sql` in order, one transaction per file, and records
each file in `schema_migrations`. Create the `p_music` database.

**Acceptance criteria:**
- [x] `uv run python -m music_project.db migrate` applies pending files
- [x] Second run applies nothing and exits 0
- [x] A failing migration rolls back its own file and isn't recorded

**Verification:**
- [x] Tests pass: `uv run python tests/test_db.py` (creates/drops `p_music_test`)
- [x] Manual check: `psql p_music -c '\dt'` shows `schema_migrations`

**Dependencies:** None

**Files likely touched:**
- `src/music_project/db/__init__.py`, `src/music_project/db/__main__.py`
- `tests/test_db.py`
- `.env` (add `DATABASE_URL`, local only)

**Estimated scope:** S

---

## Task 2: Core schema (people, accounts, demographics, tracks) ✅

_Done 2026-09-25. Extra decisions: `country` is ISO alpha-2, `native_language` an ISO 639 code, array columns default to `{}`, `birth_year` DB range 1900–2100 (future years rejected in the T3 connector), `updated_at` maintained by trigger, `extra` must be a JSON object._

**Description:** `migrations/001_core.sql`:
- `users`:
  - identity: `handle` unique (`[a-z0-9_]+`), `display_name`
  - `gender`: CHECK in `female|male|non_binary|other|prefer_not_to_say`
  - `birth_year`, `city`, `country`, `timezone`, `occupation`, `education_level`
  - `native_language`, `languages text[]`
  - musical background:
    - `musical_background`: CHECK in `none|listener_only|self_taught|formal_training|professional`
    - `instruments text[]`
    - `music_training_years int` (>= 0)
  - `favorite_genres text[]`, `notes`, `extra jsonb`, `created_at`, `updated_at`
- `user_accounts`: `id`, `user_id`, `source` (CHECK `apple_music|spotify`), `source_username`,
  `active_from`, `active_to`, `unique (source, source_username)`
- `artists` (unique on casefolded name)
- `tracks`: `match_key` unique, `title`, `album`, `duration_ms`, `genre`, `release_year`, `lang`
- `track_artists (track_id, artist_id, position)`
- `track_external_ids (source, external_id → track_id)`
- `ingest_runs (account_id, file_hash, hf_path, loaded_at, row_count)`

**Acceptance criteria:**
- [x] Migration applies on an empty DB; FKs, uniques and CHECKs are in place
- [x] Rejected: out-of-range `birth_year`, a bad handle, a gender or musical-background value not in its list, a duplicate `(source, source_username)`
- [x] One user can own a Spotify and an Apple account; deleting a user is blocked while accounts have data (`ON DELETE RESTRICT`)

**Verification:**
- [x] Tests pass: `tests/test_db.py` asserts each rejection above
- [x] Manual check: `psql p_music -c '\d users' -c '\d user_accounts'`

**Dependencies:** T1

**Files likely touched:**
- `migrations/001_core.sql`
- `tests/test_db.py`

**Estimated scope:** S

---

## Checkpoint: Foundation
- [x] `p_music` exists; `migrate` applies twice cleanly
- [x] `tests/test_db.py` passes
- [x] Human reviews `001_core.sql` before any loader is written

---

## Task 3: People + accounts: connector + app Users page ✅

_Done 2026-09-25. Also added: `pycountry` for the country/language lists (reused in T8 for `vie`→`vi`); `.streamlit/config.toml` binds to localhost and turns off usage stats; `tests/test_app.py` drives the real form headlessly (Streamlit AppTest) against `p_music_test`. The manual check ran through AppTest, so `p_music` has no test rows._

**Description:** Connector functions `upsert_user(conn, handle, **demographics)`, `get_user`,
`list_users`, and `add_account(conn, handle, source, source_username, active_from=None)`. Add
`streamlit` to deps. The app's Users page has:
- a demographics form:
  - gender select (fixed list);
  - age **or** birth-year input, stored as `birth_year`;
  - city/country, occupation, education;
  - native language select + other languages multiselect;
  - musical background: level select, instruments multiselect (free entries allowed), years of training;
  - genres multiselect, notes, and a key/value editor for `extra`;
- an accounts table with "add account" (source + username + optional dates).

**Acceptance criteria:**
- [x] Creating then editing a person gives one `users` row with the latest values; entering age 25 in 2026 stores `birth_year = 2001`
- [x] Adding an Apple account to a person who already has a Spotify account works; reusing a taken `(source, username)` shows an error message instead of a stack trace
- [x] `uv run streamlit run app/streamlit_app.py` starts on localhost

**Verification:**
- [x] Tests pass: `tests/test_db.py` covers the upsert round-trip and two accounts on one user
- [x] Manual check: create "testuser" in the app with two accounts, then check the rows in `psql`

**Dependencies:** T2

**Files likely touched:**
- `src/music_project/db/__init__.py`
- `app/streamlit_app.py`
- `pyproject.toml`, `uv.lock`
- `tests/test_db.py`

**Estimated scope:** M

---

## Task 4: Apple Music import (schema + loader + app upload, local) ✅

_Done 2026-09-25. Design change: `library_items` keeps **one row per Apple item** (unique per account + Persistent ID) pointing at the shared track, instead of summing duplicates into one row. Totals add up in queries, and remix/original items keep their own `version_tags`. Apple exports have no catalog id (Persistent IDs are per-library), so `track_external_ids` holds Spotify URIs only. Real data: kien 5,258 plays / 1,472 items (old P-Music had 4,732), 1,198 artists after credit splitting, 33 tracks shared across people. Also: an empty library is refused instead of wiping the previous load; a failed upload never replaces the last good staged file; the app now has file-based pages (`app/pages/`) and a shared `app/common.py`; the cached DB connection is ping-validated so it recovers after a Postgres restart. Manual check: kha uploaded through the app into `p_music` (203 items / 3,524 plays = XML)._

**Description:** `migrations/002_apple.sql`: `library_items (account_id, track_id,
apple_persistent_id, play_count, skip_count, loved, date_added, last_played, version_tags,
playlist_only)`, plus `playlists` and `playlist_tracks`. `load_apple(conn, account_id, xml_path)`
parses with `apple_insights.load_library(path=…)`, upserts artists/tracks/credits/external ids,
**sums duplicate items**, and replaces that account's rows in one transaction with an
`ingest_runs` row. The app's Import page takes a person + Apple account + `Library.xml`, stages
the file in `data/uploads/`, and loads it.

**Acceptance criteria:**
- [x] kien's loaded play total = sum of `Play Count` in `Library_kien.xml` (5,258)
- [x] Loading the same file twice leaves all row counts unchanged
- [x] No `artists.name` is an unsplit multi-artist credit

**Verification:**
- [x] Tests pass: `uv run python tests/test_db_load.py` (synthetic plist with a duplicate item)
- [x] Manual check: upload `data/raw_am/Library_kha.xml` in the app, count matches the XML

**Dependencies:** T3

**Files likely touched:**
- `migrations/002_apple.sql`
- `src/music_project/db/load.py`
- `app/streamlit_app.py`
- `tests/test_db_load.py`

**Estimated scope:** M

---

## Task 5: Spotify import (schema + loader + app upload, local) ✅

_Done 2026-09-25. Design changes: (1) Spotify loads **merge** (insert new plays, skip stored ones) instead of replacing the account's rows. A partial upload (one year's file) would otherwise wipe older history. (2) The natural key is `(account, ts, uri, ms_played, reason_start, reason_end, offline_timestamp)` with NULLS NOT DISTINCT: `(account, ts, uri)` alone would have dropped 39 real plays (a skip and the replay after it end in the same second). (3) A song's catalogue credits are the union over all its entries (applies to Apple too), so "Song (feat. B)" still credits B. (4) A 0 ms duration estimate is stored as unknown. Real data: bhuy 7,472 / viethung 18,731 events (= source after dedup), reload adds 0, 4,202 Spotify URIs, **475 tracks shared between Apple libraries and Spotify listening**. The app accepts the export .zip (only Streaming_History*.json, by basename, so no path traversal) or loose JSONs. Manual check: bhuy uploaded as a zip through the app into `p_music` (7,472 plays)._

**Description:** `migrations/003_spotify.sql`: `listening_events (account_id, track_id null, kind,
ts, ms_played, reason_start, reason_end, shuffle, skipped_fwd, platform, conn_country,
episode_name, incognito)`, unique on `(account_id, ts, coalesce(track uri, episode uri))`.
`load_spotify(conn, account_id, folder)` uses `spotify_insights.load_history(path=…)`. The app
accepts a `.zip` or several `Streaming_History_*.json` files.

**Acceptance criteria:**
- [x] Event count = source rows after `enrich_events` dedup; no `ip_addr` column exists
- [x] Reload is a no-op on counts; overlapping exports don't duplicate events
- [x] Spotify URIs land in `track_external_ids`

**Verification:**
- [x] Tests pass: `tests/test_db_load.py` (synthetic history with a repeated row and a podcast)
- [x] Manual check: upload `data/raw_spot/Spotify_bhuy/` as a zip in the app

**Dependencies:** T3

**Files likely touched:**
- `migrations/003_spotify.sql`
- `src/music_project/db/load.py`
- `app/streamlit_app.py`
- `tests/test_db_load.py`

**Estimated scope:** M

---

## Task 6: Push uploads to Hugging Face ✅

_Done 2026-09-25. `connectors/upload.py` (`push_apple`, `push_spotify`, `PushError`, `PushRefused`) knows nothing about the DB and refuses to overwrite an existing path unless `overwrite=True`. The app passes `overwrite=True` only when this account's own `ingest_runs.hf_path` already records that path (`db.account_pushed` / `db.set_hf_path`), because every teammate has their own local DB and HF is shared. It isn't re-exported from `connectors/__init__` (that would be a circular import with `analysis.spotify_insights._PII`). The token has `repo.write` on the CongtyTuban org, so no new token was needed. Real HF check with throwaway `zz_intake_test` files: two push commits, a second push refused, no `ip_addr` in the pushed Spotify JSON, the Apple XML byte-identical, and both files deleted in one commit (dataset back to 974 files; the 3 commits stay in HF history). App tests push to a fake hub only._

**Description:** Add `push_apple(xml_path, source_username)` and `push_spotify(folder, source_username)`
in `connectors/common.py` (or a small `connectors/upload.py`). They use `HfApi.upload_file` /
`upload_folder` to write the existing layout (`raw_am/Library_<u>.xml`, `raw_spot/Spotify_<u>/`).
Spotify JSONs get the `_PII` fields (incl. `ip_addr`) stripped first. The app calls a push after a
successful DB load and records `ingest_runs.hf_path`. On failure it shows the error and a
"retry push" button.

**Acceptance criteria:**
- [x] Pushed Spotify JSON contains no `ip_addr`; the Apple XML is pushed unchanged
- [x] Pushing to a path owned by a *different* account is refused; the same account re-pushing creates a new HF commit
- [x] Missing write permission / expired token gives a clear message ("token needs write on CongtyTuban/Streaming-data"), and the DB load is kept. **During the build: on this error, stop and ask the user for a new token.**

**Verification:**
- [x] Tests pass: `uv run python tests/test_hf_push.py` (mocked `HfApi`, asserts paths + stripped fields)
- [x] Manual check: upload a small test export for a `testuser` account, see the commit on HF, then delete that test file from HF

**Dependencies:** T4, T5

**Files likely touched:**
- `src/music_project/connectors/upload.py`
- `src/music_project/connectors/__init__.py`
- `app/streamlit_app.py`
- `tests/test_hf_push.py`

**Estimated scope:** M

---

## Task 7: Backfill existing `data/` + cross-source check ✅

_Done 2026-09-26. Core is `db.load.load_data_dir(conn, dir, hf_files)`, which works on any folder laid out like the HF dataset, so teammates can rebuild a local DB from an HF snapshot. `scripts/backfill.py` is the CLI + report. Result in `p_music`: all 5 accounts match the source files (checked independently of the loaders: summed plist Play Count; distinct raw Spotify records minus PII fields); all 5 own their HF paths; the second run's output is identical. Shared tracks: kien~viethung 305, bhuy~viethung 397, bhuy~kien 256. 206 songs merged from > 2 source ids; mostly one song on several Spotify releases, and `ariana grande|intro` is also correct on inspection: all 4 URIs are "intro (end of the world)" plus its extended version. Only 8 tracks have generic titles (intro/outro/interlude...), none wrongly merged, so the match_key is left as is. The 2 account-less test people (`whatisthis`, `whoisthis2`) were deleted on the user's request 2026-09-26._

**Description:** `scripts/backfill.py` creates (or reuses: `kha` + Apple account `kha` and `bhuy` + Spotify account `bhuy` already exist in `p_music` from the T4/T5 manual checks) one person + one account for each file in `data/`
(kien, kha, cuong → Apple; viethung, bhuy → Spotify) and loads them through the T4/T5 loaders.
No HF push, since these files are already there, but **set `ingest_runs.hf_path`** to the existing HF path (`raw_am/Library_<u>.xml`, `raw_spot/Spotify_<u>`) so the account owns it and later app uploads for these people may overwrite it (otherwise T6 refuses them). It prints per-account totals vs source, the
Apple↔Spotify `match_key` overlap per pair, and merge groups with > 2 source ids.

**Acceptance criteria:**
- [x] 5 people, 5 accounts; every printed total equals the source
- [x] Second run: identical counts; existing demographics are left untouched
- [x] Overlap report printed

**Verification:**
- [x] Manual check: run twice, diff the output

**Dependencies:** T4, T5

**Files likely touched:**
- `scripts/backfill.py`

**Estimated scope:** S

---

## Task 7b: Recording-level tracks (keep versions apart, group by song) ✅

_Done 2026-09-26. Also fixed in `apple_insights._VERSION_TAGS`: "Extended Mix" / "Original Mix" no longer count as remixes. `p_music` was rebuilt from scratch (backup in `data/backups/p_music_before_T7b_*.dump`), which also proved a fresh DB rebuilds from the repo (migrations 001–004 + backfill, ~5 s). Result: 4,924 recordings of 4,816 songs, all totals match, song-level sharing unchanged (kien~viethung 305). Known limit: two different remixes of one song by the same artist ("Legends Never Die - (Remix)" vs "(Alan Walker Remix)") still share a recording, since the remixer's name isn't part of the key._

**Description:** Requested by the user on 2026-09-26: "Hit" and "Hit (Remix)" sound different, and future
audio analysis needs one row per recording. `migrations/004_recordings.sql` adds `tracks.song_key`
(artist | base title, the old match_key) with an index. `tracks.match_key` becomes the **recording
key**: song_key plus the audio-changing version tags, e.g. `sidewalks and skeletons|goth|v:reverb+slowed`.
Every version tag counts except `ost` (a label) and `remaster` (same performance); "feat. X" is not a
version, so it still merges. `extended` and `edit` are added to `apple_insights._VERSION_TAGS`
("Extended Mix", "Radio Edit"). Both loaders key the catalogue by recording. `p_music` is rebuilt
(backup → drop → migrate → backfill); it holds no hand-entered data yet. The backfill reports sharing at
song level.

**Acceptance criteria:**
- [x] "Hit", "Hit (feat. B)" and "Hit - From X Soundtrack" share one recording; "Hit (Remix)", "Hit (Slowed + Reverb)" and "Hit (Extended Mix)" are separate recordings; all have `song_key = 'a|hit'`
- [x] Same on the Spotify side ("Song (Sped Up)" is its own recording); the same original on Apple and Spotify is still one row
- [x] Rebuilt `p_music`: backfill totals all match, ~4,930 recordings, song-level sharing unchanged (kien~viethung 305)

**Verification:**
- [x] Tests pass: `tests/test_db_load.py`, `tests/test_apple_insights.py`, and the full suite
- [x] Manual check: `Goth` / `Goth (Slowed + Reverb)` / `Goth (Sped up + Reverb)` are 3 recordings of one song

**Dependencies:** T7

**Files likely touched:**
- `migrations/004_recordings.sql`
- `src/music_project/db/load.py`, `src/music_project/analysis/apple_insights.py`
- `scripts/backfill.py`
- `tests/test_db_load.py`

**Estimated scope:** M

---

## Checkpoint: Intake
- [ ] End-to-end in the app: new person → demographics → accounts → uploads → DB rows + HF files
- [ ] Backfill totals match; all tests pass
- [ ] Human review before query work

---

## Task 8: Artist demographics from MusicBrainz ✅

_Done 2026-09-29. MusicBrainz matched 56% of artists / 70% of plays automatically; the top unmatched ones and all TV-show songs were researched on the web (see notes below); the remaining 755 in `data/artist_review.md` are the user's to decide._

**Description:** `migrations/005_artist_demographics.sql` adds these columns to `artists`:
`mb_id`, `artist_type`, `gender` (users' fixed list + `not_applicable`), `country` (ISO alpha-2),
`area`, `birth_area`, `begin_date`, `end_date`, `release_languages text[]` (ISO 639-1, most frequent
first), `first_release_year`, `tags text[]`, `match_status` (CHECK `auto|ambiguous|not_found|manual`),
`demographics_source` (CHECK `musicbrainz|wikidata|user`), `wikidata_id`, `candidates jsonb`
(MusicBrainz + Wikidata hits kept for review), `fetched_at`. Plus a view `artist_demographics`
that adds computed `age` (persons: years since birth, capped at death) and `years_active` (groups).

`connectors/wikidata.py` has `search_wikidata(name)`: `wbsearchentities`, then `wbgetentities` for
P31/P21/P27/P569/P19/P571/P434. It runs only for `ambiguous` / `not_found` artists, and its hits are
stored as candidates, never applied.

`connectors/musicbrainz.py` has:
- `search_artist(name)`: 1 req/s throttle, User-Agent built from `MB_CONTACT`;
- `artist_releases(mb_id)`: one release browse, giving `release_languages` (normalised `vie`→`vi` etc.),
  `first_release_year`, and the release titles for the album tie-break;
- `pick_match(name, candidates, album_titles)`: a pure function implementing the match rule in `plan.md`;
- `enrich_artists(conn, limit=None)`: looks up artists where `fetched_at IS NULL` and never
  touches `manual` rows.

Also: a `scripts/enrich_artists.py` CLI and `requests` declared in `pyproject.toml`. _Changed 2026-09-26:_ the in-app lookup moved to T10's Artists page as a "Look up N new artists" button, because a new person's upload can bring ~1,000 artists (~3 s each), too long to run inside one page request.

**Acceptance criteria:**
- [x] `pick_match`: exact unique hit → `auto`; two close hits with no album overlap → `ambiguous`; album overlap breaks a tie; no hits → `not_found`
- [x] Re-running after an interruption continues where it stopped; a `manual` row is unchanged after a re-run
- [x] From fixtures: a person gets gender/country/birth date, `release_languages` in frequency order with ISO 639-1 codes (`{vi,en}` for 11 `vie` + 2 `eng` releases), and `first_release_year`; a group gets `gender = 'not_applicable'`. `artist_demographics.age` is correct for a living, a deceased, and an undated artist (NULL)
- [x] Full run over the backfilled artists prints the auto / ambiguous / not_found split, weighted by plays too (coverage of what people actually listen to)
- [x] Unmatched artists are written to `data/artist_review.md` (per artist: plays, our album titles, MusicBrainz candidates, Wikidata candidates with description/gender/country/birth). **Show it to the user and apply only their choices** (`set_artist_demographics`, saved as `manual`)

**Verification:**
- [x] Tests pass: `uv run python tests/test_musicbrainz.py` (recorded JSON fixtures, no network)
- [x] Manual check: `select name, artist_type, gender, country, begin_date from artists where match_status='auto' order by random() limit 20`, then spot-check against MusicBrainz

**Dependencies:** T7b

**Files likely touched:**
- `migrations/005_artist_demographics.sql`
- `src/music_project/connectors/musicbrainz.py`, `src/music_project/connectors/wikidata.py`
- `scripts/enrich_artists.py`
- `app/streamlit_app.py`, `pyproject.toml`
- `tests/test_musicbrainz.py` (covers both connectors with fixtures)
- `migrations/006_language_groups.sql`, `src/music_project/db/languages.py` (vi/en scope, 2026-09-28)
- `migrations/007_web_research.sql` (`web` status/source + `source_url`, 2026-09-29): artists MusicBrainz can't match are researched on the web; each saved fact cites its page. Batch 1 (top 20 by plays) done: 19 Vietnamese, Yasuharu Takanashi Japanese → excluded; wrong auto-matches for Minh Hiếu / Lâm Anh fixed.
- `overrides/` + `src/music_project/db/overrides.py` (2026-09-29): committed catalogue fixes re-applied by `scripts/backfill.py`. `credits.json`: 83 songs the release credits to a TV show (RAP VIỆT, ANH TRAI / EM XINH / TINH HÀ "SAY HI", Anh Trai Vượt Ngàn Chông Gai, Chị Đẹp Đạp Gió Rẽ Sóng, Tân Binh Toàn Năng, The Masked Singer, Biển Của Hy Vọng), with performers first and the show last, marked as a `tv show` programme. 24 were researched per song on the web, the other 59 already named their performers (feat.). "TOGETHER WE SHINE" (cast theme song) stays credited to the show. `artists.json`: the 31 web-researched artist facts. The rest of the review list (755 artists) is left to the user.

**Estimated scope:** M

---

## Task 9: Query tools + `user_track_stats` view

**Description:** `migrations/007_stats_view.sql`: `user_track_stats` view per **person** × track
(Apple plays/skips/loved, Spotify streams/skips/ms, first/last played, sources). Functions in
`music_project/db/queries.py`, each returning a DataFrame, all with parameterized SQL, **defaulting to tracks/artists with `lang_group` in ('vi', 'en')** (`languages=None` for everything):
- `users()`, `user_profile(handle)` (demographics + computed `age` + accounts + per-source counts)
- `top_tracks(handle, source=None, n=20, level="song")` (`level="recording"` keeps versions apart), `top_artists(handle, n=20)`
- `listening_by_month(handle)`, split by source, so a service switch is visible
- `shared_tracks(a, b)`, `search_tracks(text)`
- `users_by(gender=, age_min=, age_max=, city=, country=, native_language=, musical_background=, instrument=, genre=)`
- artist demographics:
  - `artist_profile(name)`: all demographic fields + computed age / years active
  - `listening_by_artist_attr(handle, attr)`: share of a person's plays by artist `gender` / `country` / `artist_type` / age band / release language (a multi-language artist's plays are split evenly across their languages)
  - `artists_by(gender=, country=, release_language=, age_min=, age_max=)`
  - `artists_needing_review()`: `ambiguous` + `not_found`, most-played first
- `sql(query, params)`: ad-hoc, run in a read-only transaction

**Acceptance criteria:**
- [ ] Each function returns the expected rows on the synthetic fixture, including a person with a Spotify then an Apple account (merged in `top_tracks`, split by source in `listening_by_month`)
- [ ] `sql()` rejects writes
- [ ] `users_by(age_min=20, age_max=30)` filters using age computed from `birth_year`; `listening_by_artist_attr` shares sum to 1 with unknowns as their own row

**Verification:**
- [ ] Tests pass: `uv run python tests/test_queries.py`
- [ ] Manual check: `top_tracks("kien")` in a notebook

**Dependencies:** T8

**Files likely touched:**
- `migrations/007_stats_view.sql`
- `src/music_project/db/queries.py`
- `tests/test_queries.py`

**Estimated scope:** M

---

## Task 10: App Explore + Artists pages

**Description:**
- **Explore page**:
  - pick a person to see their profile, top tracks/artists (source toggle), a monthly chart
    split by source, and the artist-demographic mix (gender / country / type of the artists they play);
  - compare two people (shared tracks);
  - filter people by demographics.
- **Artists page**: a review queue from `artists_needing_review()`. For each artist it shows
  the MusicBrainz and Wikidata candidates in two columns (name, description/disambiguation,
  type, gender, country, birth date/place, release languages). You pick one or type the values yourself, and the
  row is saved with `match_status = 'manual'` and the matching `demographics_source` (via
  `set_artist_demographics` in the connector).

Reads go only through T9 functions, with no SQL in the app.

**Acceptance criteria:**
- [ ] Every read widget is backed by a `queries.py` function
- [ ] A person with only one source, or no data yet, renders without errors
- [ ] Resolving an ambiguous artist in the app sets `manual`, and a later `enrich_artists` run leaves it unchanged

**Verification:**
- [ ] Manual check: browse kien (Apple), bhuy (Spotify), and a fresh person; resolve one ambiguous artist

**Dependencies:** T9

**Files likely touched:**
- `app/streamlit_app.py`
- `src/music_project/db/__init__.py` (`set_artist_demographics`)

**Estimated scope:** M

---

## Task 11: Drop old DBs + docs

**Description:** Print `P-Music` row counts next to `p_music`. Back up with
`pg_dump p_music -t users -t user_accounts -t artists`. Then `dropdb` `P-Music`, `MusicDb`, and
`MUST_RAG` (approved in principle; a final yes is still needed at run time). Update
`docs/STRUCTURE.md` and README: `DATABASE_URL`, HF write token, `MB_CONTACT`, `migrate`,
backfill, `enrich_artists`, running the app, backup command.
Remove the stale §6 layout and the `parse_am.py` example.

**Acceptance criteria:**
- [ ] Backup file exists before any drop
- [ ] `psql -l` shows `p_music` and no old project DBs
- [ ] README steps work on a fresh clone

**Verification:**
- [ ] Manual check: follow README from scratch

**Dependencies:** T10

**Files likely touched:**
- `README.md`, `docs/STRUCTURE.md`

**Estimated scope:** S

---

## Checkpoint: Complete
- [ ] All acceptance criteria met, all tests pass
- [ ] Human review

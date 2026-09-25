# Implementation Plan: `p_music` database, intake app, connector + query tools

## Overview

Replace the current Postgres databases with one lean database, `p_music`, sized to
what the project does today: people and their demographics, their Apple Music
libraries, their Spotify listening history, and demographic data about the artists
they listen to. On top of it:

- a **connector** (`music_project.db`) that opens connections, applies migrations,
  saves user metadata, and loads Apple/Spotify exports;
- an **artist enrichment connector** that looks up artist demographics on MusicBrainz;
- **query tools** (`music_project.db.queries`) that return pandas DataFrames for
  notebooks and the app;
- a **small Streamlit app** to create/edit a person's metadata, upload their Apple
  `Library.xml` and/or Spotify export (pushed to the Hugging Face dataset and loaded
  into the DB), browse results, and fix artist matches.

Revised 2026-09-25 (third pass). Changes from the user's answers:
- `MUST_RAG` is dropped;
- user demographics now include native language and musical background, and gender is a fixed list;
- a person can have accounts on several services over time;
- app uploads go to the HF dataset;
- artist demographics are looked up and stored.

Defects in the current `P-Music` that the new schema has to fix:
1. ~500 of kien's plays lost (DB 4,732 vs source 5,258): duplicate library items overwrite instead of summing.
2. `apple_music_tracks` has no `user_id`, so per-user `date_added` / `playlist_only` can't be attributed.
3. Track identity = exact (name, artist_id, album_id): Apple and Spotify never match.
4. 324 / 1,001 artists are collab strings ("BigDaddy & GREY D").
5. `listening_events` has no natural key (re-load duplicates rows).

## Current databases (local Postgres 18)

| DB | Contents | Plan |
|---|---|---|
| `P-Music` | 17 tables; only `users`, `artists`, `albums`, `tracks`, `apple_music_tracks`, `user_track_stats` have rows, all derived from `data/raw_am` | Drop after `p_music` is verified (T11) |
| `MusicDb` | empty | Drop (T11) |
| `MUST_RAG` | one empty `chunks` table | Drop (T11) |

## Architecture Decisions

- **One new DB `p_music`** (lowercase, no quoting). Old DBs untouched until T11.
- **Numbered SQL migrations** in `migrations/NNN_name.sql` with a ~30-line runner that records
  applied files in `schema_migrations`. Required, because manually entered demographics and
  manual artist corrections can't be re-derived, so schema changes must never be
  "drop and reload". No ORM or Alembic.
- **`DATABASE_URL` in `.env`**, read with `python-dotenv`. Driver: `psycopg2-binary`. Both are already dependencies.
- **Person vs account split** (for people who move between services):
  - `users` = one real person, holding the demographics.
  - `user_accounts (id, user_id, source, source_username, active_from, active_to)` = one
    service account. A person who moves from Spotify to Apple Music gets a second account on
    the same person, so their history stays one timeline.
  - Source data references `account_id`, and queries by person merge both sources.
  - `unique (source, source_username)`, so an export always resolves to exactly one person.
- **User demographics on `users`**, all optional and entered manually:
  - `gender`: fixed list enforced by a CHECK constraint: `female`, `male`, `non_binary`, `other`, `prefer_not_to_say`.
  - `birth_year`: the app accepts an age or a year, and the query tools compute `age`. A
    stored age would go stale every year.
  - `city`, `country`, `occupation`, `education_level`, `timezone`.
  - `native_language` (one language), plus `languages text[]` for the other languages they speak.
  - Musical background, stored in three columns:
    - `musical_background`: fixed level `none`, `listener_only`, `self_taught`, `formal_training`, `professional`;
    - `instruments text[]`: e.g. piano, guitar, vocals, production;
    - `music_training_years int`.

    Details beyond these go in `notes`.
  - `favorite_genres text[]`, `notes`, and `extra jsonb` for any further fields (key/value editor in the app).
- **Artist demographics come from MusicBrainz** (free, no API key, good coverage in a
  spot check: "Sơn Tùng M-TP" → Person, male, VN, born 1994-07-05 in Thái Bình).
  - Artist demographic fields:

    | Field | Meaning | From |
    |---|---|---|
    | `artist_type` | person / group / orchestra / choir / character / other | MB search |
    | `gender` | same fixed list as users (+ `not_applicable` for groups) | MB search |
    | `country` | ISO alpha-2, same format as `users.country` | MB search |
    | `area`, `birth_area` | where they're based; birth city (or where a group formed) | MB search |
    | `begin_date`, `end_date` | birth / formation, death / split | MB search |
    | **age** | not stored; computed at query time: persons = years since `begin_date` (up to `end_date` if deceased); groups = years active | computed |
    | `release_languages text[]` | languages of their releases, most frequent first, e.g. `{vi,en}` | MB releases |
    | `first_release_year` | earliest release *on MusicBrainz*: a lower bound on the debut, not the true debut (MB listed Sơn Tùng's first release as 2016; he debuted in 2012) | MB releases |
    | `tags text[]` | genres | MB search |

  - Language codes are normalised to ISO 639-1 (`vie` → `vi`), with a small map for the
    languages we see; others keep their 3-letter code. So `release_languages` compares
    directly with `users.native_language`.
  - Two calls per matched artist: a search, then one release browse (also used for the
    album tie-break). ~1,000 artists at MusicBrainz's 1 request/second limit ≈ 35 min for the
    first run. It's resumable and skips artists already looked up, and it runs inside the app
    too, for artists added by a new upload.
  - Artists MusicBrainz lists with no release language keep `release_languages = '{}'`
    (unknown). We don't guess from titles, because `script_lang` only tells Latin script apart
    from others, not English from Vietnamese written without diacritics.
  - Match rule:
    - accept if the top hit scores 100 and the next hit scores clearly lower;
    - otherwise break the tie with one extra call that checks the candidate's release titles
      against the albums we have for that artist;
    - otherwise store `match_status = 'ambiguous'` with the candidate ids, for review in the app.
  - `match_status` is one of `auto`, `ambiguous`, `not_found`, `manual`. **Manual edits are
    never overwritten** by a re-run.
  - **Anything not auto-matched is decided by the user, never guessed.** For each
    `ambiguous` / `not_found` artist, the tool also searches **Wikidata** and shows both
    candidate lists side by side. Wikidata fields used:
    - label + description (e.g. "Vietnamese singer" vs "Vietnamese prince");
    - instance of (P31), gender (P21), citizenship (P27);
    - birth date/place (P569/P19), inception (P571), MusicBrainz ID (P434).

    The user picks a MusicBrainz hit, a Wikidata hit, or types the values; the row is saved
    as `manual`, with `demographics_source` set to `musicbrainz`, `wikidata` or `user`.
    Wikidata is only queried for these review cases, never to auto-fill.
    - In the **app**: the Artists page review queue.
    - **During the build** (T8 backfill run): the unmatched artists are written to a review
      report (`data/artist_review.md`: MusicBrainz + Wikidata candidates per artist, most-played
      first) and **shown to the user to decide**, instead of being filled in automatically.
  - The User-Agent contact comes from `MB_CONTACT` in `.env`. MusicBrainz requires one, and
    no personal email is hardcoded.
  - Uses `requests`: already used by `itunes_preview.py`, now declared in `pyproject.toml`.
- **Canonical track identity = `tracks.match_key`** (primary artist + base title, casefolded).
  Both insight modules already compute this as `track_key`. Source IDs go in `track_external_ids`.
- **Credits in `track_artists (track_id, artist_id, position)`**, split with the existing
  `split_artists`. Without this, collab strings would each be looked up as one "artist".
- **`user_track_stats` is a SQL view**, not a table, so it can't drift.
- **Loads are idempotent**: each load writes an `ingest_runs` row and replaces that account's rows in one transaction.
- **Upload flow in the app**:
  1. parse locally (bad files never leave the machine);
  2. load into the DB;
  3. push to HF at `raw_am/Library_<source_username>.xml` or `raw_spot/Spotify_<source_username>/…`;
  4. look up any new artists.
  - An existing HF path is never overwritten by a different account. A re-push by the same
    account is a new HF commit, so history is kept.
  - Spotify JSONs have `ip_addr` (and the other `_PII` fields) stripped before the push.
    Demographics never go to HF.
  - **If the HF push fails with an auth/permission error, stop and ask the user for a new
    token.** They generate a new fine-grained token themselves; code never works around it.
- **App = Streamlit** (`app/streamlit_app.py`, one new dependency). It runs on localhost only, with no auth.
- **Query tools are plain functions** returning DataFrames, with parameterized SQL only.
- **Tests are plain `assert` scripts** (existing style). DB tests use a throwaway `p_music_test`.
  HF and MusicBrainz are mocked in tests.

## Dependency Graph

```
T1 connection + migration runner
 └─ T2 core schema (users+demographics, user_accounts, artists, tracks, credits, external ids, ingest_runs)
     └─ T3 people + accounts: connector + app Users page     ── first end-to-end slice
         ├─ T4 Apple: schema + loader + app upload (local)
         └─ T5 Spotify: schema + loader + app upload (local)
                 ├─ T6 HF push from app
                 └─ T7 backfill data/ + cross-source check
                      └─ T8 artist demographics (MusicBrainz) + app hook
                           └─ T9 query tools + user_track_stats view
                                └─ T10 app Explore + Artists pages
                                     └─ T11 drop old DBs + docs
```

T4/T5 are independent of each other, and so are T6/T7.

## Task List

Full task cards are in `tasks/todo.md`.

### Phase 1: Foundation
- [x] T1: DB connection + migration runner
- [x] T2: Core schema (people, accounts, demographics, tracks)

### Checkpoint: Foundation
- [x] `p_music` exists; migrations apply twice cleanly
- [x] Human reviews `001_core.sql` (demographic columns + account model)

### Phase 2: Intake
- [x] T3: People + accounts (connector + app Users page)
- [ ] T4: Apple Music import
- [ ] T5: Spotify import
- [ ] T6: Push uploads to Hugging Face
- [ ] T7: Backfill existing `data/` + cross-source check

### Checkpoint: Intake
- [ ] In the app: new person → demographics → accounts → uploads → DB rows + HF files
- [ ] Backfill totals equal the source files; reloading changes nothing; all tests pass

### Phase 3: Artists + Query
- [ ] T8: Artist demographics from MusicBrainz
- [ ] T9: Query tools + `user_track_stats` view
- [ ] T10: App Explore + Artists pages

### Checkpoint: Complete
- [ ] T11: Docs updated; `P-Music`, `MusicDb`, `MUST_RAG` dropped (final go-ahead at run time)
- [ ] Human review

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Manually entered demographics / artist corrections lost | High | Migrations only; `pg_dump -t users -t user_accounts -t artists` before any migration or drop; each migration in its own transaction. |
| App upload overwrites someone else's HF file | High | `unique (source, source_username)`; push refused if the HF path belongs to another account. |
| PII pushed to HF (`ip_addr`) | Med | Strip `_PII` before upload; test asserts it. |
| HF token lacks write / expired | Med | Clear error; **ask the user for a new token**. |
| Wrong MusicBrainz match (common single-word names: "Đen", "Wren") | Med | Only unambiguous hits auto-accepted; album-title tie-break; ambiguous ones queued for review in the app; `manual` edits never overwritten. T8 reports the auto/ambiguous/not_found split. |
| MusicBrainz coverage low for small VN/indie artists | Med | Measured in T8. Every miss goes to the user with MusicBrainz + Wikidata candidates side by side; nothing is guessed. If the review list is very long, review in play order and leave the long tail `not_found`. |
| Wikidata candidate is a namesake (e.g. a historical figure) | Low | Shown with its description and P31 "instance of"; the user picks, and it's never auto-applied. |
| MusicBrainz rate limit / outage | Low | 1 req/s throttle, resumable (skips done artists), failures stay `NULL` and retry next run. |
| `match_key` over/under-merges across sources | Med | Source ids kept; T7 reports overlap and large merge groups. |

## Resolved (2026-09-25)
- `MUST_RAG`: drop.
- User demographics: gender (fixed list), city, age (as `birth_year`), native language,
  musical background, occupation, education, plus `extra`. Entered manually.
- Everyone in `data/` is a distinct person for now (5 people, 1 account each).
- App uploads go to the HF dataset. On a token error, ask the user for a new token.
- Artist demographics: looked up and stored.
- Musical background: level + instruments + years of training.
- Artists not auto-matched on MusicBrainz: ask the user and show both the MusicBrainz and the Wikidata candidates.

## Open Questions
1. **Podcasts/audiobooks**: the plan stores them with `kind`, and the query tools default to music only.

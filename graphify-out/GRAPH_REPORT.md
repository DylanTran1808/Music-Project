# Graph Report - Music-Project  (2026-09-29)

## Corpus Check
- 50 files · ~37,500 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 9 file(s) not represented in the graph (top: .ipynb 4, (none) 3, .toml 1)

## Summary
- 561 nodes · 1205 edges · 21 communities (19 shown, 2 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 54 edges (avg confidence: 0.91)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `401fd703`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- connectors/__init__.py
- test_app.py
- spotify_insights.py
- apple_insights.py
- import_data.py
- load.py
- Tasks: `p_music` database, intake app, connector + query tools
- am_previews_fetch.py
- upload.py
- apple_fetch.py
- musicbrainz.py
- wikidata.py
- artists.py
- enrich_artists.py
- languages.py
- Project-Music
- artist_fields
- test_musicbrainz.py
- Project Structure
- CLAUDE.md
- Music-Project

## God Nodes (most connected - your core abstractions)
1. `scratch_db()` - 21 edges
2. `build_profile()` - 20 edges
3. `migrate()` - 20 edges
4. `run()` - 20 edges
5. `build_profile()` - 16 edges
6. `Tasks: `p_music` database, intake app, connector + query tools` - 16 edges
7. `SpotifyHistory` - 14 edges
8. `enrich_artists()` - 14 edges
9. `test_load_data_dir()` - 14 edges
10. `enrich_tracks()` - 13 edges

## Surprising Connections (you probably didn't know these)
- `Checkpoint: Foundation` --references--> `migrate()`  [INFERRED]
  tasks/todo.md → src/music_project/db/__init__.py
- `Task 4: Apple Music import (schema + loader + app upload, local) ✅` --references--> `version_tags()`  [INFERRED]
  tasks/todo.md → src/music_project/analysis/apple_insights.py
- `Task 5: Spotify import (schema + loader + app upload, local) ✅` --references--> `enrich_events()`  [INFERRED]
  tasks/todo.md → src/music_project/analysis/spotify_insights.py
- `Task 8: Artist demographics from MusicBrainz` --references--> `pick_match()`  [INFERRED]
  tasks/todo.md → src/music_project/connectors/musicbrainz.py
- `Task 1: DB connection + migration runner ✅` --references--> `connect()`  [INFERRED]
  tasks/todo.md → src/music_project/db/__init__.py

## Import Cycles
- None detected.

## Communities (21 total, 2 thin omitted)

### Community 0 - "connectors/__init__.py"
Cohesion: 0.05
Nodes (67): HfFileSystem, music_project_analysis, music_project_analysis_apple_insights, ndarray, pandas, pyarrow_parquet, has_apple_music_data(), has_spotify_data() (+59 more)

### Community 1 - "test_app.py"
Cohesion: 0.08
Nodes (61): contextlib, datetime, dotenv, pathlib, psycopg2, psycopg2_extensions, account_pushed(), add_account() (+53 more)

### Community 2 - "spotify_insights.py"
Cohesion: 0.06
Nodes (62): canonical_artists(), All credited artists: the artist field split on &/,/feat./x, plus any "(feat.…, Collapses case variants ("HaiSam" / "Haisam") to the most common spelling…, Language guess from the writing system: ko / ja / zh / vi / latin / other.…, script_lang(), split_artists(), build_report(), Pulls whichever sources are actually available for this user. A source that… (+54 more)

### Community 3 - "apple_insights.py"
Cohesion: 0.07
Nodes (50): math, affinity_table(), album_behavior(), AppleLibrary, artist_cooccurrence(), build_profile(), concentration(), _curated() (+42 more)

### Community 4 - "import_data.py"
Cohesion: 0.06
Nodes (34): _alive(), friendly(), get_conn(), label(), pick(), push_to_hf(), Path, Helpers shared by the app's pages. (+26 more)

### Community 5 - "load.py"
Cohesion: 0.10
Nodes (34): hashlib, itertools, json, plistlib, db_totals(), main(), Load every export in data/ (laid out like the HF dataset) into p_music and…, Counts straight from the files, independent of the loaders' parsing. (+26 more)

### Community 6 - "Tasks: `p_music` database, intake app, connector + query tools"
Cohesion: 0.06
Nodes (32): music_project_analysis_report, music_project_analysis_spotify_insights, DataFrame, Artists and tracks present in both sources. Tracks match on track_key (primary…, source_overlap(), artists(), Checkpoint: Complete, Checkpoint: Foundation (+24 more)

### Community 7 - "am_previews_fetch.py"
Cohesion: 0.09
Nodes (31): difflib, logging, requests, download_preview(), find_best_match(), main(), _match_score(), _normalize() (+23 more)

### Community 8 - "upload.py"
Cohesion: 0.12
Nodes (25): Exception, HfApi, huggingface_hub, huggingface_hub_errors, _api(), apple_path(), _push(), push_apple() (+17 more)

### Community 9 - "apple_fetch.py"
Cohesion: 0.10
Nodes (24): io, numpy, pydub, debug_search(), enrich_library_with_previews(), extract_clap_embedding(), extract_mert_embedding(), get_apple_preview() (+16 more)

### Community 10 - "musicbrainz.py"
Cohesion: 0.19
Nodes (15): base_title(), Title without decorations, casefolded: the version-independent song identity., artist_releases(), _get(), _iso1(), _names(), _norm(), pick_match() (+7 more)

### Community 11 - "wikidata.py"
Cohesion: 0.26
Nodes (13): _get(), is_musical(), _item_ids(), _label(), parse_candidates(), wikidata.py Wikidata candidates for artists MusicBrainz couldn't match clearly.…, First date of a time property, cut to its precision: YYYY-MM-DD, YYYY-MM or…, Candidate dicts from wbsearchentities hits + wbgetentities of them and of the… (+5 more)

### Community 12 - "artists.py"
Cohesion: 0.24
Nodes (9): psycopg2_extras, enrich_artists(), Artist demographics in p_music: the MusicBrainz lookup loop and manual edits.…, Sets an artist's demographics outside the automatic lookup. The default is the…, Looks up pending artists; returns counts {auto, ambiguous, not_found, errors}., set_artist_demographics(), _update(), Task 10: App Explore + Artists pages (+1 more)

### Community 13 - "enrich_artists.py"
Cohesion: 0.24
Nodes (8): argparse, _cell(), main(), Path, Look up artist demographics on MusicBrainz, then write the review list. uv run…, write_review(), coverage(), Artists and their plays per match status ('pending' = not looked up yet).

### Community 14 - "languages.py"
Cohesion: 0.24
Nodes (9): collections, langdetect, artist_language(), classify_languages(), _detect(), Language groups (vi / en / other / unknown) for artists and tracks. Metadata…, scripts: title-script counts of the artist's tracks; detected: (lang, prob)…, Recomputes artists.lang_group and tracks.lang_group; returns artist counts per… (+1 more)

### Community 15 - "Project-Music"
Cohesion: 0.22
Nodes (8): 1. Install uv, 2. Get the project running, 3. Running code, 4. Adding a new dependency, 5. In case having unfound problem:, 6. Project structure, 7. Common commands cheat sheet, Project-Music

### Community 16 - "artist_fields"
Cohesion: 0.22
Nodes (9): artist_fields(), candidate_summary(), _date(), MusicBrainz dates are YYYY, YYYY-MM or YYYY-MM-DD; missing parts become 1., JSON-safe summary of a search hit, kept on the artist row for manual review., birth_year_from_age(), Birth year for someone `age` years old today (may be off by one before their…, test_birth_year_from_age() (+1 more)

### Community 17 - "test_musicbrainz.py"
Cohesion: 0.28
Nodes (7): Artists waiting for the user's decision, most-listened first, with what we know…, review_items(), Artist demographics checks: MusicBrainz / Wikidata parsing, the match rule, and…, releases_of(), test_enrich_artists(), fake_wikidata(), test_pick_match()

### Community 18 - "Project Structure"
Cohesion: 0.29
Nodes (6): Audio preview / embedding pipeline (experimental), Data flow: `build_report(username)`, Directory tree, Known loose ends, Module dependencies, Project Structure

## Knowledge Gaps
- **30 isolated node(s):** `Music-Project`, `graphify`, `1. Install uv`, `2. Get the project running`, `3. Running code` (+25 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 240 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **2 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Architecture Decisions` connect `spotify_insights.py` to `languages.py`, `Tasks: `p_music` database, intake app, connector + query tools`?**
  _High betweenness centrality (0.047) - this node is a cross-community bridge._
- **Why does `Implementation Plan: `p_music` database, intake app, connector + query tools` connect `Tasks: `p_music` database, intake app, connector + query tools` to `spotify_insights.py`?**
  _High betweenness centrality (0.045) - this node is a cross-community bridge._
- **Why does `Tasks: `p_music` database, intake app, connector + query tools` connect `Tasks: `p_music` database, intake app, connector + query tools` to `test_app.py`, `spotify_insights.py`, `apple_insights.py`, `upload.py`, `artists.py`?**
  _High betweenness centrality (0.030) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `migrate()` (e.g. with `Checkpoint: Foundation` and `Task 11: Drop old DBs + docs`) actually correct?**
  _`migrate()` has 2 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Music-Project`, `graphify`, `1. Install uv` to the rest of the system?**
  _30 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `connectors/__init__.py` be split into smaller, more focused modules?**
  _Cohesion score 0.0518326545723806 - nodes in this community are weakly interconnected._
- **Should `test_app.py` be split into smaller, more focused modules?**
  _Cohesion score 0.08186341022161918 - nodes in this community are weakly interconnected._
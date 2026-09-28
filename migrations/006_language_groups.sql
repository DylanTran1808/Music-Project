-- Language group of each artist and track, for keeping the project's scope to Vietnamese and
-- English music (user request, 2026-09-28). Computed by music_project.db.languages.classify_languages;
-- nothing is deleted, so the rule can change later without re-uploading anything.
--   vi / en: in scope;  other: another language (K-pop, J-pop, French, ...);  unknown: not enough signal.

ALTER TABLE artists ADD COLUMN lang_group text CHECK (lang_group IN ('vi', 'en', 'other', 'unknown'));
ALTER TABLE tracks  ADD COLUMN lang_group text CHECK (lang_group IN ('vi', 'en', 'other', 'unknown'));
CREATE INDEX ON tracks (lang_group);

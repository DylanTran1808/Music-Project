"""
wikidata.py

Wikidata candidates for artists MusicBrainz couldn't match clearly. Only used to show the
user options during review; nothing found here is applied automatically.

    search_wikidata(name) -> candidate dicts: wikidata_id, label, description, instance_of,
                             gender, country (ISO alpha-2), birth_date, birth_place, end_date, mb_id

Search is noisy ("Wren" finds a town, a bird and a video game), so a hit is kept only if it
has a MusicBrainz id, a musical description ("singer", "band", "boy group", ...), or a musical type.
"""

import re

import requests

from music_project.connectors.musicbrainz import user_agent

API = "https://www.wikidata.org/w/api.php"
GENDERS = {"Q6581097": "male", "Q6581072": "female", "Q48270": "non_binary", "Q1097630": "other",
           "Q2449503": "male", "Q1052281": "female"}  # male, female, non-binary, intersex, trans man, trans woman
# Whole words only: "sing" would match "single" (a song), a bare "group" matches "group of organisms".
MUSICAL = re.compile(r"\b(singers?|rappers?|musicians?|music|musical|bands?|composers?|songwriters?|djs?|"
                     r"producers?|vocalists?|idols?|duos?|trios?|orchestras?|choirs?|"
                     r"(boy|girl|pop|rock|idol|vocal|hip hop|k-pop) groups?)\b", re.I)


def _get(**params) -> dict:
    r = requests.get(API, params={**params, "format": "json"}, headers={"User-Agent": user_agent()}, timeout=30)
    r.raise_for_status()
    return r.json()


def search_wikidata(name: str, limit: int = 5) -> list:
    hits = {}
    for lang in ("vi", "en"):  # Vietnamese labels first; most of the catalogue is Vietnamese
        for h in _get(action="wbsearchentities", search=name, language=lang, uselang="en", type="item",
                      limit=limit)["search"]:
            hits.setdefault(h["id"], h)
    if not hits:
        return []
    entities = _get(action="wbgetentities", ids="|".join(hits), props="labels|descriptions|claims",
                    languages="en|vi")["entities"]
    refs = sorted({v for e in entities.values() for p in ("P31", "P21", "P27", "P19") for v in _item_ids(e, p)})
    ref_entities = _get(action="wbgetentities", ids="|".join(refs[:50]), props="labels|claims",
                        languages="en")["entities"] if refs else {}
    return parse_candidates(list(hits.values()), entities, ref_entities)


def _values(entity: dict, prop: str) -> list:
    return [c["mainsnak"]["datavalue"]["value"] for c in entity.get("claims", {}).get(prop, [])
            if (c.get("mainsnak") or {}).get("datavalue")]


def _item_ids(entity: dict, prop: str) -> list:
    return [v["id"] for v in _values(entity, prop) if isinstance(v, dict) and "id" in v]


def _label(entity: dict) -> str:
    labels = entity.get("labels") or {}
    return ((labels.get("en") or labels.get("vi")) or {}).get("value")


def _time(entity: dict, prop: str):
    """First date of a time property, cut to its precision: YYYY-MM-DD, YYYY-MM or YYYY."""
    for v in _values(entity, prop):
        if isinstance(v, dict) and "time" in v:
            chars = {11: 10, 10: 7}.get(v.get("precision"), 4)
            return v["time"].lstrip("+")[:chars]
    return None


def parse_candidates(search: list, entities: dict, refs: dict) -> list:
    """Candidate dicts from wbsearchentities hits + wbgetentities of them and of the items they point at."""
    out = []
    for hit in search:
        e = entities.get(hit["id"], {})
        ref_label = lambda q: _label(refs.get(q, {}))
        instance_of = [ref_label(q) or q for q in _item_ids(e, "P31")]
        mb_ids = _values(e, "P434")
        countries = [iso for q in _item_ids(e, "P27") for iso in _values(refs.get(q, {}), "P297")]
        genders = [GENDERS[q] for q in _item_ids(e, "P21") if q in GENDERS]
        places = [ref_label(q) for q in _item_ids(e, "P19")]
        out.append({
            "wikidata_id": hit["id"], "label": hit.get("label"), "description": hit.get("description"),
            "instance_of": instance_of, "gender": genders[0] if genders else None,
            "country": countries[0] if countries else None,
            "birth_date": _time(e, "P569") or _time(e, "P571"),  # born, or founded (groups)
            "birth_place": places[0] if places else None,
            "end_date": _time(e, "P570") or _time(e, "P576"),    # died, or dissolved
            "mb_id": mb_ids[0] if mb_ids else None,
        })
    return [c for c in out if is_musical(c)]


def is_musical(c: dict) -> bool:
    """A candidate worth showing: has a MusicBrainz id, or a musical description or type."""
    return bool(c.get("mb_id") or MUSICAL.search(c.get("description") or "")
                or any(MUSICAL.search(t or "") for t in c.get("instance_of", [])))

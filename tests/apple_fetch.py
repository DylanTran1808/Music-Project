"""
Example: pull 30-second Apple Music preview clips via the free, keyless
iTunes Search API, and load them as waveforms ready for MERT/CLAP.

No auth needed - this hits https://itunes.apple.com/search directly.
"""

import difflib
import io
import os
import time

import numpy as np
import requests
from pydub import AudioSegment

# Must be set before torch is imported - lets torch silently fall back to
# CPU for the handful of ops MPS doesn't support yet, instead of crashing.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
import torch


def get_device():
    """
    Apple Silicon (M1/M2/M3/M4) can run these models on GPU via MPS, which
    is meaningfully faster than CPU for a 330M-parameter model like MERT.
    Intel Macs have no MPS and fall back to CPU automatically - this isn't
    an error, just slower.
    """
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def _normalize(s):
    return s.lower().strip()


def _match_score(query_track, query_artist, candidate):
    """0-1 similarity between what you asked for and a candidate result,
    weighted toward the title since artist strings vary a lot (features,
    romanization, ordering)."""
    track_score = difflib.SequenceMatcher(
        None, _normalize(query_track), _normalize(candidate.get("trackName", ""))
    ).ratio()
    artist_score = difflib.SequenceMatcher(
        None, _normalize(query_artist), _normalize(candidate.get("artistName", ""))
    ).ratio()
    return 0.7 * track_score + 0.3 * artist_score


def get_apple_preview(track_name, artist_name, country="us", limit=5, min_score=0.5):
    """
    Query the iTunes Search API for one track.

    country: Apple's catalog is split per storefront - a track can be
    indexed on the Vietnamese store (country="vn") and simply absent from
    the US one. Pass the track's actual release market, not always "us".

    artist_name: if you pass multiple collaborators ("Obito, Shiki, RPT
    MCK"), only the first is used for the query itself - the full string is
    still used for scoring. Cramming every artist into the search term
    turns this into a generic keyword search, which can match an unrelated
    track that happens to share some of those names as features.

    Pulls `limit` candidates and picks the one that actually matches by
    text similarity, instead of trusting the API's top result blindly.
    Returns None (with a printed warning) if nothing clears min_score,
    rather than silently returning a wrong track.
    """
    primary_artist = artist_name.split(",")[0].strip()
    params = {
        "term": f"{track_name} {primary_artist}",
        "media": "music",
        "entity": "song",
        "limit": limit,
        "country": country,
    }
    resp = requests.get("https://itunes.apple.com/search", params=params, timeout=10)
    resp.raise_for_status()
    results = resp.json().get("results", [])
    if not results:
        return None

    scored = sorted(
        results,
        key=lambda r: _match_score(track_name, artist_name, r),
        reverse=True,
    )
    best = scored[0]
    best_score = _match_score(track_name, artist_name, best)

    if best_score < min_score:
        print(
            f"Low-confidence match ({best_score:.2f}) for '{track_name}' - "
            f"{artist_name}: got '{best.get('trackName')}' - "
            f"{best.get('artistName')}. Try a different country storefront."
        )
        return None
    return best


def debug_search(track_name, artist_name, country="vn", limit=10):
    """
    Prints every candidate the API returns with its match score, instead
    of just the best (possibly rejected) one - useful when the top match
    is below min_score and you want to see what's actually available
    before concluding the track isn't on this storefront.
    """
    params = {
        "term": f"{track_name} {artist_name.split(',')[0].strip()}",
        "media": "music",
        "entity": "song",
        "limit": limit,
        "country": country,
    }
    resp = requests.get("https://itunes.apple.com/search", params=params, timeout=10)
    resp.raise_for_status()
    results = resp.json().get("results", [])
    if not results:
        print(f"No results at all for '{track_name}' on country='{country}'")
        return
    for r in sorted(
        results, key=lambda r: _match_score(track_name, artist_name, r), reverse=True
    ):
        score = _match_score(track_name, artist_name, r)
        print(f"{score:.2f}  {r.get('trackName')} - {r.get('artistName')}")


def save_and_play_preview(preview_url, out_path="preview.m4a"):
    """
    Saves the raw preview clip exactly as Apple sends it (no re-encoding)
    and plays it - the quickest way to confirm a match is actually the
    right song (e.g. checking "Đánh Đổi" resolved correctly) before it
    goes anywhere near the embedding pipeline.

    Defaults to saving next to wherever the script runs from (not /tmp -
    that's hidden in Finder by default on macOS and easy to lose track of).

    macOS: uses `afplay`, which ships with every Mac - no extra install.
    Linux: swap the subprocess call for
        subprocess.run(["ffplay", "-nodisp", "-autoexit", out_path])
    (ffplay comes bundled with the ffmpeg install pydub already needs).
    Windows: skip the subprocess call and just open out_path in Explorer
    or the Music app instead.
    """
    import subprocess

    audio_bytes = requests.get(preview_url, timeout=10).content
    with open(out_path, "wb") as f:
        f.write(audio_bytes)
    print(f"Saved to {os.path.abspath(out_path)}")
    subprocess.run(["afplay", out_path])
    return out_path


# --- Single-track example ---
record = get_apple_preview("Bohemian Rhapsody", "Queen")
if record:
    print(record["trackName"], "-", record["artistName"])
    print(record["previewUrl"])
    # -> https://audio-ssl.itunes.apple.com/itunes-assets/.../preview.m4a

# Non-US releases: search the track's actual storefront
vn_record = get_apple_preview("Đánh Đổi", "Obito, Shiki, RPT MCK", country="vn")
if vn_record:
    save_and_play_preview(vn_record["previewUrl"])  # listen and confirm by ear


# --- Batch over your existing Apple Music library dataframe ---
def enrich_library_with_previews(
    df, name_col="Name", artist_col="Artist", country="us", sleep=0.2
):
    """
    df: your 1,472-track Apple Music export.
    Adds a `preview_url` column by looking each track up.
    `country`: set per-row if your library mixes storefronts (e.g. a
    `Country` column from Apple Music export data), rather than one value
    for the whole batch - a fixed "us" will silently miss/mismatch
    non-US releases the way the Vietnamese example above did.
    `sleep` throttles requests - there's no published rate limit for this
    public endpoint, but it's polite (and safer) not to hammer it.
    """
    previews = []
    for _, row in df.iterrows():
        rec = get_apple_preview(row[name_col], row[artist_col], country=country)
        previews.append(rec["previewUrl"] if rec else None)
        time.sleep(sleep)
    df["preview_url"] = previews
    return df


# --- Download a clip and load it as a waveform for embedding models ---
def load_preview_as_waveform(preview_url, target_sr=24000):
    """
    Apple's preview is AAC-encoded inside an .m4a container - libsndfile
    (what librosa/soundfile use by default) can't decode that, it only
    handles PCM-ish formats like WAV/FLAC/OGG. pydub shells out to ffmpeg
    instead, which handles AAC fine.

    Requires ffmpeg on PATH:
        macOS:   brew install ffmpeg
        Ubuntu:  sudo apt install ffmpeg
        Windows: choco install ffmpeg  (or download a build and add to PATH)
    And: pip install pydub

    MERT expects 24kHz mono input; CLAP variants commonly expect 48kHz -
    set target_sr to match whichever model you're feeding.
    Returns a float32 numpy waveform in [-1, 1] plus the sample rate.
    """
    audio_bytes = requests.get(preview_url, timeout=10).content
    audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format="m4a")
    audio = audio.set_channels(1).set_frame_rate(target_sr)

    samples = np.array(audio.get_array_of_samples()).astype(np.float32)
    samples /= np.iinfo(audio.array_type).max  # int PCM -> [-1, 1] float
    return samples, target_sr


# --- Turn the waveform into an actual embedding ---
def extract_mert_embedding(waveform, sr, device=None):
    """
    MERT has 13 transformer layers; different layers encode different things
    (lower = acoustic/timbral, higher = more semantic/musical), so it
    returns all of them stacked - pick a layer (or average a few) based on
    what you're using the embedding for.

    pip install transformers torch
    Model card: m-a-p/MERT-v1-330M (assumes waveform is already 24kHz mono,
    which load_preview_as_waveform gives you by default).
    """
    from transformers import Wav2Vec2FeatureExtractor, AutoModel

    device = device or get_device()
    processor = Wav2Vec2FeatureExtractor.from_pretrained(
        "m-a-p/MERT-v1-330M", trust_remote_code=True
    )
    model = AutoModel.from_pretrained(
        "m-a-p/MERT-v1-330M", trust_remote_code=True
    ).to(device)

    inputs = processor(waveform, sampling_rate=sr, return_tensors="pt")
    inputs = {k: v.to(device) for k, v in inputs.items()}
    with torch.no_grad():
        outputs = model(**inputs, output_hidden_states=True)

    # [num_layers, time, hidden_dim] -> mean over time -> [num_layers, hidden_dim]
    layerwise = torch.stack(outputs.hidden_states).squeeze(1).mean(dim=1)
    return layerwise.cpu().numpy()  # pick e.g. layerwise[6] as your feature vector


def extract_clap_embedding(waveform, sr, device=None):
    """
    CLAP gives one fixed-size vector per clip (no layer-picking needed) -
    the same space text queries get embedded into, which is what makes
    natural-language search over your library possible later.

    pip install transformers torch
    Model card: laion/clap-htsat-unfused (expects 48kHz - load your waveform
    with load_preview_as_waveform(url, target_sr=48000) for this one).
    """
    from transformers import ClapProcessor, ClapModel

    device = device or get_device()
    processor = ClapProcessor.from_pretrained("laion/clap-htsat-unfused")
    model = ClapModel.from_pretrained("laion/clap-htsat-unfused").to(device)

    inputs = processor(audios=waveform, sampling_rate=sr, return_tensors="pt")
    inputs = {k: v.to(device) for k, v in inputs.items()}
    with torch.no_grad():
        embedding = model.get_audio_features(**inputs)
    return embedding.squeeze().cpu().numpy()  # e.g. shape (512,)


if __name__ == "__main__":
    track_name = "my lil b*tich"
    artist_name = "Darangto"

    print(f"Searching for: {track_name} - {artist_name}")
    
    debug_search(
        "Xương Rồng (intro)",
        "Darangto",
        country="vn",
        limit=10,
    )
    
    record = get_apple_preview(
        track_name,
        artist_name,
        country="vn",
        limit=10,
        min_score=0.5,
    )

    if record:
        print("\nMatched track:")
        print(f"  Name:   {record.get('trackName')}")
        print(f"  Artist: {record.get('artistName')}")
        print(f"  Album:  {record.get('collectionName')}")
        print(f"  Score:  {_match_score(track_name, artist_name, record):.3f}")
        print(f"  Preview: {record.get('previewUrl')}")

        # save_and_play_preview(
        #     record["previewUrl"],
        #     out_path="danh_doi_preview.m4a",
        # )
    else:
        print("No confident match found.")
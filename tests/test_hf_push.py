"""HF push checks with a fake HfApi (never touches the real dataset). Run: uv run python tests/test_hf_push.py"""

import json
import tempfile
from pathlib import Path

import requests
from huggingface_hub.errors import HfHubHTTPError

from music_project.connectors import upload
from music_project.connectors.upload import PushError, PushRefused, push_apple, push_spotify


class FakeHub:
    """In-memory stand-in for the HfApi calls upload.py makes."""

    def __init__(self, files=(), fail_status=None):
        self.files = dict.fromkeys(files, b"")
        self.commits = []
        self.fail_status = fail_status

    def _maybe_fail(self):
        if self.fail_status:
            response = requests.Response()
            response.status_code = self.fail_status
            raise HfHubHTTPError(f"{self.fail_status} Client Error", response=response)

    def list_repo_files(self, repo_id, repo_type):
        self._maybe_fail()
        return list(self.files)

    def upload_file(self, path_or_fileobj, path_in_repo, repo_id, repo_type, commit_message):
        self._maybe_fail()
        self.files[path_in_repo] = Path(path_or_fileobj).read_bytes()
        self.commits.append([path_in_repo])

    def upload_folder(self, folder_path, path_in_repo, repo_id, repo_type, commit_message):
        self._maybe_fail()
        paths = []
        for f in sorted(Path(folder_path).iterdir()):
            self.files[f"{path_in_repo}/{f.name}"] = f.read_bytes()
            paths.append(f"{path_in_repo}/{f.name}")
        self.commits.append(paths)


def expect(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc as err:
        return err
    raise AssertionError(f"{fn.__name__} did not raise {exc.__name__}")


def test_push_apple():
    with tempfile.TemporaryDirectory() as tmp:
        xml = Path(tmp) / "Library.xml"
        xml.write_bytes(b"<plist>library</plist>")
        hub = FakeHub()
        assert push_apple(xml, "tu", api=hub) == "raw_am/Library_tu.xml"
        assert hub.files["raw_am/Library_tu.xml"] == xml.read_bytes()  # unchanged

        # Someone else's file at that path is never overwritten silently...
        other = FakeHub(files=["raw_am/Library_tu.xml"])
        err = expect(PushRefused, push_apple, xml, "tu", api=other)
        assert "raw_am/Library_tu.xml" in str(err) and other.commits == []
        # ...but the owner (overwrite=True) can push a newer export as a new commit.
        push_apple(xml, "tu", overwrite=True, api=other)
        assert len(other.commits) == 1


def test_push_spotify_strips_pii():
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        rec = {"ts": "2026-01-01T10:00:00Z", "ms_played": 1000, "ip_addr": "203.0.113.7",
               "master_metadata_track_name": "Xa Xôi", "username": "someone"}
        (folder / "Streaming_History_Audio_2026.json").write_text(json.dumps([rec]), encoding="utf-8")
        hub = FakeHub()
        assert push_spotify(folder, "sp", api=hub) == "raw_spot/Spotify_sp"
        pushed = json.loads(hub.files["raw_spot/Spotify_sp/Streaming_History_Audio_2026.json"])
        assert pushed == [{"ts": "2026-01-01T10:00:00Z", "ms_played": 1000, "master_metadata_track_name": "Xa Xôi"}]
        assert "ip_addr" in (folder / "Streaming_History_Audio_2026.json").read_text()  # local copy untouched

        other = FakeHub(files=["raw_spot/Spotify_sp/Streaming_History_Audio_2020.json"])
        expect(PushRefused, push_spotify, folder, "sp", api=other)


def test_auth_errors_are_clear():
    with tempfile.TemporaryDirectory() as tmp:
        xml = Path(tmp) / "Library.xml"
        xml.write_bytes(b"<plist/>")
        for status in (401, 403):
            err = expect(PushError, push_apple, xml, "tu", api=FakeHub(fail_status=status))
            assert "needs write access" in str(err) and upload.DEFAULT_REPO_ID in str(err), str(err)
        err = expect(PushError, push_apple, xml, "tu", api=FakeHub(fail_status=500))
        assert "needs write access" not in str(err)


if __name__ == "__main__":
    test_push_apple()
    test_push_spotify_strips_pii()
    test_auth_errors_are_clear()
    print("test_hf_push: ok")

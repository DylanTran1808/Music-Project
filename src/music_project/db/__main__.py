"""
Usage: uv run python -m music_project.db migrate|push|pull [--yes]

push: pg_dump the whole DATABASE_URL database to db/p_music.dump in the HF dataset.
pull: download that dump and restore it over DATABASE_URL (replaces local data; asks first
      unless --yes). The restore is one transaction, so a failure leaves the local data as it was.
The dump holds demographics, so push refuses unless the dataset is private.
"""

import glob
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_download
from psycopg2.extensions import make_dsn, parse_dsn

from music_project.db import connect, migrate
from music_project.connectors.common import DEFAULT_REPO_ID, HF_TOKEN

DUMP_PATH = "db/p_music.dump"
args = sys.argv[1:]
yes = "--yes" in args
cmd = [a for a in args if a != "--yes"]
if cmd not in (["migrate"], ["push"], ["pull"]):
    sys.exit(__doc__)


def pg_target():
    """DATABASE_URL as a pg_dump/pg_restore -d value, with the password moved to PGPASSWORD (off the command line)."""
    url = os.getenv("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set; add e.g. DATABASE_URL=postgresql:///p_music to .env")
    dsn = parse_dsn(url)
    password = dsn.pop("password", None)
    return make_dsn(**dsn), {**os.environ, **({"PGPASSWORD": password} if password else {})}


def pg_tool(name):
    """Path to pg_dump/pg_restore: PATH first, then the Windows installer's folder (it doesn't add itself to PATH)."""
    installed = glob.glob(rf"C:\Program Files\PostgreSQL\*\bin\{name}.exe")
    found = shutil.which(name) or max(installed, key=lambda p: int(Path(p).parts[-3]), default=None)
    if not found:
        sys.exit(f"{name} not found; install PostgreSQL 18+ and put its bin folder on PATH")
    return found


if cmd == ["migrate"]:
    conn = connect()
    applied = migrate(conn)
    conn.close()
    print("\n".join(f"applied {name}" for name in applied) or "nothing to apply")

elif cmd == ["push"]:
    target, env = pg_target()
    api = HfApi(token=HF_TOKEN)
    if not api.repo_info(DEFAULT_REPO_ID, repo_type="dataset").private:
        sys.exit(f"{DEFAULT_REPO_ID} is public; refusing to push a dump with demographics")
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "p_music.dump")
        subprocess.run([pg_tool("pg_dump"), "-Fc", "--no-owner", "--no-privileges", "-d", target, "-f", out],
                       check=True, env=env)
        api.upload_file(path_or_fileobj=out, path_in_repo=DUMP_PATH, repo_id=DEFAULT_REPO_ID,
                        repo_type="dataset", commit_message="Database snapshot")
    print(f"pushed {DUMP_PATH}")

else:
    target, env = pg_target()
    if not yes and input(f"Replace all data in {parse_dsn(target).get('dbname')} with the HF snapshot? [y/N] ") != "y":
        sys.exit("cancelled")
    local = hf_hub_download(DEFAULT_REPO_ID, DUMP_PATH, repo_type="dataset", token=HF_TOKEN, force_download=True)
    subprocess.run([pg_tool("pg_restore"), "--clean", "--if-exists", "--no-owner", "--no-privileges",
                    "--single-transaction", "-d", target, local], check=True, env=env)
    print(f"restored {DUMP_PATH}")

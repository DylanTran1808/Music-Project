# Project-Music

A project applying data/database architecture and LLM techniques (embeddings, RAG, fine-tuning) to a music domain, using Apple Music and Spotify library data as the source.

This project uses **[uv](https://docs.astral.sh/uv/)** to manage Python, dependencies, and the virtual environment. uv replaces pip, pip-tools, virtualenv, and pyenv with a single fast tool, and it works the same way on macOS and Windows — so everyone on the team runs the exact same commands.

---

## 1. Install uv

**macOS / Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Restart your terminal afterward, then confirm it worked:
```bash
uv --version
```

If you use a shell like `fish` and the installer complains about a permissions error writing a config file, that's harmless — uv itself is still installed. Just make sure `~/.local/bin` (macOS/Linux) is on your PATH.

---

## 2. Get the project running

Clone the `Project-Music` repo, then from inside the project folder run:

```bash
uv sync
```

This one command:
- Downloads the correct Python version if you don't already have it
- Creates a local `.venv` virtual environment
- Installs every dependency at the exact version pinned in `uv.lock`
- On macOS, installs the CPU/MPS build of PyTorch; on Windows, installs the CUDA build — automatically, based on your OS

You do **not** need to manually create a virtual environment or activate it.

---

## 3. Running code

Use `uv run` in front of any command to run it inside the project's environment:

```bash

uv run scripts/parse_am.py

```

---

## 4. Adding a new dependency

If you need a new package, don't edit `pyproject.toml` by hand — use:

```bash
uv add <package-name>
```

This updates `pyproject.toml` and `uv.lock` for everyone. After pulling someone else's changes (which update `uv.lock`), just run `uv sync` again to match their environment.

---

## 5. Project structure

```
Project-Music/
├── pyproject.toml          # project metadata + dependencies
├── uv.lock                 # exact resolved versions — do not edit by hand
├── .python-version         # pinned Python version
├── .venv/                  # local environment (gitignored, machine-specific)
├── .gitignore
├── README.md
│
├── src/
│   └── music_project/.     ### Example file structure
│       ├── ingest/         # Apple Music, Spotify, and Google Drive data ingestion
│       ├── db/             # database schema + loaders
│       ├── embeddings/     # embedding generation
│       ├── rag/            # retrieval + RAG pipeline
│       ├── finetune/       # fine-tuning scripts
│       └── eval/           # evaluation metrics
│
├── scripts/                # one-off runnable scripts (`uv run scripts/x.py`)
├── notebooks/              # exploratory notebooks, not part of the shipped package
├── data/                   # local data cache (gitignored — real data lives on Google Drive)
└── tests/
```

**Notes:**
- Source data (Apple Music library export, Spotify data) lives on HuggingFace and is pulled in programmatically via the tool Phuong gonna works on and should be put under `src/project_music` — nothing large is committed to Git.
- `data/` is gitignored except for a placeholder `.gitkeep` file, so the folder exists in the repo without tracking any actual data.
- Notebooks are for exploration only; reusable logic should be moved into `src/project_music/`.

---

## 6. Common commands cheat sheet

| Task | Command |
|---|---|
| Install uv | see Section 1 |
| Set up the project after cloning | `uv sync` |
| Run a script | `uv run scripts/build_dataset.py` |
| Add a dependency | `uv add <package>` |
| Remove a dependency | `uv remove <package>` |
| Update the lockfile after editing pyproject.toml | `uv lock` |
| Run tests | `uv run pytest` |
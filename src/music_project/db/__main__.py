"""Usage: uv run python -m music_project.db migrate"""

import sys

from music_project.db import connect, migrate

if sys.argv[1:] != ["migrate"]:
    sys.exit(__doc__)

conn = connect()
applied = migrate(conn)
conn.close()
print("\n".join(f"applied {name}" for name in applied) or "nothing to apply")

"""Fail if any script can make a Spotify request without the budget guard.

The guard works by patching spotipy at import time, so a script that imports
spotipy but not `spotify_guard` (directly, or via `auth`) can spend quota
uncounted. That is exactly how the repo ended up with ~90 ungoverned calls.

    python3 check_budget_guard.py     # exit 1 if anything is unguarded
"""

import pathlib
import sys

GUARD_IMPORTS = ("import spotify_guard", "from auth import", "import auth")
SKIP = {"spotify_guard.py", "check_budget_guard.py"}


def main():
    here = pathlib.Path(__file__).parent
    bad = []
    for f in sorted(here.glob("*.py")):
        if f.name in SKIP:
            continue
        src = f.read_text(errors="replace")
        if "spotipy" not in src:
            continue
        if not any(g in src for g in GUARD_IMPORTS):
            bad.append(f.name)

    if bad:
        print("UNGUARDED — these use spotipy without the budget guard:")
        for b in bad:
            print(f"  {b}")
        print("\nAdd:  import spotify_guard  # noqa: F401")
        return 1

    print("all spotipy users are budget-guarded")
    return 0


if __name__ == "__main__":
    sys.exit(main())

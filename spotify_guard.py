"""Route every spotipy HTTP request through the daily budget.

Importing this module is enough — it patches `spotipy.Spotify._internal_call`,
which is the one function every spotipy request passes through, on every
client instance regardless of who constructed it.

Why not police call sites: there are ~90 direct `sp.search()` / `sp.playlist()`
calls across 19 scripts. Wrapping them individually is a losing game because
the next script written forgets. Patching the chokepoint covers code that does
not exist yet.

`check_budget_guard.py` fails CI//pre-commit if a script imports spotipy
without importing this.
"""

import os
import threading
import time

import spotipy

_ORIGINAL_INTERNAL_CALL = spotipy.Spotify._internal_call
_PATCHED = False

# Below this, a 429 is ordinary throttling that callers back off from. Above
# it, it is a real ban worth remembering so later runs do not probe it.
BAN_THRESHOLD_SECONDS = 300

# Dev mode allows ~50 requests per rolling 30s window, so a 0.75s floor between
# requests keeps us at ~40 — under the line without stalling interactive use.
# Pacing lives here for the same reason the budget does: this is the one
# function every spotipy request passes through, so a script that never heard
# of it is still paced. Sleeping in the call sites was tried and missed the ~90
# direct sp.foo() calls across the repo.
MIN_INTERVAL_S = float(os.environ.get("SPOTIFY_MIN_INTERVAL", "0.75"))

# The window limit is not the only limit: a sustained burst earns a multi-hour
# ban even when every individual call was polite. A breather every few hundred
# requests is what a long rebuild needs to survive.
BREATHER_EVERY = int(os.environ.get("SPOTIFY_BREATHER_EVERY", "200"))
BREATHER_S = float(os.environ.get("SPOTIFY_BREATHER_SECONDS", "30"))

_pace_lock = threading.Lock()
_last_call_at = [0.0]
_calls_since_breather = [0]


def _pace():
    """Block until enough time has passed since the previous request."""
    with _pace_lock:
        now = time.monotonic()
        wait = MIN_INTERVAL_S - (now - _last_call_at[0])
        if wait > 0:
            time.sleep(wait)
        _calls_since_breather[0] += 1
        if BREATHER_EVERY and _calls_since_breather[0] >= BREATHER_EVERY:
            print(f"  [pacing] {BREATHER_EVERY} requests — pausing {BREATHER_S:.0f}s")
            time.sleep(BREATHER_S)
            _calls_since_breather[0] = 0
        _last_call_at[0] = time.monotonic()


def _budgeted_internal_call(self, method, url, payload, params):
    from spotify_budget import charge, note_429

    charge()  # raises BudgetExceeded / InCooldown before any HTTP happens
    _pace()   # never outrun the rolling window
    try:
        return _ORIGINAL_INTERNAL_CALL(self, method, url, payload, params)
    except spotipy.SpotifyException as e:
        if e.http_status == 429:
            retry_after = (e.headers or {}).get("Retry-After")
            try:
                if retry_after and int(retry_after) > BAN_THRESHOLD_SECONDS:
                    note_429(retry_after)
            except (TypeError, ValueError):
                pass
        raise


def install():
    """Idempotent. Safe to call from every entry point."""
    global _PATCHED
    if not _PATCHED:
        spotipy.Spotify._internal_call = _budgeted_internal_call
        _PATCHED = True


install()

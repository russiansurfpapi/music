"""Hard ceiling on Spotify API usage, persisted across runs.

Two 24-hour `QUOTA_EXCEEDED` bans taught us that per-process caution is not
enough: the daily budget is shared by every script, so five well-behaved runs
still add up to a ban. This module keeps the running total in library.db and
refuses to make the call that would cross the line.

It also remembers a ban. Without that, every re-run spends a request just to
rediscover that we are still locked out.

    from spotify_budget import charge, note_429, status
    charge()          # raises BudgetExceeded / InCooldown, else counts 1

Override the ceiling with SPOTIFY_DAILY_LIMIT.
"""

import atexit
import datetime
import os
import sqlite3

from db import connect

_DEFAULT_LIMIT = 2500
# Safety margin below the lowest spend that ever earned a ban.
_MARGIN = 0.8


def _learned_limit():
    """Lowest observed ban point, minus a margin. None if never banned."""
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT MIN(requests_at_ban) FROM spotify_api_usage "
                "WHERE requests_at_ban IS NOT NULL AND requests_at_ban > 0"
            ).fetchone()
    except Exception:
        return None
    return int(row[0] * _MARGIN) if row and row[0] else None


def _limit():
    env = os.environ.get("SPOTIFY_DAILY_LIMIT")
    if env:
        return int(env)
    learned = _learned_limit()
    return min(_DEFAULT_LIMIT, learned) if learned else _DEFAULT_LIMIT


DAILY_LIMIT = _limit()
# Leave headroom so an interactive command still works after a big batch job.
WARN_AT = 0.8


class BudgetExceeded(RuntimeError):
    pass


class InCooldown(RuntimeError):
    pass


def _today():
    return datetime.date.today().isoformat()


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


# Charging on every request would mean a DB write per API call, which
# deadlocks against a caller holding an open write transaction. Count in
# memory, flush periodically and at exit.
_pending = 0
_baseline = None
_FLUSH_EVERY = 25


def _flush():
    """Persist the in-memory count. Never raises — accounting must not be able
    to break the work it is accounting for. A locked DB just defers the flush.
    """
    global _pending
    if not _pending:
        return
    n = _pending
    try:
        with connect() as conn:
            conn.execute(
                "INSERT INTO spotify_api_usage (day, requests) VALUES (?,?) "
                "ON CONFLICT(day) DO UPDATE SET requests = requests + ?",
                (_today(), n, n))
            conn.commit()
    except sqlite3.OperationalError:
        return  # caller holds a write txn; keep counting, retry next flush
    _pending -= n


atexit.register(_flush)


def _load():
    global _baseline
    if _baseline is None:
        with connect() as conn:
            row = conn.execute(
                "SELECT requests, cooldown_until FROM spotify_api_usage WHERE day=?",
                (_today(),)).fetchone()
        _baseline = ((row["requests"] or 0) if row else 0,
                     row["cooldown_until"] if row else None)
    return _baseline


def status():
    """(requests_used_today, limit, cooldown_until_or_None)."""
    with connect() as conn:
        row = conn.execute(
            "SELECT requests, cooldown_until FROM spotify_api_usage WHERE day=?",
            (_today(),)).fetchone()
    used = ((row["requests"] or 0) if row else 0) + _pending
    return used, DAILY_LIMIT, (row["cooldown_until"] if row else None)


def charge(n=1):
    """Account for n requests about to be made. Raises rather than overspend."""
    global _pending
    base, cooldown = _load()

    if cooldown and cooldown > _now():
        raise InCooldown(
            f"Spotify is rate-limiting this account until {cooldown}. "
            f"No requests will be made. Re-run after that time.")

    used = base + _pending
    if used + n > DAILY_LIMIT:
        _flush()
        raise BudgetExceeded(
            f"daily Spotify budget spent ({used}/{DAILY_LIMIT}). "
            f"Raise it with SPOTIFY_DAILY_LIMIT=... if you know the account "
            f"can take it, or continue tomorrow — every script here resumes "
            f"without losing work.")

    _pending += n
    if _pending >= _FLUSH_EVERY:
        _flush()
    return used + n


def note_429(retry_after_seconds):
    """Record a ban so later runs refuse to start instead of probing it."""
    try:
        secs = int(retry_after_seconds)
    except (TypeError, ValueError):
        secs = 3600
    until = (datetime.datetime.now()
             + datetime.timedelta(seconds=secs)).isoformat(timespec="seconds")
    global _baseline
    _flush()
    used, _, _ = status()
    _baseline = None
    with connect() as conn:
        conn.execute(
            "INSERT INTO spotify_api_usage (day, requests, last_429_at, "
            "cooldown_until, requests_at_ban) VALUES (?,0,?,?,?) "
            "ON CONFLICT(day) DO UPDATE SET last_429_at=excluded.last_429_at, "
            "cooldown_until=excluded.cooldown_until, "
            "requests_at_ban=COALESCE(spotify_api_usage.requests_at_ban, "
            "                         excluded.requests_at_ban)",
            (_today(), _now(), until, used))
        conn.commit()
    return until


def clear_cooldown():
    global _baseline
    _baseline = None
    with connect() as conn:
        conn.execute("UPDATE spotify_api_usage SET cooldown_until=NULL WHERE day=?",
                     (_today(),))
        conn.commit()


def guard(fn):
    """Wrap a script main() so a budget stop reads as a message, not a crash."""
    import functools
    import sys

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except (BudgetExceeded, InCooldown) as e:
            print(f"\nSTOPPED: {e}")
            used, limit, cd = status()
            print(f"  spent today: {used}/{limit}")
            sys.exit(2)
    return wrapper


if __name__ == "__main__":
    used, limit, cd = status()
    learned = _learned_limit()
    print(f"Spotify requests today: {used}/{limit}")
    if learned:
        print(f"limit learned from a past ban at {int(learned / _MARGIN)} requests")
    print(f"cooldown until: {cd}" if cd else "no cooldown")

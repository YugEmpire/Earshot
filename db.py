"""earshot probe - storage, feature derivation, shadow policy.

Stdlib only. No mock data, no demo mode, no seeding. Every row in the
database comes from a decision a human actually made at the prompt.
"""

import os
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

DB_PATH = Path(os.environ.get("EARSHOT_DB", Path(__file__).parent / "earshot.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
  id               TEXT PRIMARY KEY,
  ts               TEXT NOT NULL,
  session          TEXT NOT NULL,
  mode             TEXT NOT NULL,
  action_type      TEXT NOT NULL,
  summary          TEXT NOT NULL,
  payload          TEXT NOT NULL,
  norm_payload     TEXT NOT NULL,
  reversible       INTEGER NOT NULL,
  touches_external INTEGER NOT NULL,
  destructive      INTEGER NOT NULL,
  outside_project  INTEGER NOT NULL,
  outbound         INTEGER NOT NULL,
  seen_count       INTEGER NOT NULL,
  human_decision   TEXT,
  human_note       TEXT,
  latency_ms       INTEGER,
  exit_code        INTEGER,
  shadow_verdict   TEXT,
  shadow_rule      TEXT,
  outcome          TEXT
);
CREATE INDEX IF NOT EXISTS idx_session_ts ON decisions(session, ts);
CREATE INDEX IF NOT EXISTS idx_norm ON decisions(norm_payload);

-- Actions the user has explicitly agreed to stop being asked about.
-- Consent, not a counter: the rule only exists because they said yes out loud.
-- A scope is one spoken decision covering many future actions: "typechecks,
-- test runs and writes inside src/ are fine for the next 90 minutes."
-- Time-limited on purpose. A permanent scope is just an off switch with extra
-- steps, and you would forget it was on.
CREATE TABLE IF NOT EXISTS scopes (
  id         TEXT PRIMARY KEY,
  session    TEXT,
  summary    TEXT,
  commands   TEXT,      -- newline-separated globs
  paths      TEXT,      -- newline-separated globs
  created_at TEXT,
  expires_at TEXT
);

CREATE TABLE IF NOT EXISTS auto_rules (
  norm_payload TEXT PRIMARY KEY,
  summary      TEXT,
  created_at   TEXT,
  session      TEXT
);
"""


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # Added after the first sessions, so ALTER rather than a schema bump -
    # existing rows keep their data and simply carry NULL here.
    try:
        conn.execute("ALTER TABLE decisions ADD COLUMN answered_via TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass          # already present
    return conn


# ---------------------------------------------------------------- heuristics
# One table. Edit these as you learn what your own work actually looks like.

# Anything crossing the machine boundary at all.
EXTERNAL = [
    r"\bcurl\b", r"\bwget\b", r"\bssh\b", r"\bscp\b", r"\brsync\b",
    r"\bgit\s+(push|pull|fetch|clone|remote)\b",
    r"\bpip\s+(install|download|uninstall)\b", r"\bpip3\s+install\b",
    r"\bnpm\s+(i|install|publish|ci)\b", r"\byarn\s+(add|install)\b",
    r"\bdocker\s+(pull|push)\b", r"\bwinget\b", r"\bchoco\s+install\b",
    r"https?://", r"\bInvoke-WebRequest\b", r"\bInvoke-RestMethod\b",
]

# Writes something OUT that you cannot quietly take back: published, pushed,
# deployed, sent, charged. Distinct from fetching, which is cheap to undo.
# This split matters - lumping `pip install` in with `git push` makes the
# irreversibility signal useless.
OUTBOUND = [
    r"\bgit\s+push\b", r"\bnpm\s+publish\b", r"\btwine\s+upload\b",
    r"\bdocker\s+push\b", r"\bgh\s+(pr|release)\s+create\b",
    r"\bscp\b", r"\brsync\b.*:", r"\bssh\b",
    r"-X\s*(POST|PUT|PATCH|DELETE)", r"\bMethod\s+(Post|Put|Delete)\b",
    r"\bdeploy\b", r"\bpublish\b", r"\bsend(mail|_email)?\b",
    r"\baws\s+s3\s+(cp|sync|rm)\b", r"\bterraform\s+apply\b",
]

DESTRUCTIVE = [
    r"\brm\b", r"\brmdir\b", r"\bdel\b", r"\bRemove-Item\b",
    r"\bgit\s+(reset\s+--hard|clean|checkout\s+--\s|branch\s+-D)\b",
    r"\bdrop\s+(table|database)\b", r"\btruncate\b", r"\bformat\b",
    r"\bmkfs\b", r"\bdd\s+if=", r"\bshutdown\b", r"\bkill(all)?\b",
    r"\bmv\b\s", r"\bMove-Item\b", r">\s*[^>|]", r"\bTaskkill\b",
]

OUTSIDE = [
    r"\.\.[\\/]",              # parent traversal
    r"\b[A-Za-z]:[\\/]",       # windows absolute path
    r"(^|\s)/(home|etc|usr|var|opt|mnt)[\\/]",  # posix absolute path
    r"%USERPROFILE%", r"~[\\/]",
]


def _hit(patterns, text):
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def derive_features(payload, cwd=None):
    """Derived locally from the payload text. The calling agent never
    supplies these - it would have an incentive to understate them."""
    text = payload or ""
    external = int(_hit(EXTERNAL, text))
    outbound = int(_hit(OUTBOUND, text))
    destructive = int(_hit(DESTRUCTIVE, text))
    outside = int(_hit(OUTSIDE, text))

    # An absolute path that is actually inside the project is not "outside".
    if outside and cwd:
        cwd_s = str(cwd).replace("\\", "/").lower()
        if cwd_s in text.replace("\\", "/").lower():
            outside = 0

    # irreversible = you cannot undo it locally. Fetching is reversible.
    reversible = 0 if (destructive or outbound) else 1
    return {
        "touches_external": external,
        "outbound": outbound,
        "destructive": destructive,
        "outside_project": outside,
        "reversible": reversible,
    }


def normalise(payload):
    s = (payload or "").lower()
    s = re.sub(r"[a-z]:[\\/][^\s\"']*", "<path>", s)
    s = re.sub(r"(^|\s)/[^\s\"']*", " <path>", s)
    s = re.sub(r"\b[0-9a-f]{40}\b", "<hash>", s)
    s = re.sub(r"\b[0-9a-f]{7,12}\b", "<hash>", s)
    s = re.sub(r"\d+", "<n>", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def seen_count(conn, norm):
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM decisions WHERE norm_payload = ?", (norm,)
    ).fetchone()
    return row["c"]


# ------------------------------------------------------------ shadow policy
# v0. Deliberately dumb - this is a baseline to beat, not a product.
# EVALUATED BUT NEVER ENFORCED. gate.py prompts the human every time,
# including when this says auto_clear. That is the entire point of the probe.

def shadow_policy(f, seen, seen_threshold=2, external_threshold=3,
                  use_destructive=True, use_outside=True, use_external=True,
                  use_outbound=False):
    # use_outbound defaults False so v0 stays a naive baseline. The sweep in
    # metrics.py turns it on and will tell you whether it was needed.
    if use_outbound and f.get("outbound"):
        return "escalate", "outbound_write"
    if use_destructive and f["destructive"]:
        return "escalate", "destructive"
    if use_outside and f["outside_project"]:
        return "escalate", "outside_project"
    if use_external and f["touches_external"] and seen < external_threshold:
        return "escalate", "external_and_novel"
    if seen_threshold and seen < seen_threshold:
        return "escalate", "novel"
    return "auto_clear", "-"


def is_auto(conn, norm):
    row = conn.execute("SELECT summary FROM auto_rules WHERE norm_payload = ?",
                       (norm,)).fetchone()
    return row["summary"] if row else None


def add_auto(conn, norm, summary, session):
    conn.execute(
        "INSERT OR REPLACE INTO auto_rules VALUES (?, ?, ?, ?)",
        (norm, summary, datetime.now().isoformat(timespec="seconds"), session))
    conn.commit()


def drop_auto(conn, norm):
    conn.execute("DELETE FROM auto_rules WHERE norm_payload = ?", (norm,))
    conn.commit()


def add_scope(conn, session, summary, commands, paths, minutes):
    from datetime import timedelta
    now = datetime.now()
    sid = uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO scopes VALUES (?,?,?,?,?,?,?)",
        (sid, session, summary, "\n".join(commands), "\n".join(paths),
         now.isoformat(timespec="seconds"),
         (now + timedelta(minutes=minutes)).isoformat(timespec="seconds")))
    conn.commit()
    return sid


def active_scopes(conn, session):
    now = datetime.now().isoformat(timespec="seconds")
    return conn.execute(
        "SELECT * FROM scopes WHERE session = ? AND expires_at > ? "
        "ORDER BY created_at DESC", (session, now)).fetchall()


def revoke_scopes(conn, session):
    n = conn.execute("DELETE FROM scopes WHERE session = ?", (session,)).rowcount
    conn.commit()
    return n


def _globs(blob):
    return [g.strip() for g in (blob or "").split("\n") if g.strip()]


def in_scope(conn, session, payload, action_type, feats):
    """Does an active scope cover this action?

    Risk flags are checked by the caller and always win. A scope says "these
    routine things are fine", never "anything at all is fine" - otherwise it
    is indistinguishable from turning the gate off.
    """
    import fnmatch
    text = (payload or "").replace("\\", "/").strip()
    low = text.lower()
    for s in active_scopes(conn, session):
        pats = _globs(s["paths"]) if action_type != "shell" else _globs(s["commands"])
        for p in pats:
            p = p.replace("\\", "/").lower()
            if fnmatch.fnmatch(low, p) or low.startswith(p.rstrip("*")):
                return s["summary"]
            # file_write payloads are "path + description of the change"
            if action_type != "shell":
                first = low.split()[0] if low.split() else ""
                if fnmatch.fnmatch(first, p):
                    return s["summary"]
    return None


def insert(conn, **kw):
    kw.setdefault("id", uuid.uuid4().hex[:12])
    kw.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
    cols = ", ".join(kw)
    marks = ", ".join("?" for _ in kw)
    conn.execute(f"INSERT INTO decisions ({cols}) VALUES ({marks})",
                 tuple(kw.values()))
    conn.commit()
    return kw["id"]

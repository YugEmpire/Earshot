"""earshot verify - did the agent actually call the gate?

  verify.py --session S --project C:\\path\\to\\work\\project

Compares files that actually changed during the session window against the
decisions logged in that window. A change with no matching gate row is a
bypass. Read GATE COMPLIANCE before you read anything in metrics.py: under
80% the sample is biased and the other numbers are worthless.
"""

import argparse
import os
import subprocess
from datetime import datetime
from pathlib import Path

import db

IGNORE = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
          ".pytest_cache", ".idea", ".vscode", "dist", "build", ".next"}

# Files generated as a BYPRODUCT of a gated action - npm writes the lockfile
# when you approve `npm install`, tsc writes tsbuildinfo when you approve a
# typecheck. Counting these as ungated actions understates compliance.
# Keep this list short: filtering too much hides real bypasses, which is the
# whole thing this check exists to catch.
IGNORE_FILES = {"earshot.db", "package-lock.json", "yarn.lock",
                "pnpm-lock.yaml", "tsconfig.tsbuildinfo", ".DS_Store"}


def git_changed(project):
    out = set()
    for cmd in (["git", "status", "--porcelain"], ["git", "diff", "--name-only"]):
        try:
            r = subprocess.run(cmd, cwd=project, capture_output=True, text=True,
                               timeout=20)
            if r.returncode != 0:
                continue
            for line in r.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                if cmd[1] == "status":
                    line = line[2:].strip() if len(line) > 2 else line
                    line = line.split(" -> ")[-1]
                out.add(line.strip('"'))
        except (FileNotFoundError, subprocess.TimeoutExpired):
            print("  (git unavailable or not a repo - falling back to mtimes only)")
            return set()
    return out


def mtime_changed(project, t0, t1):
    out = set()
    root = Path(project)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE]
        for fn in filenames:
            if fn == "earshot.db":
                continue
            fp = Path(dirpath) / fn
            try:
                m = fp.stat().st_mtime
            except OSError:
                continue
            if t0 <= m <= t1:
                out.add(str(fp.relative_to(root)).replace("\\", "/"))
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--session", required=True)
    p.add_argument("--project", required=True)
    args = p.parse_args()

    conn = db.connect()
    rows = conn.execute(
        "SELECT * FROM decisions WHERE session = ? ORDER BY ts, rowid",
        (args.session,)).fetchall()
    if not rows:
        print(f"no decisions logged for session {args.session}")
        return

    t0 = datetime.fromisoformat(rows[0]["ts"]).timestamp()
    t1 = datetime.fromisoformat(rows[-1]["ts"]).timestamp() + 300

    changed = git_changed(args.project) | mtime_changed(args.project, t0, t1)
    changed = {c for c in changed
               if not any(part in IGNORE for part in Path(c).parts)
               and Path(c).name not in IGNORE_FILES}

    blob = " ".join((r["payload"] or "") + " " + (r["summary"] or "")
                    for r in rows).replace("\\", "/").lower()

    matched, unmatched = [], []
    for f in sorted(changed):
        name = Path(f).name.lower()
        (matched if (f.lower() in blob or name in blob) else unmatched).append(f)

    print("=" * 62)
    print(f"session {args.session}   decisions logged: {len(rows)}")
    print(f"files changed in window: {len(changed)}")
    print(f"\nMATCHED   {len(matched)}")
    for f in matched[:20]:
        print(f"  ok   {f}")
    print(f"\nUNMATCHED {len(unmatched)}   <- changed with no gate row = bypass")
    for f in unmatched[:40]:
        print(f"  ??   {f}")

    total = len(matched) + len(unmatched)
    if total == 0:
        print("\nnothing changed on disk - cannot compute compliance.")
        return
    comp = len(matched) / total
    print("\n" + "=" * 62)
    print(f"GATE COMPLIANCE: {comp:.0%}")
    if comp < 0.8:
        print("UNDER 80% - the agent was bypassing you. Your sample is biased")
        print("toward the actions it chose to ask about. Do NOT read metrics.py")
        print("on this session; fix enforcement and run again.")
    else:
        print("Good enough. Proceed to metrics.py.")


if __name__ == "__main__":
    main()

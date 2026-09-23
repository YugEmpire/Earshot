r"""earshot preflight - check everything BEFORE committing a session to it.

  py preflight.py --cursor D:\rostercore --session 2026-09-06-hands
  py preflight.py --claude D:\rostercore --session claude-code

Six sessions were lost to a stale file or a mistyped label, each discovered
only after hours of work. Every one was catchable in sixty seconds. This is
those sixty seconds.

Prints a fix for anything that fails, not just a complaint.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
fails = []


def check(name, ok, fix=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        if fix:
            print(f"          fix: {fix}")
        fails.append(name)
    return ok


def has(path, pattern):
    p = os.path.join(HERE, path)
    if not os.path.exists(p):
        return False
    try:
        return re.search(pattern, open(p, encoding="utf-8",
                                       errors="ignore").read()) is not None
    except Exception:
        return False


def core():
    print("\n--- probe files ---")
    for f, pat, why in [
        ("db.py", r"answered_via", "records how you answered"),
        ("db.py", r"auto_rules", "consent-based auto-approve"),
        ("db.py", r"CREATE TABLE IF NOT EXISTS scopes", "scope pre-approval"),
        ("gate.py", r"def scope_ok", "gate honours scopes"),
        ("approve.py", r"def offer_auto", "offers to stop asking"),
        ("approve.py", r"That was a reject", "confirms refusals"),
        ("voice.py", r"def kokoro_available", "kokoro built in"),
        ("voice.py", r"def confirm", "spoken yes/no"),
        ("voice.py", r"def dictate", "spoken notes"),
        ("listen.py", r"small\.en", "english-only whisper"),
        ("listen.py", r"def listen_text", "free dictation"),
        ("metrics.py", r"answered by voice", "reports voice share"),
    ]:
        check(f"{f}: {why}", has(f, pat),
              f"re-download {f} - it is an older copy")

    print("\n--- speech ---")
    try:
        sys.path.insert(0, HERE)
        import listen
        check("faster-whisper installed", listen.available(),
              "pip install faster-whisper sounddevice numpy")
    except Exception as e:
        check("listen.py imports", False, str(e)[:60])

    kok_py = os.path.join(HERE, ".tts", "Scripts", "python.exe")
    kok_srv = os.path.join(HERE, "tts_server.py")
    if os.path.exists(kok_py) or os.path.exists(kok_srv):
        check("kokoro venv", os.path.exists(kok_py),
              "py -3.12 -m venv .tts  (then pip install kokoro soundfile sounddevice)")
        check("kokoro worker script", os.path.exists(kok_srv),
              "recreate tts_server.py")
    else:
        print("  skip  kokoro not installed - the Windows voice will be used")

    print("\n--- database ---")
    try:
        import db
        conn = db.connect()
        n = conn.execute("SELECT COUNT(*) c FROM decisions").fetchone()["c"]
        check(f"database opens ({n} decisions on record)", True)
    except Exception as e:
        check("database opens", False, str(e)[:60])


def cursor(project, session):
    print(f"\n--- cursor: {project} ---")
    rules = os.path.join(project, ".cursorrules")
    if not check(".cursorrules exists", os.path.exists(rules),
                 f"create {rules}"):
        return
    text = open(rules, encoding="utf-8", errors="ignore").read()

    labels = set(re.findall(r"--session\s+(\S+)", text))
    check(f"session label is {session} in every gate line",
          labels == {session},
          f"found {labels or 'none'} - both lines must say {session}")
    check("gate lines: exactly two (run and ask)",
          len(re.findall(r"gate\.py\s+(run|ask)\b", text)) == 2,
          "one run line and one ask line")
    check("plain-sentence summary rule (not the six-word version)",
          "fourteen" in text.lower(),
          "replace with cursorrules-voice-v2 - the old six-word rule is stale")
    check("scope block present",
          "gate.py scope" in text,
          "add the scope command block so a phase can be pre-approved once")
    check("scope tells it to RUN the command, not ask in chat",
          "Do not ask me in chat" in text or "do not ask me in chat" in text.lower(),
          "without this the agent uses its own question tool and bypasses voice")
    check("no leftover instructions to you",
          "Paste into" not in text and "Also add" not in text,
          "delete the header/footer lines - the agent tries to follow them")

    gp = re.findall(r"([A-Za-z]:\\[^\s\"]*gate\.py)", text)
    for p in set(gp):
        check(f"gate path exists: {p}", os.path.exists(p),
              "fix the absolute path in .cursorrules")


def claude(project, session):
    print(f"\n--- claude code: {project} ---")
    check("hook_claude.py present", os.path.exists(os.path.join(HERE, "hook_claude.py")),
          "download hook_claude.py into the probe folder")

    settings = os.path.join(project, ".claude", "settings.json")
    if not check(".claude/settings.json exists", os.path.exists(settings),
                 f"create {settings} with the PreToolUse hook"):
        return
    try:
        cfg = json.load(open(settings, encoding="utf-8"))
    except Exception as e:
        check("settings.json is valid JSON", False, str(e)[:60])
        return
    check("settings.json is valid JSON", True)

    hooks = (cfg.get("hooks") or {}).get("PreToolUse") or []
    check("PreToolUse hook configured", bool(hooks),
          'add hooks.PreToolUse with matcher "Bash|Edit|Write|MultiEdit"')
    if not hooks:
        return

    blob = json.dumps(hooks)
    check("matcher covers Bash and file edits",
          "Bash" in blob and "Edit" in blob and "Write" in blob,
          'matcher should be "Bash|Edit|Write|MultiEdit|NotebookEdit"')
    check("hook points at hook_claude.py", "hook_claude.py" in blob,
          "the command must run hook_claude.py, not gate.py")
    check("timeout is generous enough to answer by voice",
          any(h.get("timeout", 0) >= 300
              for grp in hooks for h in (grp.get("hooks") or [])),
          "set timeout to 600 - a short timeout means the hook gives up on you")

    for p in set(re.findall(r"([A-Za-z]:\\\\[^\s\"]*?\.(?:py|exe))", blob)):
        real = p.replace("\\\\", "\\")
        check(f"path exists: {real}", os.path.exists(real),
              "fix the absolute path in settings.json")

    check("claude command on PATH", shutil.which("claude") is not None,
          "npm install -g @anthropic-ai/claude-code")
    if session:
        print(f"  note   set EARSHOT_SESSION={session} in the window that runs claude")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cursor", metavar="PROJECT")
    p.add_argument("--claude", metavar="PROJECT")
    p.add_argument("--session", default=None)
    args = p.parse_args()

    print("=" * 62)
    print("earshot preflight")
    print("=" * 62)
    core()
    if args.cursor:
        cursor(args.cursor, args.session)
    if args.claude:
        claude(args.claude, args.session)

    print("\n" + "=" * 62)
    if fails:
        print(f"{len(fails)} problem(s). Fix these before starting:")
        for f in fails:
            print(f"  - {f}")
        print("\nA session started now would produce data you cannot use.")
        return 1
    print("All checks passed.")
    print("Start the approver, then confirm ONE gate call by voice before")
    print("giving the agent real work.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

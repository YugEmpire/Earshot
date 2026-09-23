"""earshot selftest - check every part of the rig in one pass.

  python selftest.py

Runs on a throwaway session label and deletes its own rows at the end, so it
never pollutes measurement data.

Checks, in dependency order - a failure early makes later results meaningless,
so it stops rather than reporting a cascade:

  1  database opens and writes
  2  gate queues a request instead of prompting
  3  approver picks it up and the answer flows back
  4  speech OUT - you confirm you heard it
  5  Whisper installed and the model loads
  6  microphone captures audio
  7  speech IN - transcribe and map to an intent
  8  metrics and verify run
"""

import os
import sqlite3
import subprocess
import sys
import threading
import time

SESSION = "__selftest__"
HERE = os.path.dirname(os.path.abspath(__file__))

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
results = []


def report(name, status, detail=""):
    mark = {"PASS": "  ok  ", "FAIL": " FAIL ", "SKIP": " skip "}[status]
    print(f"{mark} {name}" + (f"   {detail}" if detail else ""))
    results.append((name, status, detail))
    return status == PASS


def ask(question):
    try:
        return input(f"        {question} [y/n] ").strip().lower().startswith("y")
    except (EOFError, KeyboardInterrupt):
        return False


# ------------------------------------------------------------------- checks

def check_db():
    try:
        import db
        conn = db.connect()
        conn.execute("SELECT COUNT(*) FROM decisions").fetchone()
        return report("database opens", PASS, str(db.DB_PATH))
    except Exception as e:
        return report("database opens", FAIL, str(e)[:70])


def check_gate_and_approver():
    """The gate must QUEUE, not prompt. If it prompts, it will hang forever
    under Cursor, which is the bug that produced a fabricated rejection."""
    import db
    conn = db.connect()
    conn.execute("DELETE FROM decisions WHERE session = ?", (SESSION,))
    conn.commit()

    env = dict(os.environ, EARSHOT_TIMEOUT="25")
    proc = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "gate.py"), "run",
         "--session", SESSION, "--summary", "checking the rig still works",
         "--cmd", "echo SELFTEST_RAN"],
        cwd=HERE, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True)

    row = None
    for _ in range(60):
        time.sleep(0.25)
        row = conn.execute(
            "SELECT * FROM decisions WHERE session = ? AND human_decision IS NULL",
            (SESSION,)).fetchone()
        if row:
            break
    if not row:
        proc.kill()
        return report("gate queues the request", FAIL,
                      "no pending row appeared - is gate.py current?")
    report("gate queues the request", PASS, f"id {row['id']}")

    # answer it the way approve.py would
    conn.execute("UPDATE decisions SET human_decision='approve', latency_ms=1 "
                 "WHERE id = ?", (row["id"],))
    conn.commit()

    try:
        out, err = proc.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        return report("gate acts on the answer", FAIL, "gate did not return")

    if "SELFTEST_RAN" in (out or "") + (err or ""):
        return report("gate runs the command after approval", PASS)
    return report("gate runs the command after approval", FAIL,
                  "approved but the command did not execute")


def check_speak():
    try:
        import voice
    except Exception as e:
        return report("speech out", FAIL, str(e)[:70])
    sample = {"summary": "deleting the old build folder so the next one starts clean",
              "destructive": 1, "outbound": 0, "touches_external": 0,
              "outside_project": 0, "seen_count": 3}
    text = voice.phrase(sample)
    print(f"        speaking: {text}")
    try:
        voice.speak(text)
    except Exception as e:
        return report("speech out", FAIL, str(e)[:70])
    if ask("did you hear that clearly, at a comfortable speed?"):
        return report("speech out", PASS)
    return report("speech out", FAIL,
                  "adjust: $env:EARSHOT_RATE='-3'  $env:EARSHOT_VOICE='Zira'")


def check_whisper_installed():
    try:
        import listen
    except Exception as e:
        return report("whisper module", FAIL, str(e)[:70])
    if not listen.available():
        return report("whisper installed", FAIL,
                      "pip install faster-whisper sounddevice numpy")
    report("whisper installed", PASS)
    print(f"        loading model '{listen.MODEL_NAME}' (first run downloads it)...")
    try:
        t0 = time.time()
        listen._load_model()
        return report("whisper model loads", PASS, f"{time.time()-t0:.1f}s")
    except Exception as e:
        return report("whisper model loads", FAIL, str(e)[:70])


def check_mic():
    import listen
    print("        say something for a couple of seconds...")
    try:
        audio = listen.record(max_seconds=6)
    except Exception as e:
        return report("microphone captures", FAIL, str(e)[:70])
    if audio is None:
        return report("microphone captures", FAIL,
                      "silence - check Settings > System > Sound > Input")
    import numpy as np
    peak = float(np.max(np.abs(audio)))
    if peak < 0.01:
        return report("microphone captures", FAIL, f"level too low ({peak:.3f})")
    return report("microphone captures", PASS, f"{len(audio)/16000:.1f}s, peak {peak:.2f}")


def check_speech_in():
    import listen
    ok = 0
    for want, word in (("a", "approve"), ("r", "reject"), ("m", "change")):
        print(f'        say "{word}" (or any word meaning it, any language)...')
        intent, detail = listen.listen(max_seconds=6)
        if intent == want:
            print(f"          -> {intent}  {detail}")
            ok += 1
        else:
            print(f"          -> got {intent}, wanted {want}   {detail}")
    if ok == 3:
        return report("speech in maps correctly", PASS, "3/3")
    if ok >= 1:
        return report("speech in maps correctly", FAIL,
                      f"{ok}/3 - try $env:EARSHOT_MODEL='small'")
    return report("speech in maps correctly", FAIL, "0/3")


def check_tools():
    ok = True
    for script, args in (("metrics.py", ["--session", SESSION]),
                         ("verify.py", ["--session", SESSION,
                                        "--project", HERE])):
        try:
            r = subprocess.run([sys.executable, os.path.join(HERE, script), *args],
                               cwd=HERE, capture_output=True, text=True, timeout=90)
            ok &= report(f"{script} runs", PASS if r.returncode == 0 else FAIL,
                         (r.stderr or "").strip().splitlines()[-1][:60]
                         if r.returncode else "")
        except Exception as e:
            ok &= report(f"{script} runs", FAIL, str(e)[:60])
    return ok


def cleanup():
    try:
        import db
        conn = db.connect()
        n = conn.execute("DELETE FROM decisions WHERE session = ?",
                         (SESSION,)).rowcount
        conn.commit()
        print(f"\n  cleaned up {n} selftest rows - your session data is untouched")
    except Exception as e:
        print("  cleanup failed:", e)


def main():
    print("=" * 62)
    print("earshot selftest")
    print("=" * 62)

    if not check_db():
        print("\nstopping - nothing else can work without the database")
        return 1

    print("\n--- gate and approver ---")
    check_gate_and_approver()

    print("\n--- speech out ---")
    check_speak()

    print("\n--- speech in ---")
    if check_whisper_installed():
        if check_mic():
            check_speech_in()
        else:
            report("speech in maps correctly", SKIP, "no microphone input")
    else:
        report("microphone captures", SKIP, "whisper unavailable")
        report("speech in maps correctly", SKIP, "whisper unavailable")

    print("\n--- reporting tools ---")
    check_tools()

    cleanup()

    print("\n" + "=" * 62)
    bad = [r for r in results if r[1] == FAIL]
    skipped = [r for r in results if r[1] == SKIP]
    print(f"{len(results)-len(bad)-len(skipped)} passed, "
          f"{len(bad)} failed, {len(skipped)} skipped")
    for name, _, detail in bad:
        print(f"  FAILED: {name}   {detail}")
    if not bad:
        print("\nEverything works. Voice in and out are both live.")
        print("Note: adding voice INPUT changes a variable session 3 did not")
        print("have, so a run with it on is not comparable to the 7.4s median.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

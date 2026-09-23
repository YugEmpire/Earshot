"""earshot approver - the window you keep open and answer from.

  python approve.py

Cursor runs the agent's commands with stdin closed, so the gate cannot prompt
you inside the agent's own terminal. It writes each request to the database as
pending; this process picks it up, shows it to you, and writes your answer back.

Keep this running in its own PowerShell window all session. If it isn't
running, the gate waits, warns, and eventually times out - it will never
invent an answer on your behalf.

Latency is measured from the moment the request was queued to the moment you
answer, so it includes you noticing. That is the honest number for a voice
product: the agent is blocked that whole time.
"""

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime

import db
import voice

POLL_S = 0.4
BAR = "-" * 62


def flags_of(row):
    f = []
    if row["destructive"]:
        f.append("DESTRUCTIVE")
    if row["outbound"]:
        f.append("OUTBOUND")
    if row["touches_external"]:
        f.append("external")
    if row["outside_project"]:
        f.append("outside-project")
    if not row["reversible"]:
        f.append("irreversible")
    return " ".join(f) if f else "none"


def show(row, queued_at):
    waited = (datetime.now() - queued_at).total_seconds()
    print(f"\n{BAR}")
    print(f"  {row['session']}  |  {row['mode']}  |  waited {waited:.0f}s")
    print(f"  {row['summary']}")
    print(f"  $ {row['payload']}")
    print(f"  flags: {flags_of(row)}   seen before: {row['seen_count']}")
    print(BAR)


def answer(conn, row, queued_at, use_voice=False):
    show(row, queued_at)
    ans = None
    answered_via = "key"
    if use_voice:
        # Speak first and wait for it to finish - the recogniser would
        # otherwise hear the synthesiser and answer its own question.
        voice.speak(voice.phrase(row))
        # Two retries, then the keyboard. An unmapped answer is asked again
        # rather than guessed: guessing here runs a command nobody approved.
        for attempt in range(3):
            if attempt:
                voice.speak("Sorry, I did not catch that. "
                            "Say approve, reject, or change.")
            print("  say approve / reject / change, or press a r m > ",
                  end="", flush=True)
            try:
                ans, how = voice.listen_or_key()
            except KeyboardInterrupt:
                print("\n  (leaving it pending - the gate is still waiting)")
                return False
            if ans:
                answered_via = how
                print(f"{ans}   [{how}]")
                break
            print(f"\n  (voice: {how})")
        if not ans:
            print("  giving up on voice for this one - type it:")

    while not ans:
        try:
            print("  [a]pprove  [r]eject  [m]odify > ", end="", flush=True)
            raw = input().strip().lower()
        except KeyboardInterrupt:
            print("\n  (leaving it pending - the gate is still waiting)")
            return False
        if raw[:1] in ("a", "r", "m"):
            ans = raw[:1]
    decision = {"a": "approve", "r": "reject", "m": "modify"}[ans[:1]]

    # A misheard "reject" costs real work, and it has already happened once -
    # a four-minute reject followed immediately by the same action approved.
    # Confirm before refusing, never before allowing: an extra question on
    # approve would just be friction, but on reject it catches the error.
    if decision == "reject" and use_voice:
        sure = voice.confirm("That was a reject. Are you sure? Say yes or no.")
        if sure is False:
            print("  reject cancelled - asking again")
            voice.speak("Cancelled. " + voice.phrase(row))
            again, how2 = voice.listen_or_key()
            if again:
                decision = {"a": "approve", "r": "reject", "m": "modify"}[again]
                answered_via = how2
                print(f"  changed to {decision}   [{how2}]")
        elif sure is None:
            print("  could not confirm - keeping the reject")

    note = None
    if decision != "approve":
        if use_voice:
            note = voice.dictate(
                "Any note? Say it, or say skip.")
            print(f"  note: {note}" if note else "  note skipped")
        else:
            try:
                note = input("  note (enter to skip) > ").strip() or None
            except KeyboardInterrupt:
                note = None

    latency = int((datetime.now() - queued_at).total_seconds() * 1000)
    conn.execute(
        "UPDATE decisions SET human_decision = ?, human_note = ?, latency_ms = ?, "
        "answered_via = ? WHERE id = ?",
        (decision, note, latency, answered_via, row["id"]))
    conn.commit()
    print(f"  -> {decision}  ({latency/1000:.1f}s)   "
          f"[policy would have: {row['shadow_verdict']}]")

    if decision == "approve" and use_voice:
        offer_auto(conn, row)
    return True


def offer_auto(conn, row, after=2):
    """After enough clean approvals, ask whether to stop asking at all.

    Asked, not assumed. The user decides which actions leave the loop, and the
    risk flags still veto - familiarity never earns an exemption for something
    destructive.
    """
    if row["destructive"] or row["outbound"] or row["outside_project"] \
            or not row["reversible"]:
        return
    if db.is_auto(conn, row["norm_payload"]):
        return

    hist = [h["human_decision"] for h in conn.execute(
        "SELECT human_decision FROM decisions WHERE norm_payload = ? "
        "AND human_decision IN ('approve','reject','modify') ORDER BY ts",
        (row["norm_payload"],)).fetchall()]

    # Count consecutive approvals since the LAST pushback, not across all
    # history. Requiring a spotless record forever means one misheard reject
    # disqualifies a command permanently - which is why a typecheck approved
    # 27 times was never offered.
    streak = 0
    for h in reversed(hist):
        if h != "approve":
            break
        streak += 1
    if streak < after:
        return

    ok = voice.confirm(
        f"You have allowed this {len(hist)} times. "
        "Shall I stop asking about it? Say yes or no.")
    if ok:
        db.add_auto(conn, row["norm_payload"], row["summary"], row["session"])
        voice.speak("Done. I will not ask about that one again.")
        print(f"  AUTO: will no longer ask about {row['summary'][:40]!r}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--voice", action="store_true",
                   help="speak requests aloud and listen for a spoken answer")
    args = p.parse_args()

    conn = db.connect()
    if args.voice:
        voice.prewarm()      # load the TTS model while you read this banner
    print(f"earshot approver running{' [VOICE]' if args.voice else ''}."
          "  Ctrl+C twice to quit.")
    print(f"db: {db.DB_PATH}")
    print("Leave this window open and answer here. The gate waits on you.\n")
    idle_since = time.time()
    while True:
        try:
            row = conn.execute(
                "SELECT * FROM decisions WHERE human_decision IS NULL "
                "ORDER BY ts LIMIT 1").fetchone()
            if row is None:
                if time.time() - idle_since > 60:
                    print(f"  ... waiting  {datetime.now():%H:%M}", end="\r")
                    idle_since = time.time()
                time.sleep(POLL_S)
                continue
            queued_at = datetime.fromisoformat(row["ts"])
            answer(conn, row, queued_at, use_voice=args.voice)
            idle_since = time.time()
        except KeyboardInterrupt:
            print("\nstopped.")
            return 0
        except sqlite3.OperationalError:
            time.sleep(POLL_S)   # db momentarily locked by the gate; retry


if __name__ == "__main__":
    sys.exit(main())

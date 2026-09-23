"""earshot gate - the agent calls this instead of acting.

  gate.py run  --session S --summary "..." --cmd "<command>"
  gate.py ask  --session S --type file_write --summary "..." --payload "..."
  gate.py outcome --id <id> --outcome harm --note "..."

Exit codes:  0 approve   1 reject   2 modify   (run mode: real exit code on approve)

The shadow policy is evaluated and stored on every call but NEVER acted on.
You are prompted every time, including for decisions the policy would have
cleared. Without that you have no ground truth on the decisions the policy
skips, and false-clear rate is unmeasurable.
"""

import argparse
import os
import subprocess
import sys
import time

import db

# How long the gate waits for an out-of-band answer before giving up.
WAIT_TIMEOUT_S = int(os.environ.get("EARSHOT_TIMEOUT", "900"))

# Auto-approve after N prior approvals of the same normalised action.
# 0 disables it. This is the "you've said yes twice, stop asking" rule.
AUTO_AFTER = int(os.environ.get("EARSHOT_AUTO_AFTER", "0"))
POLL_S = 0.4

BAR = "-" * 62


def prompt(summary, payload, feats, seen, verdict):
    flags = []
    if feats["destructive"]:
        flags.append("DESTRUCTIVE")
    if feats["touches_external"]:
        flags.append("external")
    if feats["outside_project"]:
        flags.append("outside-project")
    if not feats["reversible"]:
        flags.append("irreversible")
    flag_s = " ".join(flags) if flags else "none"

    print(f"\n{BAR}", file=sys.stderr)
    print(f"  {summary}", file=sys.stderr)
    print(f"  $ {payload}", file=sys.stderr)
    print(f"  flags: {flag_s}   seen before: {seen}", file=sys.stderr)
    print(BAR, file=sys.stderr)

    start = time.perf_counter()
    while True:
        try:
            ans = input("  [a]pprove  [r]eject  [m]odify > ").strip().lower()
        except EOFError:
            # Never treat "no input available" as a rejection - that fabricates
            # a decision the human never made and poisons the data.
            print("\n  ERROR: no stdin. Run through approve.py instead.",
                  file=sys.stderr)
            sys.exit(70)
        except KeyboardInterrupt:
            ans = "r"
            print("", file=sys.stderr)
        if ans[:1] in ("a", "r", "m"):
            break
    latency = int((time.perf_counter() - start) * 1000)

    decision = {"a": "approve", "r": "reject", "m": "modify"}[ans[:1]]
    note = None
    if decision != "approve":
        try:
            note = input("  note (enter to skip) > ").strip() or None
        except (EOFError, KeyboardInterrupt):
            note = None
    return decision, note, latency


def auto_ok(conn, feats, norm):
    """Should this be cleared without asking?

    Familiarity is never enough on its own. An action you have approved fifty
    times is still destructive the fifty-first time, and "I've seen it before"
    is exactly how `rm -rf` gets waved through. So the risk flags are a hard
    veto that no amount of repetition overrides.
    """
    # Risk flags veto everything, including explicit consent. Someone who
    # said "always allow" for a command last week did not mean the version of
    # it that deletes something today.
    if feats["destructive"] or feats["outbound"] or feats["outside_project"] \
            or not feats["reversible"]:
        return False, "risky - never auto-cleared"

    agreed = db.is_auto(conn, norm)
    if agreed:
        return True, "you asked me to stop checking this one"
    return False, ""


def scope_ok(conn, session, payload, action_type, feats):
    if feats["destructive"] or feats["outbound"] or feats["outside_project"] \
            or not feats["reversible"]:
        return None          # risk always interrupts, scope or no scope
    return db.in_scope(conn, session, payload, action_type, feats)


def wait_for_answer(conn, row_id):
    """Queue mode. stdin is closed (Cursor runs commands non-interactively),
    so the prompt cannot happen here. The row is written as pending and
    approve.py - running in a window the human can actually type into - picks
    it up. This is the out-of-band approval channel, not a workaround.
    """
    print(f"\n  [{row_id}] waiting for approval in the approver window...",
          file=sys.stderr)
    start = time.perf_counter()
    while time.perf_counter() - start < WAIT_TIMEOUT_S:
        row = conn.execute(
            "SELECT human_decision, human_note, latency_ms FROM decisions "
            "WHERE id = ?", (row_id,)).fetchone()
        if row and row["human_decision"]:
            return row["human_decision"], row["human_note"], row["latency_ms"]
        time.sleep(POLL_S)
    conn.execute("UPDATE decisions SET human_decision = 'timeout' WHERE id = ?",
                 (row_id,))
    conn.commit()
    print("  TIMED OUT - no approver window running? Start approve.py.",
          file=sys.stderr)
    return "timeout", None, None


def handle(args, mode):
    payload = args.cmd if mode == "run" else args.payload
    action_type = "shell" if mode == "run" else args.type
    cwd = os.getcwd()

    conn = db.connect()
    feats = db.derive_features(payload, cwd)
    norm = db.normalise(payload)
    seen = db.seen_count(conn, norm)
    verdict, rule = db.shadow_policy(feats, seen)

    # Queue mode is the DEFAULT. Auto-detection is not reliable on Windows:
    # NUL is a character device, so isatty() returns True even with no stdin,
    # and a gate that guesses wrong either hangs or fabricates a decision.
    # Explicit beats clever - inline prompting is opt-in via --interactive.
    covering = scope_ok(conn, args.session, payload, action_type, feats)
    auto, why = auto_ok(conn, feats, norm)
    if covering and not auto:
        auto, why = True, f"covered by scope: {covering}"
    if auto:
        row_id = db.insert(
            conn, session=args.session, mode=mode, action_type=action_type,
            summary=args.summary, payload=payload, norm_payload=norm,
            seen_count=seen, human_decision="auto_approve", human_note=why,
            latency_ms=0, exit_code=None, shadow_verdict=verdict,
            shadow_rule=rule, outcome="unknown", **feats)
        print(f"  [{row_id}] auto-approved ({why})", file=sys.stderr)
        if mode == "run":
            proc = subprocess.run(payload, shell=True, cwd=cwd)
            conn.execute("UPDATE decisions SET exit_code = ? WHERE id = ?",
                         (proc.returncode, row_id))
            conn.commit()
            return proc.returncode
        return 0

    interactive = bool(getattr(args, "interactive", False))

    if interactive:
        decision, note, latency = prompt(args.summary, payload, feats, seen, verdict)
        row_id = db.insert(
            conn, session=args.session, mode=mode, action_type=action_type,
            summary=args.summary, payload=payload, norm_payload=norm,
            seen_count=seen, human_decision=decision, human_note=note,
            latency_ms=latency, exit_code=None,
            shadow_verdict=verdict, shadow_rule=rule, outcome="unknown", **feats,
        )
    else:
        row_id = db.insert(
            conn, session=args.session, mode=mode, action_type=action_type,
            summary=args.summary, payload=payload, norm_payload=norm,
            seen_count=seen, human_decision=None, human_note=None,
            latency_ms=None, exit_code=None,
            shadow_verdict=verdict, shadow_rule=rule, outcome="unknown", **feats,
        )
        decision, note, latency = wait_for_answer(conn, row_id)

    exit_code = None
    if decision == "approve" and mode == "run":
        proc = subprocess.run(payload, shell=True, cwd=cwd)
        exit_code = proc.returncode
        conn.execute("UPDATE decisions SET exit_code = ? WHERE id = ?",
                     (exit_code, row_id))
        conn.commit()

    print(f"  [{row_id}] {decision}  (policy would have: {verdict})",
          file=sys.stderr)

    if decision == "timeout":
        return 1
    if decision == "reject":
        return 1
    if decision == "modify":
        return 2
    return exit_code if exit_code is not None else 0


def main():
    p = argparse.ArgumentParser(prog="gate")
    sub = p.add_subparsers(dest="subcmd", required=True)

    r = sub.add_parser("run", help="log, prompt, then execute a shell command")
    r.add_argument("--session", required=True)
    r.add_argument("--summary", required=True)
    r.add_argument("--cmd", required=True)
    r.add_argument("--interactive", action="store_true",
                   help="prompt inline instead of queueing to approve.py")

    a = sub.add_parser("ask", help="log and prompt only; never executes")
    a.add_argument("--session", required=True)
    a.add_argument("--summary", required=True)
    a.add_argument("--payload", required=True)
    a.add_argument("--type", default="file_write",
                   choices=["file_write", "file_delete", "plan", "other"],
                   help="plan = task-level gate: one approval covering a batch "
                        "of work, reviewed afterwards with `gate.py outcome`")
    a.add_argument("--interactive", action="store_true",
                   help="prompt inline instead of queueing to approve.py")

    sc = sub.add_parser("scope", help="ask once for a batch of routine actions")
    sc.add_argument("--session", required=True)
    sc.add_argument("--summary", required=True,
                    help="spoken out loud - say what is being pre-approved")
    sc.add_argument("--commands", default="",
                    help="semicolon-separated globs, e.g. 'npx tsc*;node --test*'")
    sc.add_argument("--paths", default="",
                    help="semicolon-separated globs, e.g. 'src/*;configs/*'")
    sc.add_argument("--minutes", type=int, default=90)

    ss = sub.add_parser("scopes", help="show or revoke active scopes")
    ss.add_argument("--session", required=True)
    ss.add_argument("--revoke", action="store_true")

    o = sub.add_parser("outcome", help="label a past decision after the fact")
    o.add_argument("--id", required=True)
    o.add_argument("--outcome", required=True, choices=["ok", "harm", "unknown"])
    o.add_argument("--note", default=None)

    args = p.parse_args()

    if args.subcmd == "scopes":
        conn = db.connect()
        if args.revoke:
            print(f"revoked {db.revoke_scopes(conn, args.session)} scope(s)")
            return 0
        rows = db.active_scopes(conn, args.session)
        if not rows:
            print("no active scopes")
        for r in rows:
            print(f"  {r['summary']}\n    until {r['expires_at']}")
            print(f"    commands: {r['commands'] or '(none)'}")
            print(f"    paths:    {r['paths'] or '(none)'}")
        return 0

    if args.subcmd == "scope":
        conn = db.connect()
        cmds = [c.strip() for c in args.commands.split(";") if c.strip()]
        paths = [p.strip() for p in args.paths.split(";") if p.strip()]
        norm = db.normalise("SCOPE " + args.summary)
        row_id = db.insert(
            conn, session=args.session, mode="ask", action_type="scope",
            summary=args.summary,
            payload=f"commands: {cmds}  paths: {paths}  for {args.minutes}m",
            norm_payload=norm, seen_count=0, human_decision=None,
            human_note=None, latency_ms=None, exit_code=None,
            shadow_verdict="escalate", shadow_rule="scope request",
            outcome="unknown", **db.derive_features(args.summary, os.getcwd()))
        decision, _note, _lat = wait_for_answer(conn, row_id)
        if decision != "approve":
            print(f"  scope refused ({decision})", file=sys.stderr)
            return 1
        sid = db.add_scope(conn, args.session, args.summary, cmds, paths,
                           args.minutes)
        print(f"  scope {sid} active for {args.minutes} minutes",
              file=sys.stderr)
        return 0

    if args.subcmd == "outcome":
        conn = db.connect()
        cur = conn.execute(
            "UPDATE decisions SET outcome = ?, human_note = COALESCE(?, human_note) "
            "WHERE id = ?", (args.outcome, args.note, args.id))
        conn.commit()
        print("updated" if cur.rowcount else "no such id", file=sys.stderr)
        return 0 if cur.rowcount else 1

    return handle(args, args.subcmd)


if __name__ == "__main__":
    sys.exit(main())

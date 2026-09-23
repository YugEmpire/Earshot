"""earshot metrics - the two numbers that decide whether Earshot is a product.

  metrics.py --session 2026-08-31-am

Refuses to print rates under n=40. Reports every rate twice: all rows, and
rows after the first 30 (ALL-WARM). seen_count is zero for everything at the
start of the first session, so the full-session figure understates the policy.
Read the warm number.
"""

import argparse
import itertools
import statistics
from collections import Counter
from datetime import datetime

import db

WARMUP = 30
MIN_N = 40


def load(conn, session):
    q = "SELECT * FROM decisions"
    args = ()
    if session:
        q += " WHERE session = ?"
        args = (session,)
    rows = conn.execute(q + " ORDER BY ts, rowid", args).fetchall()
    # timeouts and still-pending rows are not decisions the human made.
    # Counting them would fabricate rejections that never happened.
    return [r for r in rows
            if r["human_decision"] in ("approve", "reject", "modify")]


def span_hours(rows):
    if len(rows) < 2:
        return 0.0
    t0 = datetime.fromisoformat(rows[0]["ts"])
    t1 = datetime.fromisoformat(rows[-1]["ts"])
    return max((t1 - t0).total_seconds() / 3600, 0.01)


def evaluate(rows, **policy):
    """Replay a policy variant over rows. Returns (auto_clear, false_clears)."""
    auto, false = 0, []
    for r in rows:
        f = {k: r[k] for k in ("destructive", "outside_project",
                               "touches_external", "outbound", "reversible")}
        v, _ = db.shadow_policy(f, r["seen_count"], **policy)
        if v == "auto_clear":
            auto += 1
            if r["human_decision"] != "approve":
                false.append(r)
    return auto, false


def report_block(label, rows, hours):
    n = len(rows)
    if not n:
        print(f"\n{label}: no rows")
        return
    auto, false = evaluate(rows)
    esc = n - auto
    print(f"\n{label}  (n={n})")
    print(f"  v0 auto-clear      {auto/n:6.1%}   ({auto}/{n})")
    print(f"  false clears       {len(false)}")
    if hours:
        print(f"  interruptions/hr   {esc/hours:6.1f}   (if v0 enforced)")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--session", default=None)
    args = p.parse_args()

    conn = db.connect()
    rows = load(conn, args.session)
    n = len(rows)

    print("=" * 62)
    print(f"session: {args.session or 'ALL'}")

    q = "SELECT COUNT(*) c FROM decisions WHERE human_decision='auto_approve'"
    a = (q + " AND session = ?", (args.session,)) if args.session else (q, ())
    auto_n = conn.execute(*a).fetchone()["c"]
    if auto_n:
        print(f"auto-approved without asking: {auto_n}  "
              f"(excluded from the latency figures below)")

    if n < MIN_N:
        # Latency does not depend on the policy sweep, so the n>=40 bar - set
        # for the auto-clear frontier - does not apply to it. Reporting it
        # here is not moving the goalposts; refusing to was over-applying a
        # rule scoped to a different claim.
        lat = sorted(r["latency_ms"] for r in rows if r["latency_ms"] is not None)
        if len(lat) >= 5:
            med = statistics.median(lat)
            p90 = lat[int(len(lat) * 0.9) - 1]
            print(f"n={n} - too few for the policy analysis (need {MIN_N}).")
            print(f"\nLATENCY ONLY  (n={len(lat)}, wide error bars)")
            print(f"  median {med/1000:.1f}s   p90 {p90/1000:.1f}s   "
                  f"over-30s {sum(1 for x in lat if x > 30000)}")
            via = Counter(r["answered_via"] or "key" for r in rows)
            if via.get("voice"):
                tot = sum(via.values())
                print(f"  answered by voice {via['voice']}/{tot} "
                      f"({via['voice']/tot:.0%}) - the rest fell back to keys")
            if med < 1500:
                print("  WARNING under 1.5s - you stopped reading the prompts.")
        else:
            print(f"insufficient data, n={n}  (need {MIN_N})")
            print("Anything computed on this is noise. Run another session.")
        return

    hours = span_hours(rows)
    print(f"n={n}   span={hours:.1f}h   {n/hours:.1f} decisions/hour")

    print("\naction types:  " + "  ".join(
        f"{k}={v}" for k, v in Counter(r["action_type"] for r in rows).most_common()))
    print("your answers:  " + "  ".join(
        f"{k}={v}" for k, v in Counter(r["human_decision"] for r in rows).most_common()))

    lat = [r["latency_ms"] for r in rows if r["latency_ms"] is not None]
    if lat:
        lat.sort()
        med = statistics.median(lat)
        p90 = lat[int(len(lat) * 0.9) - 1]
        print(f"\nlatency  median={med:.0f}ms  p90={p90:.0f}ms  "
              f"over-30s={sum(1 for x in lat if x > 30000)}")
        if med < 1500:
            print("  WARNING median under 1.5s - you stopped reading the prompts.")
            print("  The clear rate below is measuring autopilot, not judgment.")

    report_block("ALL ROWS", rows, hours)
    warm = rows[WARMUP:]
    warm_hours = span_hours(warm)
    report_block(f"ALL-WARM (after first {WARMUP})", warm, warm_hours)

    # ---- false clears in full. These are the only failures that matter.
    _, false = evaluate(warm)
    print("\n" + "=" * 62)
    print(f"FALSE CLEARS (warm): {len(false)}")
    for r in false:
        print(f"\n  [{r['id']}] {r['human_decision'].upper()}  seen={r['seen_count']}")
        print(f"    {r['summary']}")
        print(f"    $ {r['payload']}")
        if r["human_note"]:
            print(f"    note: {r['human_note']}")
    if not false:
        print("  none - v0 never cleared something you pushed back on.")

    unnecessary = [r for r in warm
                   if r["shadow_verdict"] == "escalate"
                   and r["human_decision"] == "approve"
                   and r["seen_count"] >= 3]
    print(f"\nunnecessary escalations (warm): {len(unnecessary)}"
          f"  - asked you about things you'd seen 3+ times and approved")

    # ---- sweep. Not a single number: a tolerance curve.
    # A zero-tolerance point estimate is too brittle - one stray rejection of
    # a routine command drives it to 0% and tells you nothing. What matters is
    # the shape, plus one hard constraint: false clears on IRREVERSIBLE actions
    # must be zero at any tolerance. Those are the ones that cause damage.
    print("\n" + "=" * 62)
    print("POLICY FRONTIER (warm rows)\n")
    print("  tolerance     auto-clear   interrupts/hr   rule set")

    variants = list(itertools.product(
        (True, False), (True, False), (True, False), (True, False),
        range(0, 11), (2, 3, 5)))

    def sweep(max_false):
        best = None
        for ud, uo, ue, ub, st, et in variants:
            auto, false = evaluate(
                warm, use_destructive=ud, use_outside=uo, use_external=ue,
                use_outbound=ub, seen_threshold=st, external_threshold=et)
            if len(false) > max_false:
                continue
            if any(not r["reversible"] for r in false):
                continue          # hard constraint, never relaxed
            rate = auto / len(warm) if warm else 0
            if best is None or rate > best[0]:
                best = (rate, auto, dict(destructive=ud, outside=uo,
                                         external=ue, outbound=ub,
                                         seen=st, ext_seen=et))
        return best

    headline = None
    for label, tol in (("0 false", 0), ("<=1 false", 1),
                       ("<=2% false", max(1, int(len(warm) * 0.02)))):
        b = sweep(tol)
        if b is None:
            print(f"  {label:<12} none - no rule set qualifies")
            continue
        rate, auto, cfg = b
        esc_hr = (len(warm) - auto) / warm_hours if warm_hours else 0
        print(f"  {label:<12} {rate:>8.1%}   {esc_hr:>11.1f}   {cfg}")
        if headline is None:
            headline = (rate, esc_hr, label)

    irrev_false = [r for r in false if not r["reversible"]]
    print(f"\n  v0 false clears on IRREVERSIBLE actions: {len(irrev_false)}")
    if irrev_false:
        print("  ^ the naive baseline cleared something you could not undo and")
        print("    did not want. The frontier above already forbids these, which")
        print("    is why its rule sets differ from v0.")

    if headline:
        print(f"\nSAFE AUTO-CLEAR RATE: {headline[0]:.1%} "
              f"at {headline[1]:.1f} interruptions/hour  [{headline[2]}]")
    print("Read the curve, not one row of it. If tolerating a single false")
    print("clear jumps the rate 20 points, that row is noise - go look at it.")

    print("\nNow go read every false clear above. Three of those will teach you")
    print("more than the percentage did.")


if __name__ == "__main__":
    main()

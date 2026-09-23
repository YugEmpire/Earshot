"""Explain why a repeated action never got offered for auto-approval.

  py why_no_auto.py            top 15 most-seen actions
  py why_no_auto.py "tsc"      only actions matching that text
"""
import sys
import db

conn = db.connect()
needle = sys.argv[1].lower() if len(sys.argv) > 1 else None

rows = conn.execute(
    "SELECT norm_payload, COUNT(*) n, MAX(summary) summary, "
    "MAX(destructive) d, MAX(outbound) o, MAX(outside_project) op, "
    "MIN(reversible) rev "
    "FROM decisions WHERE human_decision IN ('approve','reject','modify') "
    "GROUP BY norm_payload ORDER BY n DESC").fetchall()

print(f"{'seen':>5}  {'streak':>6}  why")
print("-" * 74)
for r in rows:
    if needle and needle not in (r["norm_payload"] or "").lower() \
            and needle not in (r["summary"] or "").lower():
        continue
    hist = [h["human_decision"] for h in conn.execute(
        "SELECT human_decision FROM decisions WHERE norm_payload = ? "
        "AND human_decision IN ('approve','reject','modify') ORDER BY ts",
        (r["norm_payload"],)).fetchall()]
    # consecutive approvals since the most recent pushback
    streak = 0
    for h in reversed(hist):
        if h != "approve":
            break
        streak += 1
    pushbacks = [h for h in hist if h != "approve"]

    if db.is_auto(conn, r["norm_payload"]):
        why = "already auto-approved"
    elif r["d"] or r["o"] or r["op"] or not r["rev"]:
        why = "risky - never offered"
    elif pushbacks and streak < 2:
        why = f"pushed back on recently ({', '.join(pushbacks[-2:])})"
    elif pushbacks:
        why = (f"has {len(pushbacks)} old pushback(s) - blocked by the OLD rule, "
               f"allowed by the new streak rule")
    else:
        why = "eligible"
    print(f"{r['n']:>5}  {streak:>6}  {why}")
    print(f"         {(r['summary'] or '')[:66]}")

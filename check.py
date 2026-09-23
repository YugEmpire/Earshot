import sqlite3
c = sqlite3.connect("earshot.db")
c.row_factory = sqlite3.Row
rows = c.execute(
    "SELECT human_decision, answered_via, latency_ms, summary "
    "FROM decisions WHERE session = ? ORDER BY ts",
    ("claude-code",)).fetchall()
if not rows:
    print("no rows for that session")
for r in rows:
    print(f'{r["human_decision"]:<8} via {r["answered_via"] or "?":<6} '
          f'{r["latency_ms"]}ms  {r["summary"][:40]}')

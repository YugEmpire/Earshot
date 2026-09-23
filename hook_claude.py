r"""earshot hook for Claude Code - real enforcement, not a suggestion.

Install in .claude/settings.json:

    {
      "hooks": {
        "PreToolUse": [{
          "matcher": "Bash|Edit|Write|MultiEdit|NotebookEdit",
          "hooks": [{
            "type": "command",
            "command": "D:\\earshot-probe\\.venv\\Scripts\\python.exe D:\\earshot-probe\\hook_claude.py",
            "timeout": 600
          }]
        }]
      }
    }

Why this matters more than the Cursor version:

Cursor's .cursorrules is advisory. The agent chooses whether to call the gate,
and Cursor keeps three other channels to the user that bypass it entirely.
A PreToolUse hook is enforcement: the tool call does not happen unless this
script says so, and a deny holds even under --dangerously-skip-permissions.

Protocol:
    stdin   {"tool_name": "...", "tool_input": {...}, "session_id": "..."}
    stdout  {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                    "permissionDecision": "allow"|"deny",
                                    "permissionDecisionReason": "..."}}

One difference from the Cursor build worth knowing: there is no agent-written
summary here. The hook sees the raw tool call, so it has to phrase the request
itself. The table below is cruder than what an agent writes, and improving it
is the obvious next step.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import gate as gatemod

SESSION = os.environ.get("EARSHOT_SESSION", "claude-code")
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hook.log")


def log(msg):
    """Exit code 0 hides stdout AND stderr, so a crash in here is otherwise
    completely invisible - the command just runs and you assume the gate
    approved it. Everything goes to a file instead."""
    try:
        from datetime import datetime
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')}  {msg}\n")
    except Exception:
        pass

# Raw command -> something worth hearing. First match wins, so order matters.
PHRASES = [
    (r"^npx\s+tsc|^tsc\b",              "checking the code for mistakes"),
    (r"^npm\s+run\s+build|^npm\s+run\s+compile", "building the project"),
    (r"^node\s+--test|^npm\s+test|^pytest|^jest",
                                         "running the tests"),
    (r"^npm\s+(i|install)\b",           "installing a package from the internet"),
    (r"^pip\s+install\b",               "installing a python package"),
    (r"^git\s+status|^git\s+diff|^git\s+log", "checking what has changed"),
    (r"^git\s+add|^git\s+commit",       "saving the work so far"),
    (r"^git\s+push",                    "publishing the code to the shared server"),
    (r"^git\s+pull|^git\s+fetch",       "fetching the latest code"),
    (r"^rm\s|^rmdir|^del\s",            "deleting files"),
    (r"^mkdir\b",                       "making a new folder"),
    (r"^curl|^wget|^Invoke-WebRequest", "fetching something from the internet"),
    (r"^ls\b|^dir\b|^cat\b|^type\b",    "looking at what is there"),
    (r"^cd\b",                          "changing folder"),
    (r"^echo\b",                        "printing a message"),
]


def phrase_for(tool, tool_input):
    if tool == "Bash":
        cmd = (tool_input.get("command") or "").strip()
        for pat, said in PHRASES:
            if re.search(pat, cmd, re.IGNORECASE):
                return said, cmd
        # Nothing matched: say the first word and let the flags carry the risk.
        first = cmd.split()[0] if cmd.split() else "a command"
        return f"running {first}", cmd

    path = (tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    name = os.path.basename(path) or "a file"
    if tool == "Write":
        return f"writing {name}", f"{path} (write)"
    if tool in ("Edit", "MultiEdit", "NotebookEdit"):
        return f"editing {name}", f"{path} (edit)"
    return f"using {tool}", json.dumps(tool_input)[:200]


def decide(payload, said, action_type):
    conn = db.connect()
    cwd = os.getcwd()
    feats = db.derive_features(payload, cwd)
    norm = db.normalise(payload)
    seen = db.seen_count(conn, norm)
    verdict, rule = db.shadow_policy(feats, seen)

    # Same three layers as the Cursor build: explicit consent, then scope,
    # then ask. Risk flags veto both shortcuts.
    covering = gatemod.scope_ok(conn, SESSION, payload, action_type, feats)
    auto, why = gatemod.auto_ok(conn, feats, norm)
    if covering and not auto:
        auto, why = True, f"covered by scope: {covering}"

    if auto:
        db.insert(conn, session=SESSION, mode="hook", action_type=action_type,
                  summary=said, payload=payload, norm_payload=norm,
                  seen_count=seen, human_decision="auto_approve", human_note=why,
                  latency_ms=0, exit_code=None, shadow_verdict=verdict,
                  shadow_rule=rule, outcome="unknown", **feats)
        return "allow", why

    row_id = db.insert(
        conn, session=SESSION, mode="hook", action_type=action_type,
        summary=said, payload=payload, norm_payload=norm, seen_count=seen,
        human_decision=None, human_note=None, latency_ms=None, exit_code=None,
        shadow_verdict=verdict, shadow_rule=rule, outcome="unknown", **feats)

    decision, note, _lat = gatemod.wait_for_answer(conn, row_id)

    if decision == "approve":
        return "allow", "approved by voice"
    if decision == "modify":
        # Deny with the spoken note as the reason. Claude reads it and adjusts,
        # which keeps the loop hands-free - "ask" would put a keyboard prompt
        # back in the middle of it.
        return "deny", note or "change it - see the spoken note"
    if decision == "timeout":
        return "deny", "no answer from the approver window"
    return "deny", note or "refused by voice"


def main():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except Exception as e:
        log(f"BAD INPUT {e}: {raw[:200]!r}")
        return 0                      # never block on malformed input

    tool = data.get("tool_name", "")
    tool_input = data.get("tool_input", {}) or {}
    log(f"fired: tool={tool} input={json.dumps(tool_input)[:160]}")

    if tool in ("Read", "Glob", "Grep", "TodoWrite", "WebFetch", "WebSearch"):
        log("  read-only, allowed")
        return 0

    said, payload = phrase_for(tool, tool_input)
    try:
        decision, reason = decide(payload, said, "shell" if tool == "Bash"
                                  else "file_write")
    except Exception as e:
        import traceback
        log("  ERROR " + traceback.format_exc()[-400:])
        return 0                      # fail open: a broken gate must not brick
                                      # the agent

    log(f"  decision={decision} ({reason})")

    # Both protocols. Newer Claude Code reads the JSON on stdout; this build
    # documents exit 0 = proceed, exit 2 = block, and ignores stdout entirely.
    # Printing the JSON AND returning the right code covers both, and getting
    # this wrong means a deny silently becomes an allow.
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }}))
    if decision == "deny":
        print(reason, file=sys.stderr)   # exit 2 shows stderr to the model
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

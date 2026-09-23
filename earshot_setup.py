r"""earshot setup - a window, not a terminal.

  pythonw earshot_setup.py       (or double-click Earshot Setup.bat)

Detects which AI coding tools are installed, shows what each one can actually
guarantee, and wires Earshot into all of them at the user level so it works in
every project without per-repo setup.

Tkinter is stdlib: no install, no dependency, runs anywhere Python does. It is
not beautiful. It is a window with buttons, which is the entire point.

The honest bit this UI refuses to hide: "works with every platform" is not the
same as "works the same on every platform".

  ENFORCED  the tool call cannot happen unless you allow it. A hook returns a
            blocking exit code and the platform obeys it.
  ADVISORY  the agent is asked to route through the gate and usually does, but
            nothing stops it acting directly, and the host keeps its own
            channels to you.

Selling advisory as enforced is the one claim that would get found out the
first time someone tests it.
"""

import json
import os
import shutil
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
PY = os.path.join(HERE, ".venv", "Scripts", "python.exe")
if not os.path.exists(PY):
    PY = sys.executable
HOOK = os.path.join(HERE, "hook_claude.py")

# Forward slashes: Claude Code runs hooks through bash, which eats backslashes.
HOOK_CMD = f"{PY} {HOOK}".replace("\\", "/")

RULES_TEXT = """TERMINAL COMMANDS
Never run a terminal command directly. Instead run:

{py} {here}/gate.py run --session {session} --summary "<see SUMMARY RULES>" --cmd "<the exact command>"

FILE EDITS
Before creating, editing or deleting any file, first run:

{py} {here}/gate.py ask --session {session} --type file_write --summary "<see SUMMARY RULES>" --payload "<path + what changes>"

Exit 0 = proceed. Exit 1 = do not, ask me what to change.
Exit 2 = I am modifying the request, wait for my instruction.

SUMMARY RULES
The summary is READ ALOUD. I am not looking at the screen. Someone with no
programming knowledge should understand what is happening to the project.
Write ONE PLAIN SENTENCE, eight to fourteen words. Say what it does to the
project, in ordinary English. No paths, no flags, no command syntax. If it
deletes, overwrites or sends anything out, that word comes first.

COMMAND SHAPE
One simple command per gate call. No pipes, no chaining with ; or &&, no
here-strings, no subshells. A compound command cannot be matched against an
allowlist and cannot be partially refused.

AT THE START OF EACH PHASE, RUN THIS COMMAND. Do not ask me in chat.
Do not use your question tool.

{py} {here}/gate.py scope --session {session} --summary "<what is routine for this phase>" --commands "npx tsc*;node --test*;npm run build*" --paths "src/*" --minutes 90

Never ask me a question in chat that could be a gate call instead.
"""


# ------------------------------------------------------------------ platforms

def detect():
    """What is installed, and what can each actually promise?"""
    out = []

    claude_dir = os.path.join(HOME, ".claude")
    out.append({
        "key": "claude",
        "name": "Claude Code",
        "found": shutil.which("claude") is not None or os.path.isdir(claude_dir),
        "level": "ENFORCED",
        "how": "PreToolUse hook. Exit code 2 blocks the tool call outright.",
        "target": os.path.join(claude_dir, "settings.json"),
        "scope": "all projects",
    })

    cursor_dir = os.path.join(HOME, ".cursor")
    out.append({
        "key": "cursor",
        "name": "Cursor",
        "found": os.path.isdir(cursor_dir) or shutil.which("cursor") is not None,
        "level": "ADVISORY",
        "how": ("Rules file. The agent is asked to call the gate and usually "
                "does, but nothing stops it acting directly."),
        "target": os.path.join(HOME, ".cursor", "rules", "earshot.mdc"),
        "scope": "all projects (user rules)",
    })

    windsurf_dir = os.path.join(HOME, ".codeium", "windsurf")
    out.append({
        "key": "windsurf",
        "name": "Windsurf",
        "found": os.path.isdir(windsurf_dir),
        "level": "ADVISORY",
        "how": "Rules file, same limitation as Cursor.",
        "target": os.path.join(windsurf_dir, "memories", "earshot.md"),
        "scope": "all projects",
    })

    codex_dir = os.path.join(HOME, ".codex")
    out.append({
        "key": "codex",
        "name": "Codex CLI",
        "found": os.path.isdir(codex_dir) or shutil.which("codex") is not None,
        "level": "UNVERIFIED",
        "how": ("Installs an AGENTS.md instruction. Whether Codex offers a "
                "blocking hook has not been confirmed - do not assume it "
                "enforces until it has been tested."),
        "target": os.path.join(codex_dir, "AGENTS.md"),
        "scope": "all projects",
    })
    return out


def install(p, session="earshot"):
    """Write the config for one platform. Merges rather than overwrites -
    these files hold the user's own settings too."""
    target = p["target"]
    os.makedirs(os.path.dirname(target), exist_ok=True)

    if p["key"] == "claude":
        cfg = {}
        if os.path.exists(target):
            try:
                with open(target, encoding="utf-8-sig") as f:
                    cfg = json.load(f)
            except Exception:
                bak = target + ".bak"
                shutil.copy(target, bak)
                cfg = {}
        hooks = cfg.setdefault("hooks", {})
        pre = [h for h in hooks.get("PreToolUse", [])
               if "hook_claude.py" not in json.dumps(h)]
        pre.append({
            "matcher": "Bash|Edit|Write|MultiEdit|NotebookEdit",
            "hooks": [{"type": "command", "command": HOOK_CMD, "timeout": 600}],
        })
        hooks["PreToolUse"] = pre
        # No BOM: json parsers reject it and the failure is silent.
        with open(target, "w", encoding="utf-8", newline="\n") as f:
            json.dump(cfg, f, indent=2)
        return f"hook installed for all projects"

    body = RULES_TEXT.format(py=PY.replace("\\", "/"),
                             here=HERE.replace("\\", "/"), session=session)
    if p["key"] == "cursor":
        body = ("---\ndescription: route every action through the earshot gate\n"
                "alwaysApply: true\n---\n\n") + body
    with open(target, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    return "rules installed for all projects"


def checks():
    """The things that silently broke sessions, in one list."""
    res = []
    res.append(("probe python", os.path.exists(PY), PY))
    res.append(("hook script", os.path.exists(HOOK), HOOK))
    try:
        sys.path.insert(0, HERE)
        import db
        conn = db.connect()
        n = conn.execute("SELECT COUNT(*) c FROM decisions").fetchone()["c"]
        res.append(("database", True, f"{n} decisions on record"))
    except Exception as e:
        res.append(("database", False, str(e)[:50]))
    try:
        import listen
        res.append(("speech in (whisper)", listen.available(),
                    "" if listen.available() else "pip install faster-whisper sounddevice numpy"))
    except Exception as e:
        res.append(("speech in (whisper)", False, str(e)[:50]))
    try:
        import voice
        res.append(("speech out", True,
                    "kokoro" if voice.kokoro_available() else "system voice"))
    except Exception as e:
        res.append(("speech out", False, str(e)[:50]))
    return res


# --------------------------------------------------------------- speech setup
# Without these the product silently degrades to the thing it exists to
# replace: a flat robotic voice and a keyboard. So the installer sets them up
# rather than leaving them as a manual step nobody will take.

TTS_VENV = os.path.join(HERE, ".tts")
TTS_PY = os.path.join(TTS_VENV, "Scripts", "python.exe")
TTS_SERVER = os.path.join(HERE, "tts_server.py")

TTS_SERVER_SRC = '''import os, sys
VOICE = os.environ.get("EARSHOT_KOKORO_VOICE", "af_heart")
SPEED = float(os.environ.get("EARSHOT_KOKORO_SPEED", "1.0"))
LANG  = os.environ.get("EARSHOT_KOKORO_LANG", "a")
_pipe = _sd = None

def warm():
    global _pipe, _sd
    from kokoro import KPipeline
    import sounddevice as sd
    _sd = sd
    _pipe = KPipeline(lang_code=LANG)
    for _g, _p, _a in _pipe("ready", voice=VOICE, speed=SPEED):
        break

def say(text):
    spoke = False
    for _g, _p, audio in _pipe(text, voice=VOICE, speed=SPEED):
        _sd.play(audio, 24000)
        _sd.wait()
        spoke = True
    return spoke

try:
    warm()
except Exception as e:
    print("ERR warmup " + str(e), flush=True); sys.exit(1)
print("READY", flush=True)
for line in sys.stdin:
    text = line.rstrip("\\n").strip()
    if not text: continue
    if text == "__QUIT__": break
    try:
        print("OK" if say(text) else "ERR empty", flush=True)
    except Exception as e:
        print("ERR " + str(e), flush=True)
'''


def find_python(*versions):
    """Kokoro needs spaCy, which has no wheel for 3.14 - hence a second, older
    interpreter purely for speech output."""
    for v in versions:
        try:
            r = subprocess.run(["py", f"-{v}", "-c", "import sys;print(sys.executable)"],
                               capture_output=True, text=True, timeout=20)
            if r.returncode == 0 and r.stdout.strip():
                return v, r.stdout.strip()
        except Exception:
            pass
    return None, None


def speech_status():
    out = []
    try:
        sys.path.insert(0, HERE)
        import listen
        out.append(("speech input (Whisper)", listen.available(),
                    "ready" if listen.available() else "not installed"))
    except Exception:
        out.append(("speech input (Whisper)", False, "not installed"))

    # The model only matters if Whisper can actually run. Reporting "ready"
    # because some unrelated HuggingFace cache exists is a false OK - it told a
    # user speech was set up while speech input was not installed at all.
    have_whisper = out[0][1]
    model_dir = os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")
    have_model = have_whisper and os.path.isdir(model_dir)
    out.append(("Whisper model downloaded", have_model,
                "ready" if have_model else
                ("downloads on first use" if have_whisper else "comes with speech input")))
    out.append(("natural voice (Kokoro)",
                os.path.exists(TTS_PY) and os.path.exists(TTS_SERVER),
                "ready" if os.path.exists(TTS_PY) else "not installed"))
    return out


def install_speech(say):
    """Long-running. `say` streams progress back to the window."""
    say("Installing speech input. This downloads about 200 MB.\n")
    r = subprocess.run([PY, "-m", "pip", "install", "-q",
                        "faster-whisper", "sounddevice", "numpy"],
                       capture_output=True, text=True)
    if r.returncode:
        say("  failed: " + (r.stderr or "")[-300:] + "\n")
    else:
        say("  speech input installed.\n")

    say("\nDownloading the speech model (about 500 MB, once)...\n")
    code = ("from faster_whisper import WhisperModel;"
            "WhisperModel('small.en', device='cpu', compute_type='int8');"
            "print('model ready')")
    r = subprocess.run([PY, "-c", code], capture_output=True, text=True)
    say("  " + ("model ready.\n" if r.returncode == 0
                else "failed: " + (r.stderr or "")[-300:] + "\n"))

    say("\nSetting up the natural voice.\n")
    ver, path = find_python("3.12", "3.11", "3.13")
    if not ver:
        say("  No Python 3.12 found. The natural voice needs one, because its\n"
            "  dependencies have no build for 3.14 yet.\n"
            "  Install Python 3.12 from python.org, then press this again.\n"
            "  Everything else still works - you will hear the system voice.\n")
        return
    say(f"  using Python {ver}\n")

    if not os.path.exists(TTS_PY):
        r = subprocess.run(["py", f"-{ver}", "-m", "venv", TTS_VENV],
                           capture_output=True, text=True)
        if r.returncode:
            say("  could not create the voice environment: "
                + (r.stderr or "")[-200:] + "\n")
            return
    say("  installing the voice (about 350 MB)...\n")
    r = subprocess.run([TTS_PY, "-m", "pip", "install", "-q",
                        "kokoro", "soundfile", "sounddevice"],
                       capture_output=True, text=True)
    if r.returncode:
        say("  failed: " + (r.stderr or "")[-300:] + "\n")
        return

    with open(TTS_SERVER, "w", encoding="utf-8", newline="\n") as f:
        f.write(TTS_SERVER_SRC)
    say("  natural voice installed.\n\nDone. Speech in and out are ready.\n")


# ------------------------------------------------------------------------ ui

def run_ui():
    import tkinter as tk
    from tkinter import ttk, messagebox

    root = tk.Tk()
    root.title("Earshot setup")
    root.geometry("760x680")

    head = tk.Frame(root, padx=16, pady=12)
    head.pack(fill="x")
    tk.Label(head, text="Earshot", font=("Segoe UI", 18, "bold")).pack(anchor="w")
    tk.Label(head, wraplength=700, justify="left", fg="#444",
             text=("Your AI coding agent asks before it acts, out loud, and you "
                   "answer by voice.\nInstall it once here and it works in every "
                   "project.")).pack(anchor="w")

    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True, padx=12, pady=8)

    # ---- platforms tab
    tab1 = tk.Frame(nb, padx=12, pady=10)
    nb.add(tab1, text="Coding tools")

    tk.Label(tab1, wraplength=700, justify="left", fg="#444",
             text=("Not every tool can promise the same thing. Enforced means the "
                   "action cannot happen without you. Advisory means the agent is "
                   "asked to check with you and usually does.")
             ).pack(anchor="w", pady=(0, 10))

    colours = {"ENFORCED": "#1a7f37", "ADVISORY": "#9a6700", "UNVERIFIED": "#777"}
    for p in detect():
        card = tk.Frame(tab1, relief="solid", bd=1, padx=12, pady=10)
        card.pack(fill="x", pady=5)
        row = tk.Frame(card)
        row.pack(fill="x")
        tk.Label(row, text=p["name"], font=("Segoe UI", 11, "bold")).pack(side="left")
        tk.Label(row, text="  " + p["level"], fg=colours[p["level"]],
                 font=("Segoe UI", 9, "bold")).pack(side="left")
        tk.Label(row, text="found" if p["found"] else "not installed",
                 fg="#1a7f37" if p["found"] else "#999").pack(side="right")
        tk.Label(card, text=p["how"], wraplength=560, justify="left",
                 fg="#555").pack(anchor="w", pady=(4, 6))

        status = tk.Label(card, text="", fg="#1a7f37")
        status.pack(side="right")

        def make(pp, lbl):
            def go():
                try:
                    lbl.config(text=install(pp), fg="#1a7f37")
                except Exception as e:
                    lbl.config(text=str(e)[:60], fg="#b00")
            return go
        tk.Button(card, text="Install", width=12,
                  command=make(p, status),
                  state="normal" if p["found"] else "disabled").pack(side="left")

    # ---- status tab
    tab2 = tk.Frame(nb, padx=12, pady=10)
    nb.add(tab2, text="Check")
    box = tk.Text(tab2, height=18, wrap="word", font=("Consolas", 9))
    box.pack(fill="both", expand=True)

    def refresh():
        box.delete("1.0", "end")
        bad = 0
        for name, ok, detail in checks():
            box.insert("end", f"{'  OK  ' if ok else ' FAIL '} {name:<22} {detail}\n")
            bad += not ok
        box.insert("end", "\n" + ("Everything is ready.\n" if not bad
                                  else f"Still to fix: {bad}\n"))
    tk.Button(tab2, text="Check again", command=refresh).pack(pady=8)
    refresh()

    # ---- speech tab
    tab_s = tk.Frame(nb, padx=12, pady=10)
    nb.add(tab_s, text="Speech")
    tk.Label(tab_s, wraplength=700, justify="left", fg="#444",
             text=("Earshot needs two things: a voice to read requests to you, "
                   "and speech recognition to hear your answer.\n\nWithout "
                   "them it falls back to a flat system voice and the keyboard "
                   "- which is the thing this replaces.\n\nEverything runs on "
                   "your machine. No audio is uploaded anywhere.")
             ).pack(anchor="w", pady=(0, 10))

    slog = tk.Text(tab_s, height=14, wrap="word", font=("Consolas", 9))
    slog.pack(fill="both", expand=True, pady=(6, 8))

    def sshow():
        slog.delete("1.0", "end")
        for name, ok, detail in speech_status():
            slog.insert("end", f"{'  OK  ' if ok else ' --   '} {name:<26} {detail}\n")

    def sgo():
        btn_s.config(state="disabled", text="Installing...")
        slog.delete("1.0", "end")

        def emit(msg):
            root.after(0, lambda: (slog.insert("end", msg), slog.see("end")))

        def work():
            try:
                install_speech(emit)
            except Exception as e:
                emit("\nerror: " + str(e))
            root.after(0, lambda: (btn_s.config(state="normal",
                                                text="Install speech"), sshow()))
        threading.Thread(target=work, daemon=True).start()

    btn_s = tk.Button(tab_s, text="Install speech", width=20, command=sgo)
    btn_s.pack(anchor="w")
    tk.Label(tab_s, fg="#777",
             text="Takes several minutes and downloads about 1 GB. Once only."
             ).pack(anchor="w", pady=(4, 0))
    sshow()

    # ---- run tab
    tab3 = tk.Frame(nb, padx=12, pady=10)
    nb.add(tab3, text="Run")
    tk.Label(tab3, wraplength=700, justify="left", fg="#444",
             text=("Start the listener, then use your coding tool normally. "
                   "Requests are read aloud and you answer by speaking.\n\n"
                   "Leave the listener window open. Do not type in it - it is "
                   "waiting for your answer.")).pack(anchor="w", pady=(0, 12))

    def start_listener():
        try:
            subprocess.Popen([PY, os.path.join(HERE, "approve.py"), "--voice"],
                             creationflags=getattr(subprocess,
                                                   "CREATE_NEW_CONSOLE", 0))
        except Exception as e:
            messagebox.showerror("Could not start", str(e))

    tk.Button(tab3, text="Start listening", width=20, height=2,
              command=start_listener).pack(anchor="w")

    # Land on the tab that needs attention. Opening on Coding tools shows a
    # complete-looking screen: a user installs their editor, presses Run, and
    # hears nothing, because speech was never installed and nothing said so.
    try:
        if not all(ok for _n, ok, _d in speech_status()):
            nb.select(tab_s)
    except Exception:
        pass

    root.mainloop()


if __name__ == "__main__":
    if "--check" in sys.argv:
        for p in detect():
            print(f"{p['name']:<14} {p['level']:<11} "
                  f"{'found' if p['found'] else '-':<12} {p['target']}")
        print()
        for name, ok, detail in checks():
            print(f"  {'ok  ' if ok else 'FAIL'} {name:<22} {detail}")
        sys.exit(0)
    run_ui()

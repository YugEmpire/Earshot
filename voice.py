"""earshot voice - speaks requests aloud and listens for your answer.

  python voice.py              speak a sample, then listen for a reply
  python voice.py --voices     list the voices installed on this machine

Both directions use what Windows already ships. No installs, works offline.

Speaking answers the "I did not notice the window" half of the 26 second
median. Listening answers the other half - switching focus and pressing a key.
You can still press a key at any time; whichever comes first wins.
"""

import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import threading

IS_WINDOWS = platform.system() == "Windows"

RATE = int(os.environ.get("EARSHOT_RATE", "-1"))       # -10 slowest, 10 fastest
VOICE_NAME = os.environ.get("EARSHOT_VOICE", "")       # substring match
LISTEN_S = int(os.environ.get("EARSHOT_LISTEN", "12"))
MIN_CONFIDENCE = float(os.environ.get("EARSHOT_CONFIDENCE", "0.30"))
DEBUG = os.environ.get("EARSHOT_VOICE_DEBUG", "") not in ("", "0")

MAX_WORDS = 18

_SAY_AS = {
    "npx": "N P X", "tsc": "type script compiler", "npm": "N P M",
    "cli": "C L I", "src": "source", "json": "jason", "dist": "build folder",
    "cd": "change directory", "rm": "remove", "--noEmit": "", "&&": "then",
}

_STRIP = (r"\$\w+", r"[A-Za-z]:[\\/][^\s]*", r"[{}()|&]+")


# ------------------------------------------------------------------ phrasing

def simplify(text):
    out = text
    for pat in _STRIP:
        out = re.sub(pat, " ", out)
    out = re.sub(r"\s*,\s*(?=,|$)", "", out)
    out = re.sub(r"\s+", " ", out).strip(" ,.;:-")
    words = out.split()
    if len(words) > MAX_WORDS:
        out = " ".join(words[:MAX_WORDS])
    return out or "an unnamed action"


def phrase(row):
    """Conversational, not clipped. A voice that reads out flags like a warning
    label is tiring within ten minutes; one that talks to you is not. Risk still
    comes first, just softened into a sentence."""
    body = simplify(row["summary"])

    lead = ""
    if row["destructive"] and row["outside_project"]:
        lead = "Careful, this removes something outside the project. "
    elif row["destructive"]:
        lead = "Careful, this deletes something. "
    elif row["outbound"]:
        lead = "Heads up, this sends data out. "
    elif row["outside_project"]:
        lead = "Just so you know, this reaches outside the project. "

    seen = row["seen_count"]
    if seen == 0:
        tail = ", first time. Okay?"
    elif seen >= 5:
        tail = ", the usual. Okay?"
    elif seen >= 2:
        tail = f", you've allowed this {seen} times before. Okay?"
    else:
        tail = ". Okay?"

    text = f"{lead}{body}{tail}"
    for k, v in _SAY_AS.items():
        text = re.sub(rf"(?<![\w-]){re.escape(k)}(?![\w-])", v, text)
    return re.sub(r"\s+", " ", text).strip()


# ------------------------------------------------------------------ speaking

_PS_SPEAK = r"""
$ErrorActionPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.Rate = __RATE__
if ('__VOICE__' -ne '') {
  foreach ($v in $s.GetInstalledVoices()) {
    if ($v.VoiceInfo.Name -like '*__VOICE__*') { $s.SelectVoice($v.VoiceInfo.Name); break }
  }
}
$s.Speak([Console]::In.ReadToEnd())
"""

_PS_VOICES = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name + '  |  ' + $_.VoiceInfo.Gender }
"""


def _ps(script, stdin=None, timeout=30):
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        input=stdin, text=True, timeout=timeout, capture_output=True)


def _speak_windows(text):
    script = _PS_SPEAK.replace("__RATE__", str(RATE)).replace("__VOICE__", VOICE_NAME)
    try:
        _ps(script, stdin=text)
    except Exception:
        pass


def _speak_posix(text):
    for exe, args in (("say", []), ("espeak", ["-s", "150"])):
        if shutil.which(exe):
            try:
                subprocess.run([exe, *args, text], timeout=30,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except Exception:
                pass
    sys.stdout.write("\a")
    sys.stdout.flush()


# ---------------------------------------------------------- kokoro (opt-in)
# Built in, not patched on, so replacing this file never loses the local TTS
# setup. Kokoro runs in its own Python 3.12 environment because it pulls in
# spaCy, which has no wheel for 3.14 - and as ONE resident process, because
# constructing the pipeline per call cost 17 seconds a sentence.
#
# It turns on by itself when .tts\\Scripts\\python.exe and tts_server.py both
# exist. EARSHOT_TTS=sapi forces the Windows voice; =kokoro refuses to fall
# back to it.

TTS_MODE = os.environ.get("EARSHOT_TTS", "auto").lower()
_HERE = os.path.dirname(os.path.abspath(__file__))
KOKORO_PY = os.path.join(_HERE, ".tts", "Scripts", "python.exe")
TTS_SERVER = os.path.join(_HERE, "tts_server.py")

_tts_proc = None
_tts_lock = threading.Lock()


def kokoro_available():
    if TTS_MODE == "sapi":
        return False
    return os.path.exists(KOKORO_PY) and os.path.exists(TTS_SERVER)


def _tts_worker():
    global _tts_proc
    if _tts_proc is not None and _tts_proc.poll() is None:
        return _tts_proc
    if not kokoro_available():
        return None
    try:
        _tts_proc = subprocess.Popen(
            [KOKORO_PY, TTS_SERVER], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        # Kokoro prints progress and warnings before it is ready, so scan for
        # the marker instead of demanding it on the first line.
        for _ in range(300):
            line = _tts_proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if line == "READY":
                return _tts_proc
            if line.startswith("ERR warmup"):
                print("  kokoro warmup failed:", line, file=sys.stderr)
                break
        _tts_proc.kill()
        _tts_proc = None
    except Exception as e:
        print("  kokoro launch failed:", e, file=sys.stderr)
        _tts_proc = None
    return _tts_proc


def _speak_kokoro(text):
    global _tts_proc
    with _tts_lock:
        p = _tts_worker()
        if p is None:
            return False
        try:
            p.stdin.write(text.replace("\n", " ") + "\n")
            p.stdin.flush()
            return p.stdout.readline().strip() == "OK"
        except Exception:
            _tts_proc = None
            return False


def prewarm():
    """Load the model in the background at startup so the first approval of
    the session is not the slow one."""
    if kokoro_available():
        threading.Thread(target=_tts_worker, daemon=True).start()


def speak(text, blocking=True):
    """Blocking by default. The recogniser must not hear the synthesiser -
    listening while speaking makes the machine answer its own question."""
    def fn(t_):
        if kokoro_available() and _speak_kokoro(t_):
            return
        if TTS_MODE == "kokoro":
            return
        (_speak_windows if IS_WINDOWS else _speak_posix)(t_)

    if blocking:
        fn(text)
        return None
    t = threading.Thread(target=fn, args=(text,), daemon=True)
    t.start()
    return t


def list_voices():
    if not IS_WINDOWS:
        print("voice listing is Windows only")
        return
    try:
        r = _ps(_PS_VOICES, timeout=20)
        print(r.stdout.strip() or "no voices found")
        print("\nPick one with:   $env:EARSHOT_VOICE = 'Zira'")
        print("Slow it down:    $env:EARSHOT_RATE = '-2'")
    except Exception as e:
        print("could not list voices:", e)


# ----------------------------------------------------------------- listening

# Small closed grammar. Offline recognition is far more accurate on a handful
# of known words than on open dictation, and there are only three answers.
_PS_LISTEN = r"""
$ErrorActionPreference = 'Stop'
try {
  Add-Type -AssemblyName System.Speech
  $recs = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()
  if ($recs.Count -eq 0) { Write-Output 'ERR|no recognizer installed'; exit }
  $r = New-Object System.Speech.Recognition.SpeechRecognitionEngine($recs[0])

  $words = New-Object System.Speech.Recognition.Choices
  $words.Add(@('approve','yes','yep','okay','go','sure','fine',
               'reject','no','nope','stop','cancel','skip',
               'modify','change','wait','edit','hold'))
  $gb = New-Object System.Speech.Recognition.GrammarBuilder($words)
  $gb.Culture = $recs[0].Culture
  $r.LoadGrammar((New-Object System.Speech.Recognition.Grammar($gb)))
  $r.SetInputToDefaultAudioDevice()

  # Short per-attempt windows in a loop. A single long Recognize() gives up
  # the moment one utterance fails to match, which ends the whole window
  # after one mumbled word.
  $r.InitialSilenceTimeout = [TimeSpan]::FromSeconds(2)
  $r.BabbleTimeout         = [TimeSpan]::FromSeconds(2)
  $deadline = (Get-Date).AddSeconds(__SECS__)
  while ((Get-Date) -lt $deadline) {
    $res = $r.Recognize([TimeSpan]::FromSeconds(2))
    if ($res) { Write-Output ($res.Text + '|' + $res.Confidence); exit }
  }
  Write-Output 'NONE|0'
} catch { Write-Output ('ERR|' + $_.Exception.Message) }
"""

_YES = {"approve", "yes", "yep", "okay", "go", "sure", "fine"}
_NO = {"reject", "no", "nope", "stop", "cancel", "skip"}
_MOD = {"modify", "change", "wait", "edit", "hold"}


def _listen_once(seconds):
    """Returns ('a'|'r'|'m', confidence) or (None, reason)."""
    if not IS_WINDOWS:
        return None, "not windows"
    try:
        r = _ps(_PS_LISTEN.replace("__SECS__", str(seconds)), timeout=seconds + 20)
        out = (r.stdout or "").strip().splitlines()
        if not out:
            return None, "no output"
        word, _, conf = out[-1].partition("|")
        word = word.strip().lower()
        if word == "err":
            return None, conf.strip()[:60]
        if word == "none":
            return None, "heard nothing matching approve/reject/modify"
        try:
            confidence = float(conf)
        except ValueError:
            confidence = 0.0
        if DEBUG:
            print(f"    [heard '{word}' at {confidence:.2f}]", file=sys.stderr)
        if confidence < MIN_CONFIDENCE:
            return None, f"too unclear: heard '{word}' at {confidence:.2f}"
        if word in _YES:
            return "a", confidence
        if word in _NO:
            return "r", confidence
        if word in _MOD:
            return "m", confidence
        return None, f"unknown word {word}"
    except Exception as e:
        return None, str(e)[:60]


def _whisper_listen(seconds):
    """Prefer Whisper when installed: multilingual, accent-tolerant, local."""
    try:
        import listen as whisper_listen
    except Exception:
        return None, "listen.py missing"
    if not whisper_listen.available():
        return None, "faster-whisper not installed"
    return whisper_listen.listen(max_seconds=seconds)


def confirm(question, seconds=10, tries=2):
    """Speak a yes/no question and listen. Returns True, False, or None if it
    could not tell. None is never treated as yes - an unclear answer to
    "are you sure?" must not become consent."""
    for attempt in range(tries):
        speak(question if not attempt else "Sorry. " + question)
        choice, _how = listen_or_key(seconds=seconds, valid="arm")
        if choice == "a":
            return True
        if choice == "r":
            return False
    return None


# Said instead of a note. Matched before anything is stored, so "skip" never
# ends up recorded as the note itself.
_SKIP_WORDS = {"skip", "skip it", "no note", "nothing", "none", "no",
               "next", "move on", "pass", "no comment", "leave it",
               "skip notes", "no thanks", "nope", "carry on", "continue"}


def dictate(prompt_text, seconds=10):
    """Spoken free text, for notes. Optional by design: say "skip" to move on
    at once, or stay quiet and it gives up on its own. Returns None either
    way. Never falls back to typing - the keyboard is what we are removing."""
    speak(prompt_text)
    try:
        import listen as whisper_listen
        if not whisper_listen.available():
            return None
        text, _why = whisper_listen.listen_text(seconds)
    except Exception:
        return None

    if not text:
        return None
    cleaned = re.sub(r"[^\w\s']", " ", text.lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned in _SKIP_WORDS:
        return None
    # A short answer starting with a skip word is still a skip: "skip it, fine"
    first_two = " ".join(cleaned.split()[:2])
    if len(cleaned.split()) <= 3 and (cleaned.split()[0] in _SKIP_WORDS
                                      or first_two in _SKIP_WORDS):
        return None
    return text


def listen_or_key(seconds=None, valid="arm"):
    """Race speech against the keyboard. Whichever lands first wins, so voice
    never traps you - if recognition struggles, just press a key.

    Returns (choice, how) where how is 'voice', 'key', or a failure reason.
    """
    seconds = seconds or LISTEN_S
    result = queue.Queue()

    def _voice():
        # Whisper first, Windows SAPI only as a fallback. SAPI could not
        # transcribe an Australian accent saying "approve", so it is the
        # backup, not the primary.
        choice, info = _whisper_listen(seconds)
        if choice is None and "not installed" in str(info) or "missing" in str(info):
            choice, info = _listen_once(seconds)
        result.put((choice, info))

    if IS_WINDOWS:
        threading.Thread(target=_voice, daemon=True).start()
        try:
            import msvcrt
            while True:
                if msvcrt.kbhit():
                    ch = msvcrt.getch().decode("utf-8", "ignore").lower()
                    if ch == "\x03":
                        raise KeyboardInterrupt
                    if ch in valid:
                        return ch, "key"
                try:
                    choice, info = result.get(timeout=0.1)
                except queue.Empty:
                    continue
                if choice:
                    return choice, "voice"
                return None, info
        except ImportError:
            pass

    line = input().strip().lower()
    return (line[:1] if line and line[0] in valid else None), "key"


if __name__ == "__main__":
    if "--voices" in sys.argv:
        list_voices()
        sys.exit(0)
    sample = {"summary": "deleting the old build folder so the next one starts clean",
              "destructive": 1, "outbound": 0, "touches_external": 0,
              "outside_project": 0, "seen_count": 3}
    text = phrase(sample)
    print("speaking:", text)
    speak(text)
    print(f"\nsay approve / reject / modify ({LISTEN_S}s), or press a/r/m")
    print("try each word a few times - 'modify' is the hardest to recognise")
    choice, how = listen_or_key()
    print(f"got: {choice}  via {how}" if choice else f"no result: {how}")
    if not choice:
        print("\nTo see what it is mishearing:  $env:EARSHOT_VOICE_DEBUG = '1'")
        print("To accept lower confidence:    $env:EARSHOT_CONFIDENCE = '0.2'")

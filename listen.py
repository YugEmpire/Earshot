"""earshot listen - multilingual, accent-tolerant speech input.

  python listen.py            record, transcribe, show the mapped intent
  python listen.py --text "yeah go for it"    test mapping without a mic

Why this replaces the Windows recogniser:
SAPI is shaped around US English and needs a separate speech pack per
language. On an Australian accent saying "approve" it returned "WSJ yeah" at
0.05 confidence. Whisper is trained on accented and multilingual speech and
runs locally, so it handles both problems at once and needs no network.

    pip install faster-whisper sounddevice numpy

Model sizes, roughly, on CPU for a two-second utterance:
    tiny   ~75 MB    fastest, weakest
    base   ~150 MB   the default here - good enough for short commands
    small  ~500 MB   noticeably better on strong accents, ~2x slower
Set with EARSHOT_MODEL. A GTX 1650 Ti has enough VRAM for small if you set
EARSHOT_DEVICE=cuda, though CPU is fast enough for utterances this short.
"""

import os
import re
import sys
import unicodedata

# small.en beats base multilingual on English by a wide margin, and it cannot
# mis-detect the language - "Aprove" was tagged Portuguese because the
# multilingual model had that option. ~500MB, still real-time on CPU for
# two-second utterances.
MODEL_NAME = os.environ.get("EARSHOT_MODEL", "small.en")
FORCE_LANG = os.environ.get("EARSHOT_LANG", "en")   # "" to re-enable detection
DEVICE = os.environ.get("EARSHOT_DEVICE", "cpu")
SAMPLE_RATE = 16000
MAX_SECONDS = float(os.environ.get("EARSHOT_MAX_SECONDS", "6"))
SILENCE_RMS = float(os.environ.get("EARSHOT_SILENCE", "0.012"))
SILENCE_HOLD = 0.7          # seconds of quiet that ends the recording

_model = None


# ------------------------------------------------------------------- intent
# Whisper returns text in whatever language was spoken, so the mapping has to
# be multilingual too. These are short, common forms - not exhaustive
# dictionaries. Anything unmatched returns None rather than guessing, because
# a wrong guess here executes a command you did not authorise.

YES = {
    "en": ["yes", "yeah", "yep", "yup", "ok", "okay", "sure", "fine", "approve",
           "aprove", "approved it", "approv", "go",
           "approved", "go ahead", "go for it", "do it", "good", "right",
           "alright", "correct", "please do", "carry on", "continue", "accept"],
    "hi": ["haan", "ha", "haan ji", "theek hai", "thik hai", "kar do", "karo",
           "ji haan", "haanji", "sahi hai", "chalo", "हाँ", "ठीक है", "करो"],
    "gu": ["ha", "haa", "barabar", "kari do", "karo", "હા", "બરાબર", "કરો"],
    "es": ["si", "sí", "vale", "claro", "adelante", "hazlo", "de acuerdo",
           "aprueba", "aprobar"],
    "fr": ["oui", "ouais", "d'accord", "vas-y", "allez", "fais-le"],
    "de": ["ja", "jawohl", "okay", "mach", "mach es", "in ordnung"],
    "pt": ["sim", "claro", "pode", "pode fazer", "vai", "beleza", "aprove",
           "aprovar", "aprovado"],
    "it": ["si", "sì", "certo", "vai", "fallo", "va bene"],
    "id": ["ya", "iya", "boleh", "lanjut", "silakan"],
    "zh": ["好", "好的", "可以", "是", "行", "同意", "继续"],
    "ja": ["はい", "いいよ", "オーケー", "どうぞ", "了解"],
    "ko": ["네", "예", "좋아", "그래", "진행"],
    "ar": ["نعم", "أجل", "موافق", "تمام", "حسنا"],
    "ru": ["да", "давай", "хорошо", "конечно", "согласен"],
    "vi": ["vâng", "được", "đồng ý", "tiếp tục"],
    "tr": ["evet", "tamam", "olur", "devam"],
}

NO = {
    "en": ["no", "nope", "nah", "stop", "cancel", "reject", "rejected", "rejct",
           "don't",
           "do not", "skip", "never", "hold off", "abort", "deny", "refuse",
           "not that", "no way", "leave it", "forget it"],
    "hi": ["nahi", "nahin", "mat karo", "ruko", "band karo", "नहीं", "मत करो",
           "रुको", "nahi ji"],
    "gu": ["na", "nahi", "na kar", "band kar", "ના", "નહીં", "બંધ કર"],
    "es": ["no", "para", "cancela", "no lo hagas", "detente"],
    "fr": ["non", "arrête", "annule", "ne fais pas", "stop"],
    "de": ["nein", "stopp", "abbrechen", "nicht", "lass es"],
    "pt": ["não", "nao", "para", "cancela", "não faça"],
    "it": ["no", "ferma", "annulla", "non farlo"],
    "id": ["tidak", "jangan", "berhenti", "batal"],
    "zh": ["不", "不要", "停", "取消", "拒绝"],
    "ja": ["いいえ", "だめ", "やめて", "中止", "キャンセル"],
    "ko": ["아니", "아니요", "그만", "취소"],
    "ar": ["لا", "توقف", "الغاء", "إلغاء", "لا تفعل"],
    "ru": ["нет", "стоп", "отмена", "не надо"],
    "vi": ["không", "dừng", "hủy"],
    "tr": ["hayır", "dur", "iptal", "yapma"],
}

MODIFY = {
    "en": ["modify", "change", "wait", "hold on", "edit", "adjust", "different",
           "not like that", "hang on", "one second", "let me", "amend", "tweak"],
    "hi": ["badlo", "badal do", "ruko zara", "thoda change", "बदलो", "रुको ज़रा"],
    "gu": ["badlo", "badal", "thodu change", "બદલો"],
    "es": ["cambia", "modifica", "espera", "ajusta"],
    "fr": ["change", "modifie", "attends", "ajuste"],
    "de": ["ändern", "andern", "warte", "anpassen"],
    "pt": ["muda", "modifica", "espera", "ajusta"],
    "it": ["cambia", "modifica", "aspetta"],
    "id": ["ubah", "tunggu", "ganti"],
    "zh": ["修改", "改", "等一下", "调整"],
    "ja": ["変更", "待って", "修正"],
    "ko": ["수정", "잠깐", "변경"],
    "ar": ["غير", "انتظر", "عدل"],
    "ru": ["измени", "подожди", "поправь"],
    "vi": ["sửa", "đợi", "thay đổi"],
    "tr": ["değiştir", "bekle", "düzelt"],
}

# Checked before the plain YES list: "no, don't do it" contains "do it".
NEGATED = [
    r"\bdon'?t\b", r"\bdo not\b", r"\bnot\b", r"\bnever\b", r"\bno\b",
    r"\bmat\b", r"\bnahi\b", r"\bnahin\b", r"\bna\b",
]


def _norm(text):
    t = unicodedata.normalize("NFKC", (text or "").lower())
    t = re.sub(r"[^\w\s'\u0900-\u097F\u0A80-\u0AFF\u4e00-\u9fff"
               r"\u3040-\u30ff\uac00-\ud7af\u0600-\u06ff\u0400-\u04ff]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _fuzzy(text, table, cutoff=0.72):
    """Catch near-misses like "aprove" for "approve". Only on tokens of four
    or more characters - fuzzy matching short words turns "go" into "no",
    which would execute the opposite of what was said."""
    import difflib
    vocab = [_norm(p) for ps in table.values() for p in ps if len(_norm(p)) >= 4]
    for token in text.split():
        if len(token) < 4:
            continue
        m = difflib.get_close_matches(token, vocab, n=1, cutoff=cutoff)
        if m:
            return m[0]
    return None


def _hits(text, table):
    """Longest phrase first, so 'go ahead' beats a stray 'go'."""
    best = None
    for phrases in table.values():
        for p in sorted(phrases, key=len, reverse=True):
            pn = _norm(p)
            if not pn:
                continue
            pattern = (rf"(?<!\w){re.escape(pn)}(?!\w)"
                       if re.search(r"[a-z]", pn) else re.escape(pn))
            if re.search(pattern, text):
                if best is None or len(pn) > len(best):
                    best = pn
    return best


def to_intent(transcript):
    """Free speech -> 'a' | 'r' | 'm' | None, with the phrase that decided it.

    None is a real answer. An unmapped utterance must fall back to a keypress,
    never to a guess - guessing here runs a command the user did not approve.
    """
    t = _norm(transcript)
    if not t:
        return None, "nothing heard"

    no_hit = _hits(t, NO)
    mod_hit = _hits(t, MODIFY)
    yes_hit = _hits(t, YES)

    # Exact first; fuzzy only if nothing matched at all, so a clean hit is
    # never overridden by an approximate one.
    if not (no_hit or mod_hit or yes_hit):
        no_hit = _fuzzy(t, NO)
        mod_hit = _fuzzy(t, MODIFY)
        yes_hit = _fuzzy(t, YES)

    negated = any(re.search(p, t) for p in NEGATED)

    # Order matters. "no, change it" is modify. "no, don't" is reject.
    # A negation anywhere disqualifies a bare yes.
    if mod_hit and (not no_hit or len(mod_hit) >= len(no_hit)):
        return "m", mod_hit
    if no_hit:
        return "r", no_hit
    if yes_hit and not negated:
        return "a", yes_hit
    if yes_hit and negated:
        return "r", f"negated '{yes_hit}'"
    return None, f"could not map: {transcript[:40]!r}"


# ------------------------------------------------------------------ capture

def _load_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        compute = "int8" if DEVICE == "cpu" else "float16"
        _model = WhisperModel(MODEL_NAME, device=DEVICE, compute_type=compute)
    return _model


def record(max_seconds=MAX_SECONDS):
    """Record until you stop talking, not for a fixed window. A fixed window
    either cuts you off or makes you wait after you have already answered."""
    import numpy as np
    import sounddevice as sd

    frames, silent_for, spoke = [], 0.0, False
    block = 0.1
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32") as s:
        for _ in range(int(max_seconds / block)):
            chunk, _over = s.read(int(SAMPLE_RATE * block))
            frames.append(chunk[:, 0].copy())
            rms = float(np.sqrt(np.mean(chunk[:, 0] ** 2)))
            if rms > SILENCE_RMS:
                spoke, silent_for = True, 0.0
            else:
                silent_for += block
                if spoke and silent_for >= SILENCE_HOLD:
                    break
    if not spoke:
        return None
    return np.concatenate(frames)


# Biases decoding toward the words we actually expect. Whisper weights this
# as context, so near-misses collapse onto the real vocabulary instead of
# onto whatever English word sounds closest.
PROMPT = ("Approve. Reject. Change. Yes. No. Stop. Cancel. Wait. "
          "Go ahead. Do it. Skip it. Modify it.")


def transcribe(audio, language=None):
    lang = language or (FORCE_LANG or None)
    if MODEL_NAME.endswith(".en"):
        lang = "en"          # English-only models reject any other value
    segs, info = _load_model().transcribe(
        audio, beam_size=3, language=lang, vad_filter=True,
        initial_prompt=PROMPT, condition_on_previous_text=False,
        temperature=[0.0, 0.2])
    return " ".join(s.text.strip() for s in segs).strip(), info.language


def listen(max_seconds=MAX_SECONDS, language=None):
    """Returns (intent, detail). intent is 'a'/'r'/'m' or None."""
    try:
        audio = record(max_seconds)
    except Exception as e:
        return None, f"microphone error: {e}"
    if audio is None:
        return None, "silence"
    try:
        text, lang = transcribe(audio, language)
    except Exception as e:
        return None, f"transcribe error: {e}"
    if not text:
        return None, "nothing transcribed"
    intent, why = to_intent(text)
    return intent, f'"{text}" [{lang}] -> {why}'


def listen_text(max_seconds=8):
    """Free dictation, no intent mapping. For spoken notes, where the whole
    point is arbitrary words rather than a fixed vocabulary."""
    try:
        audio = record(max_seconds)
    except Exception as e:
        return None, f"microphone error: {e}"
    if audio is None:
        return None, "silence"
    try:
        text, _lang = transcribe(audio)
    except Exception as e:
        return None, f"transcribe error: {e}"
    return (text or None), ("ok" if text else "nothing transcribed")


def available():
    try:
        import faster_whisper, sounddevice, numpy   # noqa: F401
        return True
    except Exception:
        return False


if __name__ == "__main__":
    if "--text" in sys.argv:
        said = sys.argv[sys.argv.index("--text") + 1]
        print(said, "->", to_intent(said))
        sys.exit(0)
    if not available():
        print("pip install faster-whisper sounddevice numpy")
        sys.exit(1)
    print(f"model={MODEL_NAME} device={DEVICE}  (first run downloads the model)")
    print("speak now, in any language...")
    print(listen())

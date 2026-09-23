"""earshot mic check - is it not hearing, or not matching?

  python micheck.py

Those are different faults with different fixes, and the error message alone
cannot tell them apart:

  audio level stays at 0      -> the microphone is not reaching the recogniser.
                                 No grammar change will help. Fix the input
                                 device.
  level moves, nothing matches -> capture works, recognition does not. That is
                                 a culture/accent problem, and free dictation
                                 or a semantic layer becomes worth building.
"""

import platform
import subprocess
import sys

IS_WINDOWS = platform.system() == "Windows"

_PS_INFO = r"""
Add-Type -AssemblyName System.Speech
Write-Output "--- recognizers installed ---"
$recs = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()
if ($recs.Count -eq 0) {
  Write-Output "NONE - Windows Speech Recognition is not installed."
} else {
  foreach ($r in $recs) { Write-Output ("  " + $r.Name + "   culture: " + $r.Culture) }
}
Write-Output ""
Write-Output "--- audio input devices ---"
Get-CimInstance Win32_SoundDevice | ForEach-Object { Write-Output ("  " + $_.Name + "  [" + $_.Status + "]") }
"""

# Reports the raw audio level the recogniser sees, once a second for 10s.
# This is the number that matters: if it never rises above a couple of units
# while you talk, nothing is reaching the engine.
# Poll AudioLevel directly. Register-ObjectEvent action blocks run in their
# OWN scope, so $script:peak set inside one is not the $script:peak read
# outside it - the previous version of this file reported 0 regardless of
# input. Recognition results DO need events, so those use -MessageData with a
# synchronized list, which does cross the scope boundary.
_PS_LEVEL = r"""
$ErrorActionPreference = 'Stop'
try {
  Add-Type -AssemblyName System.Speech
  $recs = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()
  if ($recs.Count -eq 0) { Write-Output 'RESULT_ERR|no recognizer installed'; exit }
  $r = New-Object System.Speech.Recognition.SpeechRecognitionEngine($recs[0])
  $r.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
  $r.SetInputToDefaultAudioDevice()

  $bag = [System.Collections.ArrayList]::Synchronized((New-Object System.Collections.ArrayList))
  Register-ObjectEvent -InputObject $r -EventName SpeechRecognized -MessageData $bag -Action {
    $null = $Event.MessageData.Add('OK  ' + $EventArgs.Result.Text + ' (' + [math]::Round($EventArgs.Result.Confidence,2) + ')')
  } | Out-Null
  Register-ObjectEvent -InputObject $r -EventName SpeechRecognitionRejected -MessageData $bag -Action {
    $null = $Event.MessageData.Add('REJ ' + $EventArgs.Result.Text)
  } | Out-Null

  $r.RecognizeAsync([System.Speech.Recognition.RecognizeMode]::Multiple)
  $peak = 0
  for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 250
    $lvl = $r.AudioLevel
    if ($lvl -gt $peak) { $peak = $lvl }
    if (($i % 4) -eq 3) { Write-Output ("  second " + [int](($i+1)/4) + "  level now: " + $lvl + "   peak: " + $peak) }
  }
  $r.RecognizeAsyncStop()
  Write-Output ("RESULT_PEAK|" + $peak)
  foreach ($h in $bag) { Write-Output ("RESULT_HEARD|" + $h) }
} catch { Write-Output ("RESULT_ERR|" + $_.Exception.Message) }
"""


def ps(script, timeout=90):
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        text=True, timeout=timeout, capture_output=True)


def main():
    if not IS_WINDOWS:
        print("windows only")
        return 1

    if "--dictate" in sys.argv:
        return dictate()

    print(ps(_PS_INFO, timeout=40).stdout)

    print("--- live audio test ---")
    print("Talk continuously for 10 seconds. Say 'approve', 'reject',")
    print("'change', and a few normal sentences. Starting now.\n")

    out = ps(_PS_LEVEL, timeout=90).stdout
    peak, heard, err = 0, [], None
    for line in out.splitlines():
        if line.startswith("RESULT_PEAK|"):
            try:
                peak = int(float(line.split("|", 1)[1] or 0))
            except ValueError:
                peak = 0
        elif line.startswith("RESULT_HEARD|"):
            heard.append(line.split("|", 1)[1])
        elif line.startswith("RESULT_ERR|"):
            err = line.split("|", 1)[1]
        elif line.strip().startswith("second"):
            print(line)

    print("\n" + "=" * 58)
    if err:
        print("ERROR:", err)
        print("\nUsually means no default recording device is set.")
        return 1

    print(f"peak audio level: {peak}   (0-100)")
    for h in heard:
        print("  heard:", h)

    print()
    if peak <= 3:
        print("VERDICT: the microphone is not reaching the recogniser.")
        print("  Nothing about the word list matters until this is fixed.")
        print("  - Bluetooth headsets in A2DP mode expose NO microphone.")
        print("    Your Sony XM5s are the likely culprit: switch Windows'")
        print("    default input to the laptop mic, or reconnect them in")
        print("    hands-free mode (which drops audio quality).")
        print("  - Settings > Privacy > Microphone > allow desktop apps")
        print("  - Settings > System > Sound > Input: pick a device, test it")
    elif not heard:
        print("VERDICT: audio is arriving but nothing is being recognised.")
        print("  Likely a culture mismatch - the recogniser is en-US.")
        print("  Add the English (Australia) speech pack in")
        print("  Settings > Time & language > Language, or lower the bar:")
        print("    $env:EARSHOT_CONFIDENCE = '0.2'")
    else:
        print("VERDICT: capture and recognition both work.")
        print("  If approve/reject still fail in approve.py, the closed")
        print("  grammar is the problem and free dictation plus a semantic")
        print("  layer is worth building. Show me the 'heard' lines above.")
    return 0


# --------------------------------------------------------------------------
# Synchronous dictation test.
#
# Register-ObjectEvent -Action blocks only execute when the PowerShell
# pipeline is idle, and Start-Sleep blocks it - so the async version above
# can report audio levels OR recognition results, never both. This runs
# Recognize() synchronously in a loop: no events, nothing to block, and it
# answers the only question that matters now - can this machine transcribe
# your speech at all?

_PS_DICTATE = r"""
$ErrorActionPreference = 'Stop'
try {
  Add-Type -AssemblyName System.Speech
  $recs = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()
  $r = New-Object System.Speech.Recognition.SpeechRecognitionEngine($recs[0])
  $r.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
  $r.SetInputToDefaultAudioDevice()
  $r.InitialSilenceTimeout = [TimeSpan]::FromSeconds(3)
  $r.BabbleTimeout         = [TimeSpan]::FromSeconds(3)
  $r.EndSilenceTimeout     = [TimeSpan]::FromSeconds(1)

  $deadline = (Get-Date).AddSeconds(__SECS__)
  $n = 0
  while ((Get-Date) -lt $deadline) {
    $res = $r.Recognize([TimeSpan]::FromSeconds(4))
    if ($res) {
      $n++
      Write-Output ("HEARD|" + $res.Text + "|" + [math]::Round($res.Confidence,2))
    }
  }
  Write-Output ("DONE|" + $n)
} catch { Write-Output ("ERR|" + $_.Exception.Message) }
"""


def dictate(seconds=20):
    print(f"--- dictation test ({seconds}s) ---")
    print("Say, clearly and with pauses between each:")
    print('   "approve"   "reject"   "change"   "yes do it"   "no stop"\n')
    out = ps(_PS_DICTATE.replace("__SECS__", str(seconds)), timeout=seconds + 40)
    heard, n, err = [], None, None
    for line in (out.stdout or "").splitlines():
        if line.startswith("HEARD|"):
            _, text, conf = line.split("|", 2)
            heard.append((text, conf))
            print(f'  heard: "{text}"   confidence {conf}')
        elif line.startswith("DONE|"):
            n = int(line.split("|", 1)[1])
        elif line.startswith("ERR|"):
            err = line.split("|", 1)[1]

    print("\n" + "=" * 58)
    if err:
        print("ERROR:", err)
    elif not heard:
        print("VERDICT: audio arrives but nothing transcribes.")
        print("  The recogniser cannot handle this input. Add the English")
        print("  (Australia) speech pack: Settings > Time & language >")
        print("  Language & region > English (Australia) > Language options")
        print("  > Speech. Then re-run this.")
        print("  A meaning layer cannot help - there is no text to interpret.")
    else:
        good = [h for h in heard if float(h[1]) >= 0.3]
        print(f"VERDICT: transcription works. {len(heard)} phrases, "
              f"{len(good)} above 0.3 confidence.")
        print("  Free dictation plus a meaning layer is now the right build -")
        print("  you say whatever you want, and the words get mapped to")
        print("  approve/reject/modify. Send me the lines above.")
    return 0

if __name__ == "__main__":
    sys.exit(main())

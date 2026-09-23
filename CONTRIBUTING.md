# Notes for working on Earshot

## PowerShell writes a BOM, and it breaks things silently

`Out-File -Encoding utf8` prepends a byte-order mark. Python tolerates it in
source files. JSON parsers do not ? Claude Code will reject a settings.json
with a BOM and give you no useful error, it simply will not load the hook.

Use this instead when generating any file another program parses:

    [IO.File]::WriteAllText("C:\path\to\file.json", $content)

## Claude Code runs hooks through bash

Backslashes get eaten. Use forward slashes in hook command paths, even on
Windows:

    D:/earshot-probe/.venv/Scripts/python.exe D:/earshot-probe/hook_claude.py

## Exit codes, not just stdout

PreToolUse reads exit code 0 as proceed and 2 as block. Printing a JSON
decision and exiting 0 means a "deny" is silently ignored ? the hook looks
like it works while approving everything.

## Exit code 0 hides stdout and stderr

A crash inside the hook is invisible: the command just runs. Everything goes
to hook.log for this reason. If the gate seems not to fire, read that first.

## Two Python versions

The probe runs on 3.11+. Kokoro needs its own 3.12 environment in .tts,
because its dependency chain has no build for 3.14 yet.

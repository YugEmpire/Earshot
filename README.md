# Earshot

Your AI coding agent asks before it acts. Out loud. You answer by speaking.
Hands stay off the keyboard.

Everything runs locally. No audio leaves your machine.

## Status

Early. It works, it has been measured, and it has one user. Expect rough edges.

## What it does

- Reads each action aloud in plain English before it happens
- You answer by voice, in any language - no magic words
- Confirms refusals, because a misheard "no" costs real work
- Offers to stop asking about things you have allowed repeatedly
- Pre-approve a batch of routine work with one spoken decision
- Never skips anything that deletes, publishes, or reaches outside your project

## What each tool can promise

| Tool | |
|---|---|
| Claude Code | **Enforced** - a PreToolUse hook blocks the call. Verified: a spoken reject leaves the file unchanged. |
| Cursor | **Advisory** - the agent is asked and usually complies |
| Windsurf | **Advisory** |
| Codex | **Unverified** |

Advisory is not enforcement. On Cursor the agent can act directly, and the
editor keeps its own ways of prompting you.

## Measured

Approval latency on the same work, same person, voice the only variable:

| | median | p90 |
|---|---|---|
| no voice (3 separate sessions) | 23.0s / 26.0s / 27.0s | up to 115s |
| voice | **7.4s** | 17.4s |

One session ran 45 decisions fully hands-free, 100% answered by voice.

Also measured, and it killed the original design: 118 of 138 distinct
actions happened exactly once. A "you approved this before" rule can
therefore clear at most 30% of prompts; measured, it cleared 25-27%.
See docs/findings.md.

Sample sizes 17-45, one user, sequential arms. Directional, not conclusive.

## Install

Windows, Python 3.11 or newer.

1. Download or clone this repo
2. Double-click **Earshot Setup.bat**
3. Speech tab -> Install speech (about 1 GB, once)
4. Coding tools tab -> Install next to your editor
5. Run tab -> Start listening

docs/setup-guide.md has per-tool detail and troubleshooting.

Windows will warn that "the publisher could not be verified" — it does this for
any downloaded script that isn't code-signed. Earshot listens to your
microphone and sits in front of every command your agent runs, so being wary
here is correct. Everything it does is in 15 readable Python files, and
Earshot Setup.bat is four lines. Read them before you click Run.

## Built on

faster-whisper (MIT) for speech recognition, Kokoro (Apache 2.0) for speech.
Both run on your machine.

## License

MIT

# Earshot — setup guide

Your AI coding agent asks before it acts. Out loud. You answer by speaking.
Hands stay off the keyboard.

It works almost every time. This guide is how to get it closer to every time,
and what your particular tool can and cannot promise.

---

## What "almost every time" means

Two different things can go wrong, and they are worth telling apart.

**Recognition.** Sometimes the speech is misheard. Earshot handles this by
confirming every refusal — say "reject" and it asks "are you sure?" before
acting, because a misheard yes is annoying and a misheard no costs you real
work. Following the tuning steps below takes this from occasional to rare.

**Enforcement.** This one is not about reliability at all — it is about what
your tool permits. Some tools let a gate physically block an action. Others
only let it ask nicely.

| Tool | What it can promise |
|---|---|
| **Claude Code** | **Enforced.** A hook returns a blocking exit code and the action does not happen. |
| **Cursor** | **Advisory.** The agent is instructed to check with you and usually does — but it can act directly, and Cursor keeps its own ways of prompting you. |
| **Windsurf** | **Advisory.** Same shape as Cursor. |
| **Codex** | **Unverified.** Instructions install, but whether it offers a blocking hook has not been tested. Do not assume it enforces. |

If enforcement matters for what you are doing, use Claude Code. Everywhere else
Earshot is a very good habit, not a wall.

---

## First install (once)

1. Unzip Earshot somewhere permanent — Documents, not Downloads.
2. Double-click **Earshot Setup.bat**.
3. **Coding tools** tab: press Install next to each tool you use.
4. **Check** tab: everything green. If speech input is missing it tells you the
   one command to run.
5. **Run** tab: Start listening. Leave that window open.

That is it. It now works in every project, not just the one you set it up in.

Never type in the listening window. It is waiting for your answer, and anything
you type is read as one.

---

## Claude Code

**Enforced.** This is the version that actually blocks.

Installs to `~/.claude/settings.json` as a PreToolUse hook covering Bash, Edit,
Write, MultiEdit and NotebookEdit. Read, search and web tools are not gated —
they change nothing, and gating them would triple your prompts for no gain.

**After installing, verify it once.** This takes a minute and is the only way
to know enforcement is real rather than assumed:

1. Start the listener.
2. Open Claude Code in any project.
3. Ask it to create a file called `test.txt`.
4. When you hear the request, say **reject**, then **yes** to confirm.
5. Claude Code should report the tool was blocked, and quote your reason.

If the file gets created anyway, the hook is not wired up. Check the **Check**
tab, and look at `hook.log` in the Earshot folder — every decision is recorded
there, including crashes.

**Permission mode.** Claude Code's Auto mode judges tool calls itself and runs
the ones it considers low risk. Your hook still has the final say, but Auto
mode means fewer things reach you. If you want to see everything, switch to the
default mode with `/permission-mode`.

**Known gotcha.** Hooks run through bash, which eats backslashes. The installer
writes forward slashes for this reason. If you ever hand-edit that file, do the
same.

---

## Cursor

**Advisory.** Be clear-eyed about this one.

Installs a rules file that instructs the agent to route every action through
Earshot. In practice it complies well — measured at 100% compliance across a
long session. But it is compliance, not enforcement, and Cursor has three of
its own ways to interrupt you that Earshot cannot touch:

- its command approval dialog
- its in-chat question tool
- tool permission prompts

**To get closest to hands-free on Cursor:**

1. Settings → Run Mode → **Allowlist**.
2. Command Allowlist → add these as **separate entries**, one per line:
   - the full path to Earshot's `python.exe`
   - `node`
   - `npx`
   - `npm`
3. Turn **Auto-Accept Web Search** on. A search reads public pages and changes
   nothing on your machine.
4. Restart Cursor after installing the rules. It caches them at startup, and a
   new chat alone is not always enough.
5. In a new chat, ask: *"What are your rules for running terminal commands?"*
   It should describe calling the gate. If it does not, the rules did not load.

**If the agent asks you a question in chat instead of speaking**, the rules did
not take. Re-run the installer and restart Cursor.

---

## Windsurf

**Advisory**, same as Cursor. Installs a rules file at the user level. The
verification step is the same: start a chat and ask it to recite its rules for
running commands.

---

## Codex

**Unverified.** Installs an `AGENTS.md` instruction. Whether Codex has a
blocking hook has not been tested, so treat it as advisory until someone
confirms otherwise. If you test it, the result is worth reporting.

---

## Making recognition rare-fail rather than sometimes-fail

**Use the English model.** Multilingual Whisper picks between 99 languages for
a two-second clip and gets it wrong. English-only cannot make that mistake:

    setx EARSHOT_MODEL small.en

Stronger accents may want `medium.en` — slower, noticeably better.

**Use a wired microphone if you can.** Bluetooth headsets run either in
high-quality audio mode with no microphone at all, or in hands-free mode where
the microphone is phone-call quality. Speech recognition struggles badly with
the second.

**You do not need magic words.** Anything meaning yes, no, or change works, in
any language: "go ahead", "nah don't", "hold on, change that". If it cannot
tell, it asks again rather than guessing — an unclear answer never becomes
consent.

**Say "skip" for notes.** After a refusal it asks if you want to add a note.
Say skip, nothing, or next, and it moves on.

---

## Reducing how often you are asked

Two features, both opt-in, both refusing to shortcut anything risky.

**Stop asking about routine things.** After you have allowed the same action a
few times, it offers: *"You have allowed this three times. Shall I stop asking
about it?"* Say yes and it never asks again.

**Pre-approve a batch of work.** At the start of a task the agent can ask once:
*"typechecks, test runs and writes inside source are fine for the next 90
minutes."* One spoken decision covering fifty actions.

**Neither ever skips anything that deletes, publishes, or reaches outside your
project.** No amount of familiarity earns an exemption for `rm -rf` or
`git push`. Scopes expire on purpose — a permanent one is an off switch you
would forget was on.

To see what has been pre-approved, or take it back:

    py gate.py scopes --session <name>
    py gate.py scopes --session <name> --revoke

---

## When something is wrong

**Nothing is spoken.** The listener is not running, or it started without
voice. Use the Run tab.

**Requests appear but are not spoken.** Speech output failed and fell back
silently. Check the **Check** tab.

**It never asks about anything.** On Claude Code, look at `hook.log` — if it is
empty the hook is not being invoked. On Cursor, ask the agent to recite its
rules.

**It asks about things you already pre-approved.** Risk flags override every
shortcut. Anything that deletes, publishes or reaches outside the project will
always ask. That is deliberate.

---

## What is recorded

Every decision goes into a local database in the Earshot folder: what was
asked, what you said, how long you took, and whether the answer came by voice
or keyboard. Nothing leaves your machine. Speech is processed locally — no
audio is uploaded anywhere.

    py metrics.py --session <name>     what happened
    py check.py                        every decision in order

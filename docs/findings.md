# What measuring 270 approval decisions found

Earshot started as two claims: that a **policy engine** could decide which agent
actions actually need a human, and that a **voice layer** could make the
remaining ones answerable hands-free.

Rather than build both and hope, every approval decision across eight sessions
of real work was logged — an actual software project, built by an AI agent,
with roughly 270 decisions recorded.

One claim held. The other did not, for a reason that turned out to be
structural rather than fixable.

---

## Voice works, and the effect is large

| Setup | Sessions | Median | p90 |
|---|---|---|---|
| No voice | 3 independent | 23.0s / 26.0s / 27.0s | up to 115.2s |
| Spoken requests | 1 | **7.4s** | 17.4s |

Three separate control sessions cluster at 23–27 seconds. The voice session came
in at 7.4.

The mechanism is not faster deciding. It is faster *noticing*. Most of the delay
was the agent sitting frozen while an approval request went unseen in a window
nobody was looking at. The p90 makes this clearest: without voice, some
approvals sat for nearly two minutes. Over-30-second answers went from 17 in one
control session to 2 with voice.

A later session ran **45 decisions fully hands-free, 100% answered by voice,
zero keyboard**.

---

## The policy engine does not clear its own bar

Before running anything, a threshold was written down: a rule set must safely
clear 60% of decisions to be worth building.

Measured: **25–27%**.

The reason is structural. A frequency count over the log - 225
human-answered decisions covering 138 distinct actions - shows the shape
of agent work:

| times an action occurred | number of actions |
|---|---|
| 28 | 1 (typecheck) |
| 23 | 1 (test run) |
| 3-5 | 12 |
| 2 | 6 |
| **1** | **118** |

**118 of 138 distinct actions - 86% - happened exactly once.** Each new
file, each new test, each new module. Repetition lives almost entirely in
the build-and-test loop.

That sets a hard ceiling. A rule that stops asking once you have approved
something twice can only ever clear the sightings past the second one: 67
of 225 decisions, or **30%**. Measured safe auto-clear was 25-27% - two
independent methods landing in the same place.

That killed the original thesis and replaced it with a better one: **scope
pre-approval**. One spoken decision covering a phase of routine work
("typechecks, test runs and writes inside source are fine for the next 90
minutes") collapses many first-time actions into a single choice, while
anything risky still interrupts.

Combined — repeats auto-approved by consent, novel work batched by scope, the
residue answered by voice — one session removed **84% of prompts**: 32
auto-approved, 6 asked, all six answered by voice.

---

## Advisory is not enforcement

The same layer was built for two platforms, and they are not comparable.

**Claude Code** exposes a PreToolUse hook. It returns a blocking exit code and
the tool call does not happen. Verified directly: a spoken reject left the file
unchanged and the agent reported the block.

**Cursor** has a rules file. The agent complied well — 100% gate compliance
measured across a full session — but compliance is not enforcement. During
testing, Cursor interrupted through **three separate channels** the layer could
not touch: its command approval dialog, its in-chat question tool, and tool
permission prompts. Each bypassed the voice layer entirely.

An approval product that sits beside the agent rather than inside it cannot
suppress the host's own channels. It can only ask politely.

---

## The unresolved question, which matters more than either claim

Across ~270 decisions, **fewer than ten were refused or modified** — and at
least one of those refusals was a speech misrecognition, not a real objection.

The approval loop has never once prevented something its user would have
regretted.

Voice makes that loop fast and pleasant, and that is now well evidenced. But
pleasant and unnecessary are compatible. No further self-instrumentation settles
this — it needs people other than the builder who run agents daily.

Worth noting alongside: during this period Claude Code shipped Auto mode, where
the platform classifies each tool call and runs the low-risk ones itself. The
policy layer this project set out to build is now a default in at least one
host.

---

## Honest limits

- One user, one machine, one codebase
- Sessions ran sequentially, not interleaved; order and fatigue effects
  uncontrolled
- Sample sizes of 17–48 per arm
- The hands-free session used a different configuration again, so it is not
  directly comparable to the 7.4s figure
- Five separate sessions were lost to setup errors before a working measurement
  was collected, which says as much about the tooling as the method

It would not survive peer review. It was enough to make build decisions on.

---

## What was built in the end

Spoken requests in plain English. Local multilingual speech input. Spoken
confirmation on refusals, because a misheard "no" costs real work.
Consent-based auto-approval that asks before it stops asking. Time-limited
scope pre-approval. Risk flags that veto every shortcut — nothing that deletes,
publishes or reaches outside the project is ever auto-cleared, no matter how
familiar. A full audit trail of every decision, stored locally.

The measurement rig required six attempts to configure correctly, each failure a
stale file or a mistyped label. That is itself a finding: if setup is this
fragile for the person who built it, it is not yet ready for anyone else.

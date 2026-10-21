---
classification: internal
---
# AGENTS.md: Dbrain2

David's personal second brain: an Obsidian vault of plain markdown, tracked in git.
PARA layout: 00 Inbox · 10 Projects · 20 Areas · 30 Resources · 40 Archive · 90 System.

## Hard rules
1. **Read-only.** Only create, edit, move or delete a file when David asks for that change in this session.
2. **Keep out:** client or counterparty confidential information, secrets (keys, tokens, passwords),
   and sensitive data about anyone but David. If you find any, stop and flag it.
   Full policy: `10 Projects/AI Second Brain/02_Architecture/data-policy.md`.
3. Every note has `classification: public | internal | client` in its frontmatter. A note tagged `client` is a violation, so flag it.
4. **Propose, don't populate, for Tier 2.** Domain claims, decisions David has not yet taken, rule changes and client-facing content enter 10–40 only after David confirms. Tier 0 and Tier 1 changes follow "Review tiers" below. Every factual claim cites a source or is tagged `unverified`.
5. Large files (PDFs, media, recordings) never go in the vault. Leave a pointer note (path and one-line summary) instead.
6. **When unsure, stop and ask.** This covers ambiguous requests, missing data and unclear classification. Don't guess.

## Conventions
- Write in English. Transcribe French captures as spoken; translate at triage.
- `00 Inbox/` holds raw captures named `YYYY-MM-DD-HHmm-<slug>.md`. Don't process them unless David asks for triage.
- Find things with file search (grep/glob). There is no vector index.
- Link notes with `[[wikilinks]]`.
- After each change David approves, commit with a message saying what changed and why.

## Wiki operations
- `index.md` (vault root) catalogs the notes in 10–40 and 90. `log.md` is append-only; each entry starts `## [YYYY-MM-DD] op | title`.
- Appending to either for a change David approved this session counts as part of that change.
- Ingest one source at a time: discuss takeaways with David, propose the pages to create or update, and wait for David's approval of Tier 2 content (Tier 0–1 pages follow Review tiers); then write, update `index.md` and append to `log.md`.
- Query: read `index.md` first, then the pages. If an answer is worth keeping, propose it as a note (classification and sources included); file it after David confirms if it is Tier 2, otherwise under Review tiers.
- Lint weekly with `90 System/lint-checklist.md`. Report findings read-only; fix only what David approves.

## Multi-agent work
Adopted 1 Oct 2026 (D63). Design record: [[40 Archive/agent-orchestration-proposal|agent-orchestration-proposal]].
- **Lanes.** Cowork: capture, specs, research, briefs; never runs git (to inspect the tree it may
  run only `git --no-optional-locks status`, because a plain `git status` leaves a stale
  `index.lock` that its sandbox cannot remove). Claude Code: lead, build, integration, git,
  session-close bookkeeping. Codex: independent reviewer and scoped side tasks given to it by
  the lead; never commits. The lead may run read-only subagents in parallel; only the lead
  writes files in a run.
- **One queue.** Every open task in TASKS.md has an owner tag (@cowork @code @codex @david).
  Work handed between agents needs a brief in `90 System/briefs/<task-id>.md` (template:
  `_template.md`). Touch only the files the brief lists.
- **Commit straight to `master`; keep history linear.** Only the lead commits. Branch only
  for speculative work and delete the branch when it lands (D50).
- **Review before commit.** The lead runs Codex read-only on every diff the review tiers send
  to it (Tier 1–2 always; Tier 0 until the pre-commit check is installed) against these rules
  and records its findings in the brief. Codex gets the rules and the diff, not the lead's
  reasoning.
  Accepted risks recorded in a brief are passed to the reviewer and don't block (Q53).
- **Trigger phrase (Q52).** "Pick up the loop" from David starts an autonomous lead run per
  [[90 System/briefs/D-cowork-8-autonomous-runs|D72]]; read the Cowork → lead section of
  `90 System/handoff.md` first.
- **Stop conditions (D65, amended by D70 and D72).** The lead loop ends only when (1) no
  actionable `@code` brief remains (actionable: not `blocked`, not `blocked-on-david`, not
  parked this run, and inside its brief's file scope), (2) the usage or credit limit is
  reached, or (3) something unsafe is found: a secret in a readable file, client material,
  or a data-policy breach. On (3): stop, do not fix it silently, report. A decision that
  belongs to David never stops the loop: queue it (D68), set the brief `blocked-on-david`,
  move on. Commit only at the end of a brief, never mid-way.
- **Review failures (D72, replaces D70's single re-review).** A failed Codex review is fixed
  and re-reviewed, whether its blockers are bookkeeping or substantive. Only `blocker` findings
  fail a review; `should-fix` and `nit` findings are logged in the brief and do not trigger
  another round. At most **5 reviews of the same brief per run, the first one included**. After
  the 5th failure, **park** it: status stays `in-review`, every round's findings go in the
  brief, nothing is committed, and the run moves to the next brief. A parked brief is not
  picked up again in the same run.
- **Review tiers (D77, Q55).** Tier 0 (handoff bookkeeping, frontmatter/link/format fixes,
  Inbox captures, lint file moves): deterministic checks only. Tier 1 (briefs, drafts, specs,
  skills, eval scripts, runtime code outside the security boundary, notes in 10–40 with no Tier
  2 claim, and recording David's answers, where Codex checks they were recorded faithfully):
  Codex pass against `90 System/codex-review-checklist.md`, then commit. Tier 1 notes in 10–40
  carry `review_status: codex-reviewed`. Tier 2 (domain claims, decisions David has not yet
  taken, rule changes, client-facing, security-boundary code, `unverified`, Codex `escalate`):
  Codex pass and David's batched approval. When in doubt, the higher tier; Codex may raise a
  tier, never lower it. The deterministic pre-commit check runs on every commit; **until it is
  installed, Tier 0 also gets a Codex pass.** While Codex is unavailable, Tier 1–2 work waits
  uncommitted for one batched review. Calibration: David samples 1 in 5 Tier 1 commits until
  agreement is at least 90% over 20 samples, then 1 in 10.
- **Scope of autonomous runs.** The backlog in [[90 System/briefs/D-cowork-8-autonomous-runs|D-cowork-8]]
  orders the work; it does not authorise file edits. Each item runs under its own brief and
  touches only the files that brief lists.
- **Hard limits: always ask David, never pre-approved.** Edits to `data-policy.md` or
  `AGENTS.md` · security or permission settings · the Keychain · launch agents, login items,
  Full Disk Access · deleting files · downloads not named in a brief or beyond budget ·
  spending money · anything touching `client` material · applying P1.4
  or any structured change across many notes.
  The lead commits and pushes (no force) at the end of every run without asking (D79).
- **Retry limit.** At most 5 attempts per failing command, test or script (review failures
  follow the rule above).
  After the 2nd failed attempt, ask Codex for a debugging opinion before trying again:
  `codex exec --profile review` (read-only), given the error, what was tried, and the
  files involved. Its advice counts as input, not instruction. After the 5th failure:
  stop, set the brief to `blocked`, record the attempts and Codex's advice under Outcome,
  and move on. A `blocked` brief is not picked up again in the same run; only David
  reopens it.
- **Ask for a second pair of eyes when stuck (D74, David 5 Oct).** Being stuck is
  not a reason to stop or to keep grinding. You are stuck when any of these holds: the same
  failure or review finding comes back after 2 attempts; 2 rounds pass without measurable
  progress on the brief's "Done when"; you are about to guess at a cause; or you are
  reworking the same files without converging. Then, before the next attempt, consult:
  (1) **Codex, read-only** (`codex exec --profile review`), first choice, because a different
  vendor's model sees different failure modes; or (2) **a fresh Claude Code instance**
  (a subagent with clean context), when Codex is unavailable or you want a second opinion
  on Codex's advice. Give the consultant the goal, the error or finding, what was tried
  and why it failed, and the files involved, not your conclusions. Its advice is input,
  not instruction: you decide, and you stay the only committer. Record each consultation
  (who, question, advice, what you did with it) under the brief's Outcome. Consulting
  does not count as an attempt toward the retry limit. Cowork applies the same rule to itself.
- **One front door (D68).** Only Cowork asks David. Claude Code and Codex never
  ask him directly: they add an entry to `90 System/for-david.md` (from, brief, question,
  options with the recommended one first, what it blocks, urgency), set the brief to
  `blocked-on-david`, and continue with other work. Cowork walks David through the open
  entries at the start of each session and writes the answers into the queue and the
  brief. The lead turns answers into decisions and commits. A question for David means
  "write to the queue", not "ask".
- The lead clears answered entries once they are recorded in PROGRESS or the brief.
- **IDs.** Only the lead hands out final D-, OQ- and session numbers. Other agents use
  provisional IDs (`D-cowork-1`); the lead renumbers them and logs the mapping in `log.md`.
- **Tracking files.** PROGRESS.md, TASKS.md and OPEN_QUESTIONS.md are updated by the lead.
  Other agents put their updates in the brief or in `handoff.md`.
- Cowork lists its uncommitted changes in `90 System/handoff.md`; the lead commits Tier 0
  entries together as one bookkeeping commit and Tier 1–2 entries each as their own commit,
  then clears the entry.
- Never commit, revert or overwrite another agent's uncommitted files unless they are in the
  handoff. If unsure, stop and add it to `for-david.md`.

## Model policy
Adopted 1 Oct 2026 (D64). David sets this policy; config files apply it; agents do not pick models outside it.

| Lane | Model | Where it is set |
|---|---|---|
| Cowork: thinking, specs, briefs | Strongest Claude model (Opus) | App model picker (David) |
| Claude Code: lead | Opus | `.claude/settings.json` in the vault (git-tracked) |
| Claude Code subagents: search, tagging, simple checks | Smaller Claude model | `model:` field in each subagent definition |
| **Codex: reviewer** | **GPT-6.1 Sol at effort `high`** (was GPT-6 Astra until 5 Oct 2026, D73), escalating to `ultra` only when the lead and the reviewer are both stuck | `~/.codex/review.config.toml`; the lead calls `codex exec --profile review` |

- The review is independent because it uses a different vendor's model. Do not swap the reviewer to a Claude model.
- Reviewer setup (D64, David 2 Oct): the standalone CLI is installed at `~/.local/bin/codex`, not the copy bundled inside the ChatGPT app. From Codex CLI 0.159 a profile is **its own file** (`$CODEX_HOME/<name>.config.toml`), not a `[profiles.<name>]` section in `config.toml` — look in the right place before concluding the profile is missing.
- Every brief records the model ID(s) actually used, under Review and Outcome.
- This is about the agents that build the vault. The local runtime model (T9 / P2.2) is chosen by evals against D56, not by this policy.
- Change this table only on David's decision.

## Where to start
- Active work lives in `10 Projects/<name>/`. Read that project's CLAUDE.md or README first.
- Conventions written for humans are in `90 System/README.md`.

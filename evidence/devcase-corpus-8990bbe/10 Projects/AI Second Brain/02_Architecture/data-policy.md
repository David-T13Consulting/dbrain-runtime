---
classification: internal
version: 0.6
status: scope confirmed 29 Sep 2026; implementation schedule amended at David's request 1 Oct 2026; personal-data waves and children's rule approved by David 5 Oct 2026 (D69); temporary §7 exception for the medispring-infi archive approved by David 6 Oct 2026 (Q63)
---

# Data policy — Dbrain2 / AI Second Brain

**v0.6 (7 Oct, Q76/P2.7):** adds §7a, the failure store (exact text approved by David on 7 Oct). Nothing else in the policy changes.

**v0.5 (6 Oct, Q63):** temporary exception to the §7 boundary "client material physically unreachable", scoped to `~/Archive/medispring-infi` (former-employer confidential material, treated as client material). The boundary there is (1) the configured-roots check: the folder and its ancestors are outside every folder configured for Claude Code, Codex and Cowork, checked by realpath and failing closed, and (2) the no-access rule: no agent opens, reads, lists, runs git in or links to it. Ends when P1.3 verifies enforcement or on 31 Dec 2026, whichever is first; if P1.3 hasn't verified it by then, David decides again. Nothing else in the policy changes.

**v0.4 (5 Oct, D69):** personal data beyond training/HR enters in gated waves (§4a), and children's data has its own rule (§3a). Approved by David as Q20, revised after review and approved again as Q25. The red line and §2 are unchanged.

**v0.3 scheduling clarification (1 Oct):** align T9, write gates and trace masking with roadmap S1–S6. Permitted data, exclusions and contract rules are unchanged.

**Red line, in one sentence:** No client or counterparty confidential information, no secrets, and no
sensitive data about anyone other than me ever enters this system. Everything else is tiered, and
the tier sits in frontmatter.

## 1 · Tiers

| Tier | What it covers | In the vault? | Sent to a model API? | Shown in a demo? |
|---|---|---|---|---|
| `public` | Anything already published or publishable: posts, course notes with nothing confidential in them, public research | ✅ | ✅ | ✅ |
| `internal` | My own personal and business material: notes, captures, my Strava/HR data, my Tower13 rates, pipeline and positioning, minimised mentions of third parties (§3) | ✅ | ✅ | ✅ my own data · ❌ third-party mentions |
| `client` | Anything containing a client's or counterparty's confidential information, names or terms | ❌ **never** | ❌ | ❌ |

The `client` tag exists so the rule can be checked (a note carrying it is a violation to be removed), not as permission to store client material.

## 2 · Never enters the system, whatever the tier

1. Client or counterparty confidential information, including deliverables, data, meeting content and contract terms
2. Secrets such as API keys, tokens, passwords and passcodes. Those go in env vars or the keychain, never in a file an agent can read
3. Government ID numbers, bank or card numbers
4. Health, financial or ID details about anyone other than me
5. Large binaries (media, PDFs, recordings). They stay outside the vault with a pointer note (rule 7)

## 3 · Third parties (family, contacts)

- Allowed in a minimised form: first name plus the context needed. Nothing sensitive (see §2.4).
- Notes that mention a third party are never screen-shared in a demo.
- Children: §3a applies instead, and it is stricter (no first names).

## 3a · Children's data

1. **No names, initials or nicknames.** Use neutral labels ("child A", "child B") or none at all.
2. **No identifying places.** No school, club or gym names, no addresses, **no town or neighbourhood**. Use generic nouns ("school", "climbing club").
2a. **Minimise before capture, not at triage.** Combinations can identify a child even without names (times + activity + town). Capture only what the logistics need: day, time, activity type, what to bring. A capture that breaks §3a is deleted at triage, not cleaned up and kept.
3. **No health, behaviour, performance or photos** of children, whatever the tier (extends §2.4).
4. **Never shown in a demo.** Demos use a synthetic family.
5. **Manual capture only** for now (Inbox → triage). No portal, email or website integrations before month 4 (no MCP sprawl).
6. Logistics only: what, when, what to bring or prepare. This feeds the parked second domain (OQ-11) as **data, not a built feature**. No family-logistics features until the H1 gate.

## 4 · My own special-category data (Strava / heart rate)

- Heart rate and training data count as health data under GDPR Art. 9. It is **my** data, used with my explicit choice.
- Tier: `internal`. It may be sent to a model API and shown in demos.
- In a demo, say this out loud: *"This is special-category data. I chose to use my own, and here's what that means for yours."* That's part of the governance story, not something to hide.

## 4a · Personal data beyond training: waves

**Effective date:** the waves govern material **added from 5 Oct 2026**. Ordinary personal material already in the vault under v0.3 (profile, goals) stays; P1.4 relabels it on the four axes and flags anything that would now be wave 2 for David to keep, move or remove.

| Wave | Content | Labels (OQ-20 axes: domain · sensitivity · processing · sharing) | Gate before entry |
|---|---|---|---|
| 0 (in force) | David's own training and HR data | personal · sensitive · approved external · approved for demo (§4, David's explicit choice) | none, already permitted |
| 1 | Ordinary personal material: routines, family logistics without names, reading, goals, non-financial planning | personal · ordinary · approved external · private | **P1.4 four-axis retrofit complete** and the lint check validates the four axes on new notes |
| 2 | Sensitive personal material: health beyond training, finances, journal | personal · sensitive · **local-only** · private | All five: (a) P2.2/P2.3 local query service answers with no silent cloud fallback; (b) cloud agents (Cowork, Claude Code, Codex, claude.ai Project) **cannot reach** wave-2 files, verified by an access test, not by labels; (c) synthetic sensitive-note leak test passes (titles, search, listings, logs, traces, agent handoffs); (d) trace redaction live; (e) a deletion rule decided **and tested** that removes an item from the working copy, git history **and every backup** (T8/P6.1) |
| never | Others' sensitive data, client material, secrets | §2, unchanged | — |

- Labels inform controls; they never grant access by themselves.
- **Known tension:** gate (b) will probably need a separate local-only store outside the paths granted to cloud agents. That revisits OQ-20 (labels only, no vault split) **at wave 2, not now**. A separate purgeable repo would help with gate (e) for git history only; backups still need their own deletion rule.

## 5 · Model providers

- Assume the default commercial API terms: **inputs and outputs are kept for about 30 days** unless a ZDR agreement exists (see `02_Critical_Assessment.md` §3).
- Anything sent to a model API must therefore be something I would accept being stored by that provider for 30 days, which means `public` or `internal` only.
- The model/hosting choice is checked at T9 (now S2), and again before any external-provider comparison or integration. Verify actual provider terms before use; local operation has no silent cloud fallback.

## 6 · Tower13 commercial material and contracts

**Rule (D16, 29 Sep):** every client contract must state clearly that I use a personal AI agent. Until a
contract says so, nothing about that engagement goes into the system, **including my own notes about it**.

| Contract state | What may enter the system |
|---|---|
| States my AI agent use explicitly | My own `internal` notes about the engagement: my time, my learnings, my rates. Counterparty confidential material still never enters (§2.1). |
| Silent or ambiguous | Nothing about the engagement. |
| Restricts or bans AI use | Nothing about the engagement, and I don't use the agent on it at all. |

- My own rates, proposals, pipeline and positioning, when they don't mention an engagement, stay `internal`.
- A disclosure clause makes my own notes permissible. It never makes client data permissible. §2.1 is absolute.
- Going forward, the Tower13 contract template carries a standard AI-use disclosure clause (T0.7).

## 7 · Enforcement (current → planned)

| Control | Now | Planned |
|---|---|---|
| Classification frontmatter on every note | ✅ convention | Lint check (H1) |
| Agent access | Read-only default; specific session edits only when authorized | Scoped deterministic writer at S3 after T4.2 restore/rollback tests; exact-patch approval remains required |
| Secret scanning | Manual | Pre-commit scan (H1) |
| Client material physically unreachable | Required boundary; outside-vault location alone is not proof | Verify allowed/reachable roots in P1.3 without reading prohibited material |
| PII in traces | n/a | Redaction/retention from the first traces at S2; verify at S6 |
| Exception: medispring-infi archive (v0.5, Q63) | Configured-roots check + no-access rule | Ends at P1.3 verification or 31 Dec 2026 |

## 7a · Failure store (v0.6)

Failure store. Ask Brain may keep the text of a question and its answer only when the question raised a failure signal, only in a local store on the host, outside the vault and outside git, readable only by the local model and by David. Unapproved failures are deleted after 90 days. No cloud model, agent or reviewer reads question text: cloud agents work from failure classes and synthetic equivalents. The weekly review reads counts, classes and synthetic equivalents only.

## 8 · Changing this policy

Only David changes it. Record the change as a decision in `PROGRESS.md` and bump `version`.

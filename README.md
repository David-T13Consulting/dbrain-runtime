# dbrain-runtime

A scoped query service over the Dbrain2 vault, read-only except for one Inbox write (PL1 steps 6-7). Built for **P2.2**.

Separate from the vault on purpose: the vault holds knowledge, this holds code. David
authorised this location on 2 Oct 2026. Pushed to a private GitHub `origin`; whether any
other backup holds it is not tested (T4.2: `python3 -I -B experiments/restore_test.py runtime`).

```sh
./query "What counts as a tempo session?"        # needs a local model running
./query "..." --stub-model                        # no weights needed; pipeline only
./run_dev_cases.py --stub-model                   # the 10 dev cases, retrieval only
./secret_scan.sh                                  # self-testing credential scan
python3 -m unittest discover -s tests -t .        # the full suite; counts live in the P2.2 brief
./measure.sh <label>                              # measured run, evidence kept together
```

## What it does

`query` retrieves evidence from an allowlisted slice of the vault, asks a local model to
answer **only** from that evidence, and returns one of three verdicts with citations
carrying file paths and line ranges:

- **supported** — cited retrieved evidence, the answer's wording overlaps it, **and** an
  entailment verifier confirmed it. No verifier exists yet (T4.1), so today nothing is
  reported as supported: a grounded answer comes back `incomplete`, reason "entailment
  unverified"
- **incomplete** — cited nothing, invented an id, or wandered off its own citation
- **withheld** — nothing retrieved, or the model itself declined

The `default` and `devcases` profiles read a **frozen copy** of their three files as the
C2 baseline read them on 8 Oct (`evidence/`, Q138), not the live vault, so every model is
measured on the same evidence. The phone profile reads the live vault.

## The boundary

`dbrain/scope.py` is the only way the service reads the vault. The invariant is blunt on
purpose: **no symlink anywhere between the vault root and the file**, and the allowlist is
checked on the *lexical* path.

Resolution-based authorisation was the first design and it was breakable: a symlinked
allowlist root authorised its target, and resolving then opening left a window in which
a component could be swapped. Now the path is normalised (`..` refused outright), checked
component by component against the allowlist, then walked with `openat` + `O_NOFOLLOW`
anchored to the vault's own descriptor. The descriptor chain *is* the check, so there is
no window between them.

A note is refused at index time unless it carries a recognised `classification`, and
`client` is always refused. Quoted values, trailing comments and casing are all handled;
a missing or unrecognised label is a refusal, because absence of a label is not permission.

## No cloud fallback

`dbrain/adapter.py`:

- refuses a non-loopback endpoint **at construction**, and separately requires every
  address the host resolves to be loopback — a name on the allowlist that resolved
  off-box would otherwise pass;
- **refuses 3xx redirects** rather than following them. A local endpoint answering with
  `302 http://example.com/...` was the one bypass that actually reached outside;
- strips ambient proxy settings, so `HTTP_PROXY` cannot move a loopback request off-box;
- raises `ModelUnavailable` when the model is down, and `RedirectRefused` subclasses it,
  so a caller handling "model down" cannot mistake a redirect for a successful answer.

There is deliberately no fallback branch in that file. Provider keys are detected only so
a trace can record that they were ignored; no code path reads their values.

## Rented providers

`dbrain/provider.py` (PV1 step 4, D135) can send a question to a rented model. It is
**not wired into any entry point yet**: the phone service and `query` stay on the local
model (stage 1, D141, nothing leaves the Mac). It exists for stage 3.

- **Chosen only by explicit configuration.** `DBRAIN_PROVIDER`, `DBRAIN_LEVEL` and
  `DBRAIN_PROVIDER_MODEL` must all be set and must all match a closed registry in the
  code (today: Scaleway, level L1, three model IDs). Unset means the local adapter, as
  before; half-set is refused. Adding a provider, level or model is a reviewed code
  change, not configuration.
- **No fallback.** A provider that is down, redirects, answers malformed or reports a
  different model raises; nothing retries another provider, model, level or the local
  model.
- **The key** is read from the macOS Keychain (`/usr/bin/security`, with a timeout) at
  each call, never from the environment, never kept on the adapter, never in a message,
  a trace or `describe()`. No key fails closed before any network call. An answer
  containing the key is refused.
- **Error texts are fixed.** Nothing a server sends (error body, redirect target, finish
  reason) is echoed back, since it could carry the key; exception chains are suppressed.
- **Egress** goes only to the registry's base URL, over TLS, with ambient proxies stripped
  and every redirect refused. The endpoint is rebuilt from the registry on every call;
  no caller can pass a URL. Tests name only a port (`test_port`): host `127.0.0.1`,
  plain http and the path are fixed in code, and test mode refuses the Keychain, so it
  needs an injected dummy key.
- **Bounded:** responses over 2 MB are refused; usage is reported per call, never kept.

**Not detectable:** a provider that silently answers with a successor model **under the
requested ID**. The adapter refuses an answer whose reported model differs from the
configured one, but cannot see a substitution the provider does not report (Scaleway
documents replacing models after end of life). PV1 step 5 records the model list at
measurement time; a changed list is the only signal.

## Writes

The read side is guarded by `scope.py`; the write side has its own guard,
`dbrain/outpath.py`, because destinations are configurable (`DBRAIN_ARTIFACTS`, `--out`).
Without it, a symlinked output directory could aim a trace or a report back into the vault
and truncate a note. Every write destination is resolved and refused if it lands inside
the vault, and then opened by walking that resolved path from `/` with `openat` +
`O_NOFOLLOW`, creating missing directories inside the same walk. Opening the absolute path
was not enough (round C): `O_NOFOLLOW` guards only the last component, so a parent swapped
for a symlink after the check redirected the write. A test performs exactly that swap.
Callers never `mkdir` an output directory themselves for the same reason.

**The one vault write (PL1 step 6).** `dbrain/writer.py` is the only code allowed to write
inside the vault, and it has one operation: a new file in `00 Inbox/`, for a phone capture
David heard read back and saved (`POST /capture/save`, see `mvp/README.md`). It opens the
vault's real path from `/` with `O_NOFOLLOW`, writes a hidden temporary file `O_EXCL`,
places the note with `link()` (which never replaces a name), removes the temporary file,
and checks the result. It never edits, never creates a folder, and does not use
`outpath.py`. `/ask` still writes nothing to the vault (`tests/test_readonly.py`).
A correction from the phone (PL1 step 7, D143) is the same operation: one more Inbox note
naming the corrected note in `corrects:`; `index.build` then leaves that note out. Nothing
is ever edited (`tests/test_correct.py`).

The opened file must be a regular file with exactly one link, checked on the descriptor
before anything is truncated (round D): a hard link to a vault note passes `realpath` and
`O_NOFOLLOW`, and writing through it would change the note. `measure.sh` is shell, so it
asks the same guard for its evidence directory (`python3 -m dbrain.outpath mkdir`), then
`cd -P`s into it and writes only relative to that directory, with `noclobber` on. Nothing
is written before the guard has passed (round D; a test aims it at a vault).

Destinations are also compared with the vault by **filesystem identity** (device and inode),
not only by path, for every existing ancestor and every directory walked through: on a
case-insensitive volume `dbrain2` is `Dbrain2` (round F). Reads refuse hard-linked notes the
same way (round E).

**Labels (rule 3), an allowlist (round K redesign, Q46):** a note reaches the model only
if its frontmatter is plain LF text that both readers parse to the same result, with
exactly one top-level `classification` whose value is plainly `public` or `internal`. The
accepted language: column-0 `key: value` lines, blank lines and column-0 comments; no
indentation; plain values (not containing `: `), one-line quoted strings or one-line flat
flow lists; no CR, other line break, control, tab, stray BOM or backslash; no duplicate
keys. The whole block is validated before its label counts. Where PyYAML is available it
must read the same keys and exactly the same label; a parse error refuses the note.
**Stop or skip (David, Q46):** a recognisable `client` label stops every query
(`ProhibitedNote`, AGENTS.md rule 3), whether in a valid block or in one that fails
validation (a `classif…:` key in any spelling whose value, on its line or on
more-indented lines below, says `client` outside a comment). Anything else outside the
language, any other label, and a note with no label are refused (`UnverifiableNote` /
`ScopeError`): the whole-vault profile skips them and lists them in the trace, the strict
profiles fail. A refused note is never indexed. **Residual risk, accepted in Q46:** a client
label disguised beyond recognition is skipped rather than flagged; it still never reaches
the model. CRLF notes and notes with an unquoted `: ` in a value (four on 5 Oct) are
skipped, not repaired.

**Limits of "read-only", stated:** the guards stop misconfigured, aliased, symlinked and
hard-linked destinations and the ancestor swap between check and open. They do not stop a
concurrent process running as David that re-links or moves a destination *while* it is being
written: such a process can already write the vault itself. A hard link made after the open is
detected when the file closes, after the write, and a link that comes and goes in between is
not seen at all.

Raw output (traces, reports, measurement evidence) lives in `artifacts/` in this repo,
git-ignored (David, Q23). It was under `~/Documents/Dbrain2-artifacts/runtime/` until 5 Oct.
The 1 Oct benchmark inputs the dev cases come from are in `~/Documents/Source_Material/AI_Second_Brain/local-models/` (moved from `~/Documents/Dbrain2-artifacts/` on 6 Oct).

## What is proven, and what is not

| P2.2 "Done when" | State |
|---|---|
| 1 · answer + citations with line ranges + supported/incomplete/withheld; abstains with no evidence | **Pipeline proven**; the generated prose needs a model |
| 2 · scope enforced in code | **Proven** — tests each asserting on an attempted **read**, not merely a path check. Covers `..`, a symlink out of the vault, a symlink to a forbidden note, a symlinked allowlist *root*, a symlinked allowlist *file*, a symlinked intermediate directory, a post-index swap to a symlink, `90 System/`, `Source_Material`, and a mis-cased root |
| 3 · read-only | **Proven** — every file in the vault is hashed before and after, for both an in-process query and a real CLI invocation **with tracing on**, which is the path that actually writes |
| 4 · no silent cloud fallback | **Mostly proven.** Non-loopback refusal, off-box DNS refusal, redirect refusal against a live local server, proxy neutralisation, loud failure, and key-indifference are all tested. Network-blocked half **met 5 Oct**: both measured passes ran under a `sandbox-exec` profile denying all outbound traffic except `localhost:18087`, provider keys unset |
| 5 · 10 dev cases; p50/p95, peak memory, pinned versions | **Measured 5 Oct, run B** (three-file corpus, after round C): p50 23.4 s, p95 42.1 s (n=20), server peak RSS 4.14 GiB. Misses the D56 p95 < 8 s target. Run A's figures are superseded (wrong corpus). Evidence: `artifacts/2026-10-05-b-evidence/` (moved from `Dbrain2-artifacts/runtime/` on 5 Oct, Q23); results note (draft) in the vault: `02_Architecture/p2.2-query-service-2026-10-05.md` |
| 6 · redacted trace outside the vault | **Proven** — the question is a SHA-256, never clear text; the trace records every note indexed, not only the retrieved ones. A query whose model call fails (down, or a malformed reply) is traced too, with status `failed` (round D) |
| 7 · secret scan clean | **Proven** for this repository, and the scanner self-tests against a planted key so it cannot pass vacuously, and against a missing target, so an error cannot pass as clean (round D). It includes `artifacts/`. Scope is this repo, not the whole disk |
| 8 · Codex review | Rounds A onward; findings and dispositions in the P2.2 brief in the vault |

**"Proven" means "a test fails if it stops being true."** It does not mean the boundary is
unbreakable. The things most likely to be wrong are the ones no test covers: the retrieval
quality, the model's faithfulness, and whatever attack nobody thought of.

### Grounding is not faithfulness

An answer that cites retrieved evidence must also have at least 15% of its content words
in what it cited (`GROUNDING_THRESHOLD`), or it is `incomplete`. That catches the gross
failure — an answer citing real evidence while discussing something else, which scores 0 —
without rejecting legitimate paraphrase. Passing it is necessary for `supported`, never
sufficient: an entailment verifier must also confirm the answer, and none exists yet.

The lexical check cannot distinguish a true claim from a plausible false one built from the
evidence's own vocabulary. `test_grounding_is_a_lexical_check_and_says_so` pins that
limitation, and both 5 Oct runs observed it for real: `source_retention` gave a confidently
wrong answer at 67–88% overlap. Decline detection is a phrase list, English only. On 5 Oct it
missed a French decline and "does not specify", and fired on a correct answer that opened
with a hedge. It was deliberately not tuned on the dev cases.
Real faithfulness checking is T4.1 / P2.1.

### The model

llama.cpp b11146 + Qwen3-4B Q4_K_M, both verified against the 1 Oct SHA-256 pins. They had
not been removed after 1 Oct, as this README used to say; they were still in `/private/tmp`
(volatile). Since 5 Oct the model lives only in `~/Library/Application Support/dbrain/models/`
(copied there by `mvp/prepare-model.sh`, which lands with MVP-phone; the `/private/tmp` copy was deleted, Q42); the archive is still in
`/private/tmp`. `measure.sh` and `experiments/run.sh` read the model from there, or from
`DBRAIN_MODEL`. Start the server with the 1 Oct settings:

```sh
llama-server -m <Qwen3-4B-Q4_K_M.gguf> --host 127.0.0.1 --port 18087 --offline \
  --no-webui -np 1 -c 4096 -t 4 -ngl 99 --no-cache-prompt
```

The adapter uses `/v1/chat/completions` with `/no_think`. It first used the raw
`/completion` endpoint, which applies no chat template: the model never saw an end-of-turn
and repeated itself up to the token cap (found 5 Oct, on the first run with weights).

`LlamaCppAdapter` targets llama.cpp as the **provisional** default because that is the
runtime actually measured. **T9 is not closed by this choice.**

### The allowlist

Both profiles are the same three files: `AGENTS.md`, `data-policy.md`, and **section 0 only**
of `04_Roadmap.md`. That is the corpus the 10 dev cases were written against, and it is
what P2.2 "Done when" 2 specifies since Cowork corrected the brief on 2 Oct. `devcases` is
kept only as a name, because earlier run reports record it.

Until round C, `DEFAULT` used the first brief's roots (`20 Areas/Running`,
`30 Resources/Running`, `30 Resources/Local AI`, `02_Architecture`) and `devcases` included
all of `02_Architecture`. That let evaluation notes into the corpus they measure: the 5 Oct
results note sat inside it, and `client_policy` cited the 1 Oct benchmark. Widening the
corpus is a separate, versioned change with its own cases (M5). Each run report records a
content hash of exactly what was indexed (`corpus.version`).

### Retrieval baseline, 2 Oct (stub model, retrieval only)

Development evidence, not D56 acceptance.

- **Grounded cases: 5/8** retrieved all expected evidence. Misses: `client_policy`,
  `source_retention`, `french_voice` — the French miss already recorded as M5.
- **Abstention cases: 0/2.** Retrieval returns near-miss evidence for both questions the
  corpus cannot answer, so abstention rests entirely on the model. Recorded as `defer`, not
  `pass`. This is the weakest point in the design, and worth saying plainly: the failure
  mode most likely to produce a confident wrong answer is the one least covered by a
  deterministic rule.

The 2 Oct baseline above used the wider corpus; see the P2.2 brief for the rerun on the
three-file corpus.

### Where the reviewer and the lead disagreed

Codex's round B called the root-based `DEFAULT` a blocker; the lead rejected it because the
first brief specified roots. Cowork's 2 Oct correction of the brief made the reviewer right
in substance, and round C found the consequence. `DEFAULT` now follows the corrected brief.

### Not done, deliberately

The 6 reserved acceptance cases are never loaded; the harness refuses any path whose name
contains "reserved". No MCP server, no vector store (D5). Measurements here would be
development evidence, not D56 acceptance, which needs the host (D53).

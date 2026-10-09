# MVP-phone: ask the second brain from the iPhone

Brief: `90 System/briefs/MVP-phone.md` in the vault (David's Q32–Q34). On `main`.
Read-only: the service reads the whole vault minus dot-folders. A note with no
`classification`, an unrecognised one (`restricted`) or frontmatter it cannot verify is
skipped and traced, never indexed; the other notes still answer. A note with a recognisable
`client` label, or conflicting labels, is not skipped: it **pauses** the service (every
question gets 423 "Paused", the path goes to this Mac's log as `POLICY FLAG`, nothing is
answered) until it is removed or relabelled. Answers come from the local model on this Mac; the
service writes only traces and logs, to `~/Library/Application Support/dbrain/logs/`
(outside the vault and outside git; data policy §7b).

Dates: one plain date per question is read (today, tomorrow, the day after tomorrow,
yesterday, a weekday with this/next/on, this or next weekend, this or next week, an explicit
date such as "Tue 6 Oct"; English and French). Any other date-like text makes the reply
"I couldn't read the date…", with no answer, rather than an answer about the wrong day.
That includes a month on its own ("November lunches?"): ask with a day, or without the month.

```
iPhone Shortcut ── Tailscale (WireGuard) ──▶ this Air: dbrain.serve :8765 ──▶ query service ──▶ llama-server 127.0.0.1:18087
```

## One-time setup (David)

1. **Tailscale** on the Air and the iPhone, same account; turn on MagicDNS in the admin
   console. Note the Air's name (e.g. `davids-air`) and tailnet suffix (`….ts.net`).
2. **Energy:** System Settings › Battery › Options: prevent automatic sleeping on power
   adapter when the display is off. Keep the lid open (a closed lid sleeps the Air unless
   an external display is attached).
3. **Token:** in Terminal, `cd ~/Work/dbrain-runtime && ./mvp/make-token.sh`. It goes
   into the Keychain and onto the clipboard for 2 minutes. Paste it straight into the
   Shortcut (step 6). It is never stored in a file.
4. **Model files:** `./mvp/prepare-model.sh` (installs the pinned runtime from its archive, hash-checked, if it
   is not already in `~/Library/Application Support/dbrain/`; checks the hash of the default model, Gemma 4 E4B QAT
   Q4_0, 5.15 GB, chosen by T9 and adopted 8 Oct, which is fetched under `models/MANIFEST.md`, never by this script;
   the old baseline Qwen3-4B is optional: hash-checked if present, copied only if its source file is given).
5. **Start:** double-click `mvp/Ask Brain.command`. To start at login, add that file in
   System Settings › General › Login Items. Keep its Terminal window open. It restarts the
   model or the service if either stops, and keeps the Air awake on power while it runs.
   (A launchd variant would need Full Disk Access for the system Python; see the queue.)
6. **Shortcut "Ask Brain":** Cowork writes the step-by-step. In short:
   - Ask for Input (text; dictation works from the keyboard).
   - Get Contents of URL: `http://<air-name>.<tailnet>.ts.net:8765/ask`, method POST,
     header `Authorization: Bearer <token>`, request body JSON with key `question`.
   - Get Dictionary Value `display` › Show Result (optionally Speak Text).
   - `GET …:8765/health` (no token) reports whether the service and the model are up.
     With the Air asleep nothing answers and the Shortcut hangs: Shortcuts has no client
     timeout, and David accepted this on the Air host (Q36).

## Marking an answer wrong (P2.7 step C5)
Every `/ask` reply carries `answer_id`, a random id (not derived from the question). While
failure capture is on, the service keeps each recent question and its answer **in memory
only, never on disk, for at most one hour** (data policy §7a, v0.8; D123): a tap is accepted
for 59¾ minutes, and a sweep every 15 s drops anything older; a restart drops everything.
`POST /wrong` with the same token and the body `{"id": "<answer_id>"}` (nothing else is
accepted) stores that answer as a failure with the signal `user_wrong`, once; if it was
stored already for another reason, the audit log gets one text-free line adding the signal.
The reply's `display` says what happened (marked, already marked, too late, switched off).
With capture off nothing is held and a tap stores nothing.

**Shortcut change (David edits "Ask Brain" himself; about 2 minutes):**
1. After **Get Contents of URL** (the `/ask` call), add **Get Dictionary Value**: key
   `answer_id`, from *Contents of URL*. Then **Set Variable** `AnswerID` to it.
2. In the existing **Get Dictionary Value** `display`, tap its input and choose the first
   **Contents of URL** (the `/ask` result), not `AnswerID`; keep **Show Result** after it.
3. Add **Choose from Menu**, prompt `Is this answer right?`, two items: `OK`, `Wrong`.
4. Under `OK`: nothing (or **Stop This Shortcut**).
5. Under `Wrong`: **Get Contents of URL** `http://<air-name>.<tailnet>.ts.net:8765/wrong`,
   method POST, header `Authorization: Bearer <token>` (the same token as for `/ask`),
   request body JSON with one key, `id`, of type Text, value the variable `AnswerID`.
6. Still under `Wrong`: **Get Dictionary Value** `display` from that result › **Show Result**.
7. Leave **End Menu** where Shortcuts puts it. Nothing else changes.

## What the service guarantees (tested in `tests/test_serve.py`)
- Listens only on the Tailscale address (or loopback); refuses `0.0.0.0`, `::` and LAN
  addresses, and refuses any peer outside the tailnet.
- Bearer token, constant-time comparison, 401 otherwise; the token is never logged,
  traced or returned.
- One question at a time (429 otherwise), 4 KB body, 500-character question.
- No cloud fallback: if the local model is down the answer is 503 "unavailable".
- Status is `incomplete`, `withheld`, `unclear` (a date it could not read: no answer, asks again), `malformed` or `unavailable`; never `supported`
  before T4.1 (Q21). Citations show their folder; `00 Inbox` ones are marked unreviewed.
- Nothing in the vault changes.

## Traffic and data flow (for T8)
Content travels only between David's devices, encrypted by WireGuard. Tailscale's
coordination server sees device metadata (names, keys, addresses, connection times), not
questions or answers. The hop is plain HTTP inside the WireGuard tunnel; if the Shortcut
refuses `http://`, `tailscale serve` can put HTTPS in front with a tailnet certificate.

## Logs and traces
In `~/Library/Application Support/dbrain/logs/` (`DBRAIN_LOGS` overrides it; refused
inside the vault or a git working tree): service and model logs (method, path, status;
never the question or token; the model server's log was checked with
`experiments/server_log_check.py`) and `traces-YYYY-MM-DD.jsonl` (question as a SHA-256,
paths read, timings, status, `reason_code`). Logs and traces written before 9 Oct are in
the runtime's git-ignored `artifacts/` folder.

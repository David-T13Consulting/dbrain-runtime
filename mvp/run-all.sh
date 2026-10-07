#!/bin/sh
# MVP-phone: run the local model and the /ask service, restarting either if it exits,
# and keep the Air awake while on power (caffeinate -s) for as long as this runs.
# Started by "Ask Brain.command" (a Login Item) or by hand. Ctrl-C stops both.
set -u
set -C   # noclobber; every log is a new file per start, never appended (`>>` follows links)
HERE=$(cd "$(dirname "$0")/.." && pwd)
APP="$HOME/Library/Application Support/dbrain"
SERVER="$APP/llama/llama-b11146/llama-server"
MODEL="$APP/models/Qwen3-4B-Q4_K_M.gguf"
LOGS=$(PYTHONPATH="$HERE" python3 -c "from dbrain import outpath; print(outpath.make_dir('$HERE/artifacts/logs', fresh=False))") || exit 1
[ -x "$SERVER" ] && [ -f "$MODEL" ] || { echo "run mvp/prepare-model.sh first" >&2; exit 1; }

# Model server: loopback only, offline, same settings as P2.2 run B.
( while :; do
    "$SERVER" -m "$MODEL" --host 127.0.0.1 --port 18087 --offline --no-webui -np 1 \
        -c 4096 -t 4 -ngl 99 --no-cache-prompt > "$LOGS/llama-server-$(date +%Y%m%dT%H%M%S).log" 2>&1
    echo "$(date '+%F %T') llama-server exited $?; restarting in 10 s" > "$LOGS/restart-llama-$(date +%Y%m%dT%H%M%S).log"
    sleep 10
  done ) &
LLAMA_LOOP=$!

# /ask service: binds to the Tailscale address only (refuses to start without one).
( cd "$HERE" && while :; do
    env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u GOOGLE_API_KEY -u GEMINI_API_KEY \
        -u MISTRAL_API_KEY -u COHERE_API_KEY -u HF_TOKEN \
        python3 -m dbrain.serve > "$LOGS/ask-$(date +%Y%m%dT%H%M%S).log" 2>&1
    echo "$(date '+%F %T') ask service exited $?; restarting in 30 s" > "$LOGS/restart-ask-$(date +%Y%m%dT%H%M%S).log"
    sleep 30
  done ) &
ASK_LOOP=$!

# Keep-awake on power, held for exactly as long as this script runs.
/usr/bin/caffeinate -s -w $$ &
trap 'pkill -P $LLAMA_LOOP; pkill -P $ASK_LOOP; kill $LLAMA_LOOP $ASK_LOOP 2>/dev/null; exit 0' INT TERM HUP
echo "dbrain MVP running (logs: $LOGS). Keep this window open; Ctrl-C to stop."
wait

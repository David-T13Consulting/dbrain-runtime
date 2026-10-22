#!/bin/bash
# Builds the "Ask Brain 6" voice Shortcut (PL1 step 4): dictate a question, hear the answer
# (the service's `spoken` field, read by on-device speech), see the screen (`display`),
# then OK / Wrong as in "Ask Brain 5".
#
# No token goes into any file, even a temporary one (data policy §2.2). The two
# Authorization headers hold a placeholder; David replaces each with the value copied from
# "Ask Brain 5"'s header on the phone (Q147, 21 Oct). This script never reads the Keychain.
#
#   ./mvp/make-voice-shortcut.sh          build, sign and open for "Add Shortcut"
#   ./mvp/make-voice-shortcut.sh --check  build and sign into a temp folder only (no open)
set -euo pipefail
MODE="${1:-open}"

if command -v tailscale >/dev/null 2>&1; then TS=tailscale
else TS=/Applications/Tailscale.app/Contents/MacOS/Tailscale; fi
HOST=$("$TS" status --json | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
[ -n "$HOST" ] || { echo "Could not read the Tailscale name. Is Tailscale running?"; exit 1; }

WORK=$(mktemp -d); chmod 700 "$WORK"; trap 'rm -rf "$WORK"' EXIT
HOST="$HOST" WORK="$WORK" /usr/bin/python3 - <<'PY'
import os, plistlib, uuid
host, work = os.environ["HOST"], os.environ["WORK"]
PLACEHOLDER = "PASTE THE VALUE FROM ASK BRAIN 5"
u = lambda: str(uuid.uuid4()).upper()
u_dict, u_ask, u_spk, u_dsp, u_id, u_wrong, u_wdsp, g_menu = (u() for _ in range(8))

def text(s):
    return {"Value": {"string": s}, "WFSerializationType": "WFTextTokenString"}
def ref(out_uuid, name):
    return {"OutputUUID": out_uuid, "OutputName": name, "Type": "ActionOutput"}
def var_text(out_uuid, name):
    return {"Value": {"string": "￼", "attachmentsByRange": {"{0, 1}": ref(out_uuid, name)}},
            "WFSerializationType": "WFTextTokenString"}
def var_attach(out_uuid, name):
    return {"Value": ref(out_uuid, name), "WFSerializationType": "WFTextTokenAttachment"}
def fields(items):
    return {"Value": {"WFDictionaryFieldValueItems": [
                {"WFItemType": 0, "WFKey": text(k), "WFValue": v} for k, v in items]},
            "WFSerializationType": "WFDictionaryFieldValue"}
def post(uid, path, body):
    return {"WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
            "WFWorkflowActionParameters": {
                "UUID": uid, "WFURL": "http://%s:8765%s" % (host, path), "WFHTTPMethod": "POST",
                "ShowHeaders": True, "WFHTTPHeaders": fields([("Authorization", text(PLACEHOLDER))]),
                "WFHTTPBodyType": "JSON", "WFJSONValues": fields(body)}}
def value(uid, key, src):
    return {"WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
            "WFWorkflowActionParameters": {"UUID": uid, "WFGetDictionaryValueType": "Value",
                                           "WFDictionaryKey": key,
                                           "WFInput": var_attach(src, "Contents of URL")}}
def menu(mode, **extra):
    p = {"GroupingIdentifier": g_menu, "WFControlFlowMode": mode}
    p.update(extra)
    return {"WFWorkflowActionIdentifier": "is.workflow.actions.choosefrommenu",
            "WFWorkflowActionParameters": p}
def item(title):
    return menu(1, WFMenuItemTitle=title, WFMenuItemAttributedTitle=text(title))

actions = [
    {"WFWorkflowActionIdentifier": "is.workflow.actions.dictatetext",
     "WFWorkflowActionParameters": {"UUID": u_dict, "WFDictateTextStopListening": "After Pause"}},
    post(u_ask, "/ask", [("question", var_text(u_dict, "Dictated Text"))]),
    value(u_spk, "spoken", u_ask),
    {"WFWorkflowActionIdentifier": "is.workflow.actions.speaktext",
     "WFWorkflowActionParameters": {"WFText": var_text(u_spk, "Dictionary Value")}},
    value(u_dsp, "display", u_ask),
    {"WFWorkflowActionIdentifier": "is.workflow.actions.showresult",
     "WFWorkflowActionParameters": {"Text": var_text(u_dsp, "Dictionary Value")}},
    value(u_id, "answer_id", u_ask),
    menu(0, WFMenuPrompt="Is this answer right?", WFMenuItems=["OK", "Wrong"]),
    item("OK"),
    item("Wrong"),
    post(u_wrong, "/wrong", [("id", var_text(u_id, "Dictionary Value"))]),
    value(u_wdsp, "display", u_wrong),
    {"WFWorkflowActionIdentifier": "is.workflow.actions.showresult",
     "WFWorkflowActionParameters": {"Text": var_text(u_wdsp, "Dictionary Value")}},
    menu(2, UUID=u()),
]
wf = {"WFWorkflowActions": actions, "WFWorkflowClientVersion": "2302.0.4",
      "WFWorkflowMinimumClientVersion": 900,
      "WFWorkflowIcon": {"WFWorkflowIconStartColor": 463140863, "WFWorkflowIconGlyphNumber": 59446},
      "WFWorkflowImportQuestions": [], "WFWorkflowTypes": [], "WFWorkflowInputContentItemClasses": [],
      "WFWorkflowHasOutputFallback": False, "WFWorkflowHasShortcutInputVariables": False}
with open(os.path.join(work, "unsigned.shortcut"), "wb") as f:
    plistlib.dump(wf, f, fmt=plistlib.FMT_BINARY)
PY

OUT="$WORK/Ask Brain 6.shortcut"
shortcuts sign --mode anyone --input "$WORK/unsigned.shortcut" --output "$OUT"
echo "Built for http://$HOST:8765 (no token inside)."
if [ "$MODE" = "--check" ]; then
  echo "Check only: signed OK, not opened."
  exit 0
fi
open "$OUT"
echo "Click 'Add Shortcut'. Then on the iPhone, in both Authorization headers, replace"
echo "'PASTE THE VALUE FROM ASK BRAIN 5' with the value copied from Ask Brain 5's header."
printf "Press Enter once added; the temporary file is then deleted. "
read -r _

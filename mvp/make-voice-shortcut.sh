#!/bin/bash
# Builds the "Ask Brain" voice Shortcut (PL1 step 4): dictate a question, hear the answer
# (the service's `spoken` field, read by on-device speech) while the screen (`display`) shows
# as the prompt of an OK / Wrong menu, as in "Ask Brain 5".
#
# No token goes into any file, even a temporary one (data policy §2.2), and this script
# never reads the Keychain. Both Authorization headers take the output of "Brain Key", whose
# one Text action holds a placeholder that David replaced once, on the phone, with the
# value copied from "Ask Brain 5"'s header (Q147, 21 Oct).
#
# Since 21 Oct the token lives in a separate one-action Shortcut, "Brain Key", which David
# fills once and which is never rebuilt; "Ask Brain" calls it for both headers, so a rebuild
# of "Ask Brain" needs no paste (David asked, 21 Oct). Still no token in any file.
#
#   ./mvp/make-voice-shortcut.sh             build, sign and open "Ask Brain" for "Add Shortcut"
#   ./mvp/make-voice-shortcut.sh --key       build, sign and open "Brain Key" (once, ever)
#   ./mvp/make-voice-shortcut.sh --check     build and sign "Ask Brain" into a temp folder only
set -euo pipefail
MODE="${1:-open}"

if command -v tailscale >/dev/null 2>&1; then TS=tailscale
else TS=/Applications/Tailscale.app/Contents/MacOS/Tailscale; fi
HOST=$("$TS" status --json | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
[ -n "$HOST" ] || { echo "Could not read the Tailscale name. Is Tailscale running?"; exit 1; }

WORK=$(mktemp -d); chmod 700 "$WORK"; trap 'rm -rf "$WORK"' EXIT
HOST="$HOST" WORK="$WORK" MODE="$MODE" /usr/bin/python3 - <<'PY'
import os, plistlib, uuid
host, work, mode = os.environ["HOST"], os.environ["WORK"], os.environ["MODE"]
PLACEHOLDER = "PASTE THE WHOLE BEARER VALUE FROM ASK BRAIN 5"
u = lambda: str(uuid.uuid4()).upper()
u_dict, u_ask, u_spk, u_dsp, u_id, u_wrong, u_wdsp, g_menu, u_key, u_txt = (u() for _ in range(10))

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
                "ShowHeaders": True,
                "WFHTTPHeaders": fields([("Authorization", var_text(u_key, "Shortcut Result"))]),
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

KEY_NAME = "Brain Key"
key_actions = [
    {"WFWorkflowActionIdentifier": "is.workflow.actions.gettext",
     "WFWorkflowActionParameters": {"UUID": u_txt, "WFTextActionText": text(PLACEHOLDER)}},
    {"WFWorkflowActionIdentifier": "is.workflow.actions.output",
     "WFWorkflowActionParameters": {"WFOutput": var_text(u_txt, "Text")}},
]
actions = [
    {"WFWorkflowActionIdentifier": "is.workflow.actions.runworkflow",
     "WFWorkflowActionParameters": {"UUID": u_key, "WFWorkflowName": KEY_NAME,
                                    "WFWorkflow": {"workflowName": KEY_NAME, "isSelf": False},
                                    "WFShowWorkflow": False}},
    {"WFWorkflowActionIdentifier": "is.workflow.actions.dictatetext",
     "WFWorkflowActionParameters": {"UUID": u_dict, "WFDictateTextStopListening": "After Pause"}},
    post(u_ask, "/ask", [("question", var_text(u_dict, "Dictated Text"))]),
    value(u_spk, "spoken", u_ask),
    {"WFWorkflowActionIdentifier": "is.workflow.actions.speaktext",
     # Not waiting for speech to finish, so the screen appears while it speaks (David, 21 Oct).
     "WFWorkflowActionParameters": {"WFText": var_text(u_spk, "Dictionary Value"),
                                    "WFSpeakTextWait": False}},
    value(u_dsp, "display", u_ask),
    value(u_id, "answer_id", u_ask),
    # One step (David, 21 Oct): the screen text is the menu's prompt, OK / Wrong below it,
    # instead of Show Result (which waits for Done) then a menu. Menu items as text values
    # (the plain-string form showed no menu on the phone).
    menu(0, WFMenuPrompt=var_text(u_dsp, "Dictionary Value"),
         WFMenuItems=[{"WFItemType": 0, "WFValue": text("OK")},
                      {"WFItemType": 0, "WFValue": text("Wrong")}]),
    item("OK"),
    item("Wrong"),
    post(u_wrong, "/wrong", [("id", var_text(u_id, "Dictionary Value"))]),
    value(u_wdsp, "display", u_wrong),
    {"WFWorkflowActionIdentifier": "is.workflow.actions.showresult",
     "WFWorkflowActionParameters": {"Text": var_text(u_wdsp, "Dictionary Value")}},
    menu(2, UUID=u()),
]
wf = {"WFWorkflowActions": key_actions if mode == "--key" else actions, "WFWorkflowClientVersion": "2302.0.4",
      "WFWorkflowMinimumClientVersion": 900,
      "WFWorkflowIcon": {"WFWorkflowIconStartColor": 463140863, "WFWorkflowIconGlyphNumber": 59446},
      "WFWorkflowImportQuestions": [], "WFWorkflowTypes": [], "WFWorkflowInputContentItemClasses": [],
      "WFWorkflowHasOutputFallback": False, "WFWorkflowHasShortcutInputVariables": False}
with open(os.path.join(work, "unsigned.shortcut"), "wb") as f:
    plistlib.dump(wf, f, fmt=plistlib.FMT_BINARY)
PY

if [ "$MODE" = "--key" ]; then NAME="Brain Key"; else NAME="Ask Brain"; fi
OUT="$WORK/$NAME.shortcut"
shortcuts sign --mode anyone --input "$WORK/unsigned.shortcut" --output "$OUT"
echo "Built for http://$HOST:8765 (no token inside)."
if [ "$MODE" = "--check" ]; then
  echo "Check only: signed OK, not opened."
  exit 0
fi
open "$OUT"
if [ "$MODE" = "--key" ]; then
  echo "Click 'Add Shortcut'. Then on the iPhone, in Brain Key's one Text action, replace the"
  echo "placeholder with the whole value copied from Ask Brain 5's header (Bearer and the token)."
else
  echo "Click 'Add Shortcut' (replace the old Ask Brain if asked). No paste needed: it calls Brain Key."
fi
printf "Press Enter once added; the temporary file is then deleted. "
read -r _

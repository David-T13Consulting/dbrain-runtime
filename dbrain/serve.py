"""MVP-phone: `POST /ask` over Tailscale, so David can ask from his iPhone (brief MVP-phone).

    python3 -m dbrain.serve [--host 100.x.y.z] [--port 8765]

It is a thin, read-only shell around `answer.ask()` with the whole-vault profile. What
it adds is the boundary:

  * **Bind:** only a Tailscale address (100.64.0.0/10 or fd7a:115c:a1e0::/48) or loopback.
    `0.0.0.0`, `::` and LAN addresses are refused at start-up; without `--host` the
    address comes from the Tailscale CLI, and the service does not start if there is none.
  * **Peers:** every request's source address is checked against the same ranges.
  * **Auth:** `Authorization: Bearer <token>`, compared in constant time. The token is
    read from the macOS Keychain at start-up and kept only in memory: never in a file,
    an argument, an environment variable or a log (data policy §2.2).
  * **Load:** one question at a time (a second gets 429), 4 KB body cap, 500-character
    question cap, and one end-to-end deadline: 120 s after the request arrived the phone
    gets 504, whatever stage is still running (step 5). A late model call keeps the
    one-at-a-time lock until it returns, so it never overlaps the next question.
  * **Nothing executes:** the question is text for retrieval and the prompt; vault
    content is evidence, never instructions; nothing is written to the vault. Traces go
    to the git-ignored `artifacts/` folder through the write guard.

`GET /health` answers without auth and without any vault content, so the Shortcut can
tell "the Air is offline or asleep" from "the brain has no answer".
"""
import argparse
import hmac
import ipaddress
import json
import os
import re
import shutil
import subprocess
import socket
import sys
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import adapter as adapter_mod
from . import answer, config, index, outpath, trace
from . import failures as failures_mod

# Payload key carrying the verdict's reason code from Service.ask to the capture point;
# never sent to the phone.
REASON_KEY = "_reason_code"

TAILNET_NETS = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))
KEYCHAIN_SERVICE = "dbrain-mvp-ask"
MAX_BODY = 4096
MAX_QUESTION = 500
DEFAULT_PORT = 8765
REQUEST_TIMEOUT = 120
TAILSCALE_CLIS = ("tailscale", "/Applications/Tailscale.app/Contents/MacOS/Tailscale")


class BindRefused(Exception):
    """An address this service must not listen on."""


def allowed_address(addr):
    """Tailnet or loopback. Everything else, including the LAN and 0.0.0.0, is refused."""
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return False
    if ip.is_loopback:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in net for net in TAILNET_NETS)


def check_bind(host):
    if host in ("", "0.0.0.0", "::", "*") or not allowed_address(host):
        raise BindRefused(
            "refusing to listen on %r: only a Tailscale address or loopback is allowed" % host
        )
    return host


def tailscale_ipv4():
    """The Air's Tailscale address, from the Tailscale CLI, or None."""
    for cli in TAILSCALE_CLIS:
        path = shutil.which(cli) or (cli if os.path.isabs(cli) and os.path.exists(cli) else None)
        if not path:
            continue
        try:
            out = subprocess.run([path, "ip", "-4"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            continue
        for line in out.stdout.split():
            if allowed_address(line) and not ipaddress.ip_address(line).is_loopback:
                return line
    return None


def keychain_token(service=KEYCHAIN_SERVICE):
    """Read the bearer token from the login Keychain. Never logged, never written."""
    try:
        out = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit("cannot read the token from the Keychain: %s" % exc)
    token = out.stdout.strip()
    if out.returncode != 0 or len(token) < 32:
        raise SystemExit(
            "no usable token in the Keychain item %r (run mvp/make-token.sh first)" % service
        )
    return token


# Fact-first wording for the phone (David, MVP acceptance, 5 Oct): "Yes, there's no
# school on 2 Nov" put a yes/no lead-in in front of the fact and contradicted it. P2.2's
# PROMPT is left untouched, so its pinned prompt version still describes run B.
MVP_PROMPT = answer.PROMPT.replace(
    "- Do not guess, and do not use knowledge from outside the evidence.\n",
    "- Do not guess, and do not use knowledge from outside the evidence.\n"
    "- Start with the fact itself, as a full sentence (for example \"There is no school "
    "on Mon 2 Nov.\"). Do not begin with Yes or No.\n"
    "- Keep it short: one to three sentences.\n",
)
assert MVP_PROMPT != answer.PROMPT

# Date awareness (David, Q46 priority 2, 5 Oct): "What's on the menu tomorrow?" was
# answered about 2 Nov, because nothing knew what day it was. Relative dates are resolved
# against the Air's date in Brussels *before retrieval*, written into the question in the
# forms the notes use ("Tue 06" under a month heading, "Tue 6 Oct"), and the model is told
# today's date and to answer for that date or say it found nothing for it.
TIMEZONE = "Europe/Brussels"
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August",
                "September", "October", "November", "December")
_WEEKDAY_WORDS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5,
    "sunday": 6, "lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3, "vendredi": 4,
    "samedi": 5, "dimanche": 6,
}
_WD = "|".join(_WEEKDAY_WORDS)
# Dates are read in three steps (Codex consultation, session 20, after review rounds 5-7
# each found a new form): (1) recognise components separately (explicit dates, standalone
# phrases, weekdays, week references), keeping their spans; (2) combine a weekday with an
# adjacent week reference, in either order, through a small connector grammar; (3) resolve.
# A date that cannot be read confidently is "unclear": the service asks again rather than
# guess. Explicit dates win over anything they overlap.
_STANDALONE = re.compile(
    r"\b(?:day after tomorrow|apr[eè]s-demain|day before yesterday|avant-hier|tomorrow|demain|"
    r"today|aujourd'hui|yesterday|hier|"
    r"(?:this|ce|next|coming|le) (?:weekend|week-end)(?: prochain)?)\b", re.I)
_WEEK = re.compile(r"\b(?:(?:this|next) week(?:'s)?|cette semaine|(?:la )?semaine prochaine)(?![\w-])", re.I)
_WEEKDAY = re.compile(r"\b(?:(?P<pre>on|this|coming|next|ce|le) )?(?P<wd>%s)(?: (?P<post>prochain))?\b"
                      % "|".join(_WEEKDAY_WORDS), re.I)
# What may sit between a weekday and its week: spaces, a comma, and one of: of, in, during,
# de, de la. Nothing else, so "Thursday and next week" stays two targets.
_CONNECT = re.compile(r"^[ ,]*(?:(?:of|in|during|de la|de) )?[ ,]*$", re.I)

# Explicit dates: "Mon 2 Nov", "2 November 2026", "Monday, October 12", "le 2 novembre".
# Left as written in the question, but listed in `found` like relative ones.
_MONTH_WORDS = {}
for _n, _names in enumerate((
        ("jan", "january", "janvier", "janv"), ("feb", "february", "fevrier", "février", "févr", "fevr"),
        ("mar", "march", "mars"), ("apr", "april", "avril", "avr"), ("may", "mai"),
        ("jun", "june", "juin"), ("jul", "july", "juillet", "juil"),
        ("aug", "august", "août", "aout"), ("sep", "sept", "september", "septembre"),
        ("oct", "october", "octobre"), ("nov", "november", "novembre"),
        ("dec", "december", "décembre", "decembre", "déc")), 1):
    for _w in _names:
        _MONTH_WORDS[_w] = _n
_MON = "|".join(sorted(_MONTH_WORDS, key=len, reverse=True))
_WDA = "|".join(sorted(list(_WEEKDAY_WORDS) + ["mon", "tue", "tues", "wed", "thu", "thur",
                                                "thurs", "fri", "sat", "sun"],
                       key=len, reverse=True))
_YEAR_TAIL = r"(?:,?\s*\(?(?P<y%d>\d{4})\)?(?!\d))?"
_EXPLICIT = re.compile(
    r"\b(?:(?P<w1>%s)\.?,?\s+)?(?:the\s+|le\s+)?(?P<d1>\d{1,2})(?:st|nd|rd|th|er)?\s+(?:of\s+)?"
    r"(?P<m1>%s)\b\.?" % (_WDA, _MON) + _YEAR_TAIL % 1 +
    r"|\b(?:(?P<w2>%s)\.?,?\s+)?(?P<m2>%s)\b\.?\s+(?:the\s+)?(?P<d2>\d{1,2})(?:st|nd|rd|th)?(?!\d)"
    % (_WDA, _MON) + _YEAR_TAIL % 2, re.I)


def _explicit_date(m, today):
    """The date an explicit match names, or None if it is not a real date or its weekday
    disagrees ("lundi 6 octobre" in 2026). Without a year: this year, or next year if that
    would be more than about six months ago."""
    day = int(m.group("d1") or m.group("d2"))
    month = _MONTH_WORDS[(m.group("m1") or m.group("m2")).lower()]
    year = m.group("y1") or m.group("y2")
    try:
        if year:
            d = date(int(year), month, day)
        else:
            d = date(today.year, month, day)
            if d < today - timedelta(183):
                d = date(today.year + 1, month, day)
    except ValueError:
        return None
    wd = (m.group("w1") or m.group("w2") or "").lower()
    if wd:
        named = _WEEKDAY_WORDS[wd] if wd in _WEEKDAY_WORDS else [x.lower() for x in _DAYS].index(wd[:3])
        if named != d.weekday():
            return None
    return d


def today_local():
    """Today in Brussels. No silent fallback to the host's zone: without a time-zone
    database this raises, and main() refuses to start."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(TIMEZONE)).date()


def fmt_day(d):
    """`Tue 6 Oct 2026`, the form the answer states."""
    return "%s %d %s %d" % (_DAYS[d.weekday()], d.day, _MONTHS[d.month - 1], d.year)


def days_label(days):
    """One day, a run of consecutive days ("Mon 5 Oct 2026 to Sun 11 Oct 2026"), or a list
    of separate days: separate dates are never shown as one range."""
    days = sorted(set(days))
    if len(days) == 1:
        return fmt_day(days[0])
    if all((b - a).days == 1 for a, b in zip(days, days[1:])):
        return "%s to %s" % (fmt_day(days[0]), fmt_day(days[-1]))
    return ", ".join(fmt_day(d) for d in days)


def _search_forms(d):
    """The ways a note may write this date, for lexical retrieval."""
    return "%s %d %s %d; %s %02d %s" % (_DAYS[d.weekday()], d.day, _MONTHS[d.month - 1], d.year,
                                        _DAYS[d.weekday()], d.day, _MONTH_NAMES[d.month - 1])


def _standalone_days(p, today):
    p = p.lower().replace("è", "e")
    if p in ("today", "aujourd'hui"):
        return [today]
    if p in ("tomorrow", "demain"):
        return [today + timedelta(1)]
    if p in ("day after tomorrow", "apres-demain"):
        return [today + timedelta(2)]
    if p in ("yesterday", "hier"):
        return [today - timedelta(1)]
    if p in ("day before yesterday", "avant-hier"):
        return [today - timedelta(2)]
    # a weekend: the coming Sat-Sun (this one if we are in it); next / prochain: a week on
    sat = today - timedelta(today.weekday()) + timedelta(5)
    if p.startswith("next") or "prochain" in p:
        sat += timedelta(7)
    return [sat, sat + timedelta(1)]


def _week_offset(text):
    """0 for this week, 7 for next week."""
    t = text.lower()
    return 7 if ("next" in t or "prochaine" in t) else 0


def _weekday_days(m, today):
    wd = _WEEKDAY_WORDS[m.group("wd").lower()]
    ahead = (wd - today.weekday()) % 7
    if (m.group("pre") or "").lower() == "next" or m.group("post"):
        ahead = ahead or 7  # "next Monday" on a Monday is a week away
    return [today + timedelta(ahead)]


def resolve_dates(question, today):
    """(question for retrieval and the model, [(phrase, [dates])]). Each relative phrase is
    kept and followed by its absolute date(s), so both retrieval and the model see them.
    Explicit dates are left as written but listed too. An entry with no dates is unclear
    (an impossible date, or a weekday whose week contradicts it): the caller asks again."""
    # Curly apostrophes read like straight ones. Same length, so spans stay aligned.
    question = question.replace("’", "'").replace("‘", "'")
    taken = []  # (start, end, phrase, days, rewrite)

    def free(s, e):
        return not any(a < e and s < b for a, b, _, _, _ in taken)

    for m in _EXPLICIT.finditer(question):  # reserved even when invalid: "Monday 31 Feb"
        d = _explicit_date(m, today)
        taken.append((m.start(), m.end(), m.group(0), [d] if d else [], False))
    for m in _STANDALONE.finditer(question):
        if free(m.start(), m.end()):
            taken.append((m.start(), m.end(), m.group(0), _standalone_days(m.group(0), today), True))
    weeks = [m for m in _WEEK.finditer(question) if free(m.start(), m.end())]
    wdays = [m for m in _WEEKDAY.finditer(question) if free(m.start(), m.end())]
    used_w = set()
    monday = today - timedelta(today.weekday())
    for wm in wdays:
        partner = None
        for k in weeks:
            if id(k) in used_w:
                continue
            gap = question[wm.end():k.start()] if wm.end() <= k.start() else question[k.end():wm.start()]
            if (wm.end() <= k.start() or k.end() <= wm.start()) and _CONNECT.match(gap):
                partner = k
                break
        if partner is None:
            stacked = (wm.group("pre") or "").lower() in ("this", "next", "coming") and wm.group("post")
            taken.append((wm.start(), wm.end(), wm.group(0),
                          [] if stacked else _weekday_days(wm, today), True))  # "next jeudi prochain"
            continue
        used_w.add(id(partner))
        s, e = min(wm.start(), partner.start()), max(wm.end(), partner.end())
        pre = (wm.group("pre") or "").lower()
        offset = _week_offset(partner.group(0))
        if pre == "next" or (wm.group("post") and offset == 0):
            days = []  # "next Thursday this week" contradicts; "next Thursday next week" stacks
        else:
            days = [monday + timedelta(offset + _WEEKDAY_WORDS[wm.group("wd").lower()])]
        taken.append((s, e, question[s:e], days, True))
    for k in weeks:
        if id(k) not in used_w:
            off = _week_offset(k.group(0))
            taken.append((k.start(), k.end(), k.group(0),
                          [monday + timedelta(off + i) for i in range(7)], True))
    taken.sort(key=lambda t: (t[0], t[1]))
    # Conservative by design (session 20, after review rounds 5-8 each found new phrasings):
    # the service resolves one plain date phrase per question and asks again otherwise.
    # Fail-closed (session 31, after round 9): any date-like text left over once the
    # supported phrases are consumed also makes the question unclear, so a form the
    # parser does not know is never passed on as if there were no date.
    hits = _leftover(question, taken)
    if hits or (taken and (len(taken) > 1 or _qualified(question, taken)
                           or _stray_year(question, taken))):
        spans = [(s, e) for s, e, _, _, _ in taken] + hits
        start, end = min(s for s, _ in spans), max(e for _, e in spans)
        return question, [(question[start:end].strip(" ,"), [])]
    out, found, pos = [], [], 0
    for start, end, phrase, days, rewrite in taken:
        found.append((phrase, days))
        out.append(question[pos:end])
        pos = end
        if not rewrite or not days:
            continue
        if len(days) > 2:  # a week: the span, then each day as the notes write it ("Tue 6 Oct")
            each = ", ".join(fmt_day(d).rsplit(" ", 1)[0] for d in days)
            out.append(" (%s: %s)" % (days_label(days), each))
        else:
            out.append(" (%s)" % "; ".join(_search_forms(d) for d in days))
    out.append(question[pos:])
    return "".join(out), found


# Words that change what a date phrase means when they sit right next to it ("the day
# before Monday", "last Friday", "Monday after next", "avant-hier"): the phrase alone would
# name the wrong day, so the question is unclear.
_MODIFIERS = {"before", "after", "last", "previous", "past", "following", "since", "until",
              "till", "from", "avant", "apres", "après", "dernier", "derniere", "dernière",
              "precedent", "précédent", "suivant", "suivante", "depuis", "jusqu'a", "jusqu'à",
              "passe", "passé", "ago", "of", "by", "between", "except", "but", "coming"}


def _qualified(question, taken):
    for start, end, _, _, _ in taken:
        before = question[:start]
        if before[-1:] in ("-", "–", "—", "'") or (before[-1:].isalnum()):
            return True
        after = question[end:]
        if after[:1] in ("-", "–", "—") or after[:1].isalnum():
            return True
        prev = re.search(r"([\w'-]+)[ ,]*$", before)
        nxt = re.match(r"[ ,]*([\w'-]+)", after)
        words = [w.group(1).lower() for w in (prev, nxt) if w]
        if any(w in _MODIFIERS for w in words):
            return True
    return False


# Date-like text outside the supported phrases (session 31, fail-closed, after review
# rounds 9-10 and a Codex consultation). Not a list of what is understood: a description of
# what *looks like* a date, checked over the whole question once the supported phrases are
# blanked out. Anything found makes the question unclear, a bare month too ("menus for
# May", review round 11). A plural weekday ("Thursdays") names a habit, not a date, and
# passes.
_FULL_WEEKDAYS = tuple(_WEEKDAY_WORDS)
_PLURAL_WEEKDAYS = {w + "s" for w in _WEEKDAY_WORDS}
# Every unconsumed month name or weekday abbreviation is date-like wherever it sits, even
# where it is also a word ("May I", "mon menu", "sept personnes"): positional exceptions were
# bypassed three review rounds running (10-12; Codex consultation, session 31).
_WD_ABBR = {"mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri", "sat", "sun"}
_ALWAYS = {"days", "jours", "weeks", "semaines", "weekend", "weekends", "week-end", "week-ends",
           "months", "fortnight", "fortnights", "quinzaine", "tonight", "tonite", "overmorrow"}
_SINGULAR = {"day", "jour", "journée", "journee", "week", "semaine", "month", "mois", "year",
             "years", "année", "annee", "années", "annees"}
_DAYPART = {"morning", "afternoon", "evening", "night", "matin", "après-midi", "apres-midi",
            "soir", "soirée", "soiree", "nuit", "midi"}
_THIS = {"this", "ce", "cet", "cette"}
_ARTICLE = _THIS | {"the", "le", "la"}
_STRONG = {"next", "last", "previous", "past", "following", "prochain", "prochaine",
           "prochains", "prochaines", "dernier", "dernière", "derniere", "derniers",
           "précédent", "précédente", "precedent", "precedente", "suivant", "suivante",
           "every", "each", "chaque", "other", "ago", "before", "after", "since", "until",
           "till", "avant", "après", "apres", "depuis", "jusqu'à", "jusqu'a", "jusqu'au",
           "between", "entre", "through", "thru", "except", "sauf", "not", "pas", "early",
           "late", "mid", "mi", "début", "debut", "fin"}
_NUMBER_WORDS = {"a", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
                 "ten", "eleven", "twelve", "fifteen", "twenty", "thirty", "few", "couple",
                 "several", "un", "une", "deux", "trois", "quatre", "cinq", "sept", "huit",
                 "neuf", "dix", "onze", "douze", "quinze", "vingt", "trente", "quelques",
                 "plusieurs"}
_ORDINAL = re.compile(r"^(?:fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|"
                      r"\w+teenth|twentieth|thirtieth|(?:twenty|thirty)-\w+|\w+i[eè]me)$")
_LOW_ORDINAL = {"first", "second", "third", "premier", "première", "premiere"}
# A day name or "tomorrow" mistyped ("tomorow", "wenesday") still names a date.
_FUZZY = ("tomorrow", "yesterday", "tonight", "weekend", "aujourd'hui", "demain", "semaine",
          "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
          "mercredi", "vendredi", "samedi", "dimanche")
_NOT_TYPOS = {"domain", "sundae", "sundaes"}
# Time-direction words: with no date read, they may point at one ("the next weekday").
_DIRECTION = {"next", "last", "previous", "past", "following", "upcoming", "coming", "prochain",
              "prochaine", "prochains", "prochaines", "dernier", "dernière", "derniere",
              "derniers", "dernières", "précédent", "précédente", "precedent", "precedente",
              "suivant", "suivante", "ago", "avant-veille", "veille", "lendemain", "surlendemain",
              "eve"}
# More words that may point at a date when none was read (red-team pass before review
# round 14): texting shorthand, French and Dutch day words, short week words, school periods,
# "now" and hour offsets.
_POINTERS = {"tmrw", "tmw", "tmr", "tmrrw", "2moro", "2morrow", "tdy", "2day", "tday", "auj",
             "ajd", "dem1", "dmain", "lun", "mar", "mer", "jeu", "ven", "sam", "dim", "weds",
             "wk", "wks", "sem", "w-e", "wkend", "break", "breaks", "term", "terms",
             "half-term", "trimestre", "semester", "semestre", "rentrée", "rentree",
             "vacances", "congé", "conge", "congés", "conges", "holiday", "holidays",
             "détente", "detente", "strike", "grève", "greve", "saint", "saints", "st", "souls",
             "fête", "fete", "fêtes", "fetes", "nicolas", "sinterklaas", "valentin", "valentine",
             "epiphany", "épiphanie", "epiphanie", "chandeleur", "thanksgiving", "diwali", "eid",
             "hanukkah", "hallowe'en", "now", "maintenant", "currently", "actuellement",
             "moment", "hour", "hours", "heure", "heures", "lunchtime", "morgen", "vandaag",
             "gisteren", "overmorgen", "eergisteren", "volgende", "vorige", "komende",
             "maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag",
             "januari", "februari", "maart", "mei", "juni", "juli", "augustus", "oktober"}
# Seasons (run B review): a period that may point at a date when none was read.
_SEASONS = {"winter", "summer", "spring", "autumn", "fall", "season", "seasons", "hiver", "été",
            "ete", "printemps", "automne", "saison", "saisons", "winters", "summers"}
# "a été", "ont été": the verb, never the season.
_AVOIR = {"a", "ai", "as", "avons", "avez", "ont", "avait", "avaient", "aura", "auront",
          "aurait", "auraient", "eu", "ayant", "n'a", "n'ont", "n'avait", "y"}
_POLITE = {"please", "pls", "plz", "thanks", "thank", "thx", "merci", "svp", "stp"}
_HOLIDAYS = {"christmas", "xmas", "noël", "noel", "easter", "pâques", "paques", "halloween",
             "toussaint", "carnaval", "carnival", "réveillon", "reveillon", "nouvel", "new-year",
             "nye", "pentecost", "pentecôte", "ascension", "assomption", "armistice"}
# Numbers that cannot be a date: a time of day, or a count with its unit or noun.
_TIME_OR_UNIT = re.compile(r"^\d{1,2}(?::\d{2}|h\d{0,2}|am|pm)$|^\d+(?:k|km|m|min|kg|g|l|€|%|x)$")
_COUNT = re.compile(r"\d{1,3}(?:[.,]\d+)?")
_COUNT_NOUNS = {"people", "persons", "person", "personnes", "personne", "kids", "children",
                "enfants", "adults", "adultes", "km", "minutes", "min", "hours", "heures",
                "euros", "eur", "times", "fois", "places", "seats", "portions", "%"}
# Tokens: a code (letters then digits: "P2.4", "D53", "Oct6"), a number not glued to a
# letter (after ":", "/", "." or "-" it still counts: "menu:2026-10-06"), or a word.
_TOKEN = re.compile(r"[^\W\d_]+\d[\w.]*|(?<![^\W_])\d+(?:[.,:/h-]\d+)*[^\W_]*|"
                    r"[^\W\d_]+(?:['-][^\W\d_]+)*", re.U)


def _near(a, b, limit):
    """Edit distance of a and b is at most limit (small words, small limits)."""
    if abs(len(a) - len(b)) > limit:
        return False
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    if row[-1] <= limit:
        return True
    # one swap of neighbouring letters counts as one edit ("demian")
    return any(a[:k] + a[k + 1] + a[k] + a[k + 2:] == b for k in range(len(a) - 1)) and limit >= 1


def _leftover(question, taken):
    """[(start, end)] of date-like text outside every consumed phrase. Empty means the
    question holds no date the parser has not fully read."""
    masked = list(question)
    for s, e, _, _, _ in taken:
        masked[s:e] = " " * (e - s)
    masked = "".join(masked)
    toks = [(m.start(), m.end(), m.group(0).lower()) for m in _TOKEN.finditer(masked)]
    hits, strong, undated, months = [], [], [], []

    def touches_span(s, e):  # only spaces between this token and a consumed phrase
        return any((b <= s and not question[b:s].strip()) or (e <= a and not question[e:a].strip())
                   for a, b, _, _, _ in taken)

    def date_word(w, prev, prev2, nxt):
        if w in _PLURAL_WEEKDAYS or w in _NOT_TYPOS:
            return False
        if w.startswith(_FULL_WEEKDAYS) or w in _WD_ABBR or w in _ALWAYS or _ORDINAL.match(w):
            return True
        if w in _MONTH_WORDS:
            return True
        if w in _SINGULAR:  # "next week" is read; "a week", "the week after", "Monday week" are not
            return (prev in _STRONG or prev in _NUMBER_WORDS or prev in _ARTICLE
                    or prev == "coming")
        if w in _LOW_ORDINAL and prev in ("twenty", "thirty", "vingt", "trente"):
            return True  # "the twenty first"
        if w in _LOW_ORDINAL:  # "on the first", "the second of November"; not "the second brain"
            return prev in ("the", "le", "la") and (not nxt or nxt in ("of", "de", "d")
                                                    or nxt in _MONTH_WORDS)
        if w in ("an", "ans"):  # "dans un an", "deux ans"
            return prev in _NUMBER_WORDS
        if w in _NUMBER_WORDS and w != "a":
            return prev in ("le", "du", "au")  # "le trois"
        if w in _DAYPART:
            return prev in _THIS
        return len(w) >= 5 and any(_near(w, f, 1 if len(f) <= 7 else 2) for f in _FUZZY)

    for i, (s, e, w) in enumerate(toks):
        prev = toks[i - 1][2] if i else ""
        prev2 = toks[i - 2][2] if i > 1 else ""
        nxt = toks[i + 1][2] if i + 1 < len(toks) else ""
        bare = w[:-2] if w.endswith("'s") else w
        if w[0].isdigit():
            count = (_COUNT.fullmatch(w) and nxt in _COUNT_NOUNS
                     and not masked[e:toks[i + 1][0]].strip())
            if not (_TIME_OR_UNIT.match(w) or count):
                hits.append((s, e))
            continue
        if re.match(r"[^\W\d_]+\d", w):  # a code: a date only if it starts with a day or month
            alpha = re.match(r"[^\W\d_]+", w).group(0)
            if (alpha in _MONTH_WORDS or alpha in _WD_ABBR or alpha in _WEEKDAY_WORDS
                    or alpha in ("w", "wk", "kw", "sem", "week", "semaine")):  # "W42"
                hits.append((s, e))
            continue
        parts = [bare] + (bare.split("-") if "-" in bare else [])  # "mid-October", "mi-octobre"
        if any(date_word(p, prev, prev2, nxt) for p in parts):
            hits.append((s, e))
        elif "-" in bare and any(p in _STRONG for p in parts) and any(p in _MONTH_WORDS for p in parts):
            hits.append((s, e))
        elif bare in _SINGULAR and touches_span(s, e):
            hits.append((s, e))
        else:
            if (bare in _SEASONS and not (bare in ("été", "ete") and prev in _AVOIR)
                    or bare in _HOLIDAYS or bare in _SINGULAR or bare in _DAYPART or bare in _POINTERS
                    or any(p in _POINTERS for p in parts) or bare.startswith(("maandag", "dinsdag",
                    "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"))
                    or any(p in _DIRECTION for p in parts)
                    or (re.search(r"(?:day|jour|journ[ée]e)s?$", bare)
                        and bare not in _PLURAL_WEEKDAYS)):
                undated.append((s, e))  # date-like only when nothing was read: see below
            if bare in _LOW_ORDINAL and prev in ("the", "le", "la"):
                months.append((s, e))  # "the third": a conflict only next to a date read
                # With no date read, "the first" may be a day of the month whatever follows
                # ("on the first, please?" round 14; "on the first if possible?" run B), so it
                # asks again; "the first race?" over-asks, the accepted cost. Not "second brain".
                if nxt != "brain":
                    undated.append((s, e))
            if any(p in _STRONG for p in parts) or bare.endswith("n't"):
                strong.append((s, e))
    # When a phrase was read, the answer always carries "(Asked about ...)", so an answer
    # about another date is visible. When none was read there is no label, so any word that
    # may point at a date makes the question unclear (review round 13: "the next weekday",
    # "New Year's Day"). Over-asking ("Which week is the break?") is the accepted cost.
    if not taken:
        hits += undated
    else:
        hits += months  # "yesterday the third"
    if hits or taken:
        hits += strong
    return hits


def _stray_year(question, taken):
    """A four-digit number outside every recognised date: possibly a year the parser did
    not attach ("October 6,2027" before the fix), so the date is not certain."""
    for m in re.finditer(r"(?<!\d)\d{4}(?!\d)", question):
        if not any(s <= m.start() and m.end() <= e for s, e, _, _, _ in taken):
            return True
    return False


def dated_prompt(today, found):
    """MVP_PROMPT with today's date and, when the question named a date, the rule to
    answer for that date only."""
    extra = "- Today is %s (%s).\n" % (fmt_day(today), TIMEZONE)
    days = [d for _, ds in found for d in ds]
    if days:
        named = days_label(days)
        extra += ("- The question is about %s. Begin the answer with that date (for example "
                  "\"%s: ...\"). If the evidence says nothing for that date, say \"Nothing found "
                  "for %s.\" and do not answer about another date.\n" % (named, named, named))
    return MVP_PROMPT.replace("- Keep it short", extra.replace("%", "%%") + "- Keep it short", 1)


CITE_ID = re.compile(r"\[E(\d+)\]")
_WORD = re.compile(r"[\w:'’-]+", re.U)
_STOP = {"the", "and", "for", "with", "that", "this", "from", "are", "was", "is", "on", "in",
         "of", "to", "a", "an", "at", "by", "it", "be", "or", "as", "there", "evidence"}


def citation(cite):
    """`path:start-end` -> a dict the Shortcut can show, with the folder made visible."""
    m = re.match(r"^(?P<path>.+):(?P<start>\d+)-(?P<end>\d+)$", cite)
    path, lines = (m["path"], "%s-%s" % (m["start"], m["end"])) if m else (cite, "")
    folder = path.split("/", 1)[0] if "/" in path else "(vault root)"
    return {
        "path": path,
        "lines": lines,
        "folder": folder,
        # Inbox notes are raw captures nobody has reviewed: say so next to the answer.
        "unreviewed": folder == "00 Inbox",
    }


def _words(text):
    return {w.lower().strip("'’-") for w in _WORD.findall(text)} - _STOP


def quote_lines(chunk_text, answer_text, k=2, width=160):
    """The note's own line(s) that best match the answer, verbatim, so a contradiction
    between the answer and its source is visible on the phone (acceptance fix 2)."""
    target = _words(CITE_ID.sub("", answer_text))
    scored = []
    for i, line in enumerate(chunk_text.splitlines()):
        bare = line.strip()
        if not bare or bare.startswith("#") or set(bare) <= set("|-: "):
            continue
        score = len(_words(bare) & target)
        if score:
            scored.append((score, i, bare))
    best = sorted(sorted(scored, reverse=True)[:k], key=lambda t: t[1])
    out = []
    for _, _, bare in best:
        out.append(bare if len(bare) <= width else bare[: width - 1] + "…")
    return out


def plain_status(status, reason, citations):
    """One line David can read on the phone, instead of the engineering reason."""
    reason = reason or ""
    if status == "supported":
        line = "Supported by the cited note"
    elif status == "incomplete":
        if "entailment unverified" in reason:
            line = "Not verified (no automatic fact-check yet)"
        elif "without citing" in reason:
            line = "Not verified: no source cited"
        elif "never offered" in reason:
            line = "Not verified: it cited a source it wasn't given"
        elif "of the answer's wording" in reason:
            line = "Not verified: the answer strays from its source"
        else:
            line = "Not verified"
    elif status == "withheld":
        line = ("No answer: nothing in the notes matches" if "no evidence" in reason
                else "No answer: the notes don't say")
    elif status == "malformed":
        line = "No usable answer: the model's reply was cut off or empty. Try rephrasing"
    elif status == "unclear":
        line = "No answer: the date wasn't clear"
    elif status == "unavailable":
        line = "The local model isn't answering on the Air. Try again in a minute"
    else:
        line = status
    if any(c.get("unreviewed") for c in citations):
        line += " · from an unreviewed Inbox note"
    return line


def display_text(payload):
    """One plain-text block for the Shortcut's Show Result / Speak Text."""
    lines = [payload.get("answer") or "(no answer)"]
    cites = payload.get("citations", [])
    if cites:
        lines.append("")
        lines.append("From your notes:")
        for n, c in enumerate(cites, 1):
            tag = " (unreviewed Inbox note)" if c["unreviewed"] else ""
            lines.append("[%d] %s, lines %s%s" % (n, c["path"], c["lines"], tag))
            for q in c.get("quote", []):
                lines.append("    “%s”" % q)
    lines.append("")
    lines.append(payload.get("status_line") or payload["status"])
    lines.append("%.1f s" % payload["latency_s"])
    return "\n".join(lines)


def error_display(code):
    """Every non-200 response carries a readable `display` (acceptance fix 4). Generic on
    purpose: it never says *why* a token failed or what the server saw."""
    return {
        400: "The question was empty, too long (500 characters max) or not sent as text.",
        401: "Not authorised: the token in this Shortcut doesn't match. "
             "Re-copy it from the Keychain on the Air (mvp/make-token.sh).",
        403: "Refused: this device isn't on David's Tailscale network.",
        404: "Wrong address: the Shortcut should call /ask.",
        411: "The request had no length; rebuild the Shortcut's request body.",
        413: "The question was too long to send.",
        423: "Paused: a note in the vault is labelled client, or has conflicting labels. "
             "Nothing was answered. The note's path is in the Air's log; remove or relabel it.",
        429: "The brain is still answering a previous question. Try again in a moment.",
        503: "The local model isn't answering on the Air. Try again in a minute.",
        504: "No answer within 2 minutes. The Air may be busy; try a shorter question.",
        500: "Something went wrong on the Air. Nothing was changed; try again.",
    }.get(code, "Request failed (%d)." % code)


def _renumber(answer_text, label_to_n):
    """[E3] -> [2]: number sources as the phone lists them; drop ids it does not list."""
    def sub(m):
        n = label_to_n.get(int(m.group(1)))
        return "[%d]" % n if n else ""
    return re.sub(r"\s+(?=\[E\d+\])", " ", CITE_ID.sub(sub, answer_text)).replace("  ", " ").strip()


class Service:
    """Everything the handler needs, so tests can build one with a stub model."""

    def __init__(self, token, model_factory, profile=None, vault=None, limit=None,
                 tracing=True, model_health=None, today=None, failures=None):
        if not token or len(token) < 32:
            raise ValueError("a bearer token of at least 32 characters is required")
        self._token = token.encode("utf-8")
        self.model_factory = model_factory
        self.profile = profile or config.MVP_WHOLE_VAULT
        self.vault = vault
        self.limit = limit or int(os.environ.get("DBRAIN_MVP_CHUNKS", "4"))
        self.tracing = tracing
        self.busy = threading.Lock()
        self.deadline = REQUEST_TIMEOUT
        self.model_health = model_health or (lambda: None)
        self.today = today or today_local
        # P2.7 step C2: a FailureStore, or None (capture off). Set only by main(), and only
        # when the launcher turns capture on.
        self.failures = failures

    def authorised(self, header):
        if not header or not header.startswith("Bearer "):
            return False
        return hmac.compare_digest(header[len("Bearer "):].strip().encode("utf-8"), self._token)

    @staticmethod
    def _citations(result):
        """Cited sources in the order the phone shows them, each with its quoted line(s),
        and the answer's [En] ids renumbered to match that order."""
        by_label = {i: c for i, (_, c) in enumerate(result.hits, 1)}
        order, label_to_n = [], {}
        for m in CITE_ID.finditer(result.answer or ""):
            label = int(m.group(1))
            chunk = by_label.get(label)
            if chunk is None or label in label_to_n:
                continue
            cite = chunk.cite()
            existing = [n for n, c in enumerate(order, 1) if c[0] == cite]
            if existing:
                label_to_n[label] = existing[0]
                continue
            order.append((cite, chunk))
            label_to_n[label] = len(order)
        out = []
        for cite, chunk in order:
            c = citation(cite)
            c["quote"] = quote_lines(chunk.text, result.answer or "")
            out.append(c)
        result.answer = _renumber(result.answer or "", label_to_n)
        return out

    def ask_within(self, question, started):
        """`ask` under the end-to-end deadline (step 5): `started` is when the request
        arrived (time.monotonic). The caller holds `busy`; the worker releases it when the
        model call really ends, which may be after the phone got its 504."""
        box = {}
        if time.monotonic() - started >= self.deadline:  # already spent reading the request
            self.busy.release()
            return self._timed_out()

        def work():
            try:
                box["result"] = self.ask(question)
            except Exception as exc:  # never a blank screen on the phone
                sys.stderr.write("%s ask failed: %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"),
                                                         type(exc).__name__))
                box["result"] = (500, {"status": "error"})
            finally:
                self.busy.release()

        worker = threading.Thread(target=work, name="dbrain-ask", daemon=True)
        worker.start()
        worker.join(max(0.0, self.deadline - (time.monotonic() - started)))
        if (worker.is_alive() or "result" not in box
                or time.monotonic() - started > self.deadline):
            return self._timed_out()
        return box["result"]

    def record_failure(self, question, status_code, payload, reason_code=None):
        """P2.7: keep a failed question for the loop, once per request, at the outcome the
        phone saw, with every signal it raised (failures.SIGNALS); an unverified answer is
        only counted. Never affects the reply: called after it is sent, and any error here
        is reported by its type only."""
        if self.failures is None:
            return None
        try:
            return self.failures.capture(question, status_code, payload, reason_code)
        except Exception as exc:
            sys.stderr.write("%s failure capture skipped: %s\n"
                             % (time.strftime("%Y-%m-%dT%H:%M:%S"), type(exc).__name__))
            return None

    def _timed_out(self):
        sys.stderr.write("%s ask exceeded the %g s deadline\n"
                         % (time.strftime("%Y-%m-%dT%H:%M:%S"), self.deadline))
        return 504, {"status": "timeout", "reason": "no answer within %g s" % self.deadline}

    def ask(self, question):
        started = time.time()
        model = self.model_factory()
        status_code = 200
        today = self.today()
        asked, found = resolve_dates(question, today)
        unclear = [phrase for phrase, days in found if not days]
        if unclear:
            # A date that cannot be read confidently: ask again rather than guess. No
            # retrieval, no model call.
            payload = {"status": "unclear", "reason": "date not understood",
                       "answer": "I couldn't read the date in \"%s\". Ask again with one "
                                 "plain date, such as \"tomorrow\", \"next Monday\" or "
                                 "\"Tue 6 Oct\"." % unclear[0],
                       "citations": [], "latency_s": round(time.time() - started, 2)}
            payload["status_line"] = plain_status("unclear", "", [])
            payload["display"] = display_text(payload)
            return 200, payload
        try:
            result = answer.ask(asked, profile=self.profile, model=model,
                                vault=self.vault, limit=self.limit,
                                prompt=dated_prompt(today, found))
            cites = self._citations(result)  # renumbers result.answer, so it goes first
            if found and result.answer:
                # Always state the date the question was resolved to, whatever the answer
                # says (Codex consultation, session 20): it sets the context next to the
                # quoted source lines; it does not certify the answer.
                result.answer = "(Asked about %s.) %s" % (
                    days_label([d for _, ds in found for d in ds]), result.answer)
            payload = {
                "status": result.status,
                "reason": result.reason,
                "answer": result.answer,
                "citations": cites,
                # For the capture point only: the handler removes it before the reply, so
                # the phone's JSON is unchanged (P2.7 step C2).
                REASON_KEY: result.reason_code,
            }
        except index.ProhibitedNote as exc:
            # AGENTS.md rules 2-3: client material (or conflicting labels) is a breach to stop
            # on and report, not to skip. Every question is refused until it is resolved;
            # the path goes to the Air's log for David, never the note's content.
            sys.stderr.write("%s POLICY FLAG: %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), exc))
            result = None
            status_code = 423
            payload = {"status": "paused", "reason": "prohibited note in the vault",
                       "answer": "", "citations": []}
        except adapter_mod.MalformedResponse as exc:
            result = getattr(exc, "partial", None)
            payload = {"status": "malformed", "reason": "the local model's reply could not be used",
                       "answer": "", "citations": []}
        except adapter_mod.ModelUnavailable as exc:
            result = getattr(exc, "partial", None)
            status_code = 503
            payload = {"status": "unavailable", "reason": "the local model is not answering",
                       "answer": "", "citations": []}
        payload["latency_s"] = round(time.time() - started, 2)
        payload["status_line"] = plain_status(payload["status"], payload["reason"],
                                              payload["citations"])
        payload["display"] = display_text(payload)
        if self.tracing and result is not None:
            try:
                trace.write(result, model, self.profile)
            except outpath.OutsideVaultError:
                pass  # the guard refused; the answer is still returned, nothing is written
        return status_code, payload


def make_handler(service):
    class Handler(BaseHTTPRequestHandler):
        server_version = "dbrain-ask/0.1"
        sys_version = ""
        timeout = REQUEST_TIMEOUT + 10

        # Logging and error replies carry only server-generated text (Codex consultation,
        # session 20): a fixed method label (GET, POST, OTHER), a fixed route label (/ask,
        # /health, OTHER), a numeric status and fixed event names. Nothing the client sent
        # (method, path, query, headers, body) is ever interpolated, so the token cannot
        # reach the log whichever field it is put in.
        def _labels(self):
            method = getattr(self, "command", None)
            method = method if method in ("GET", "POST") else "OTHER"
            path = (getattr(self, "path", "") or "").split("?", 1)[0]
            route = path if path in ("/ask", "/health") else "OTHER"
            return method, route

        def _log(self, event, code=None):
            method, route = self._labels()
            try:
                code = "%d" % int(getattr(code, "value", code))
            except (TypeError, ValueError):
                code = "-"
            sys.stderr.write("%s %s %s %s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"),
                                                  method, route, event, code))

        def log_request(self, code="-", size="-"):
            self._log("response", code)

        def log_error(self, fmt, *args):
            self._log("error")  # http.server's messages can quote the request line

        def log_message(self, fmt, *args):
            self._log("message")

        def send_error(self, code, message=None, explain=None):
            """http.server's own errors (bad request line, unsupported method, header
            limits) would echo client text in the status line and the body. Both supplied
            strings are discarded: the reply is the fixed reason phrase and `display`."""
            try:
                code = int(code)
            except (TypeError, ValueError):
                code = 500
            self.close_connection = True
            body = json.dumps({"status": "error", "display": error_display(code)}).encode("utf-8")
            self.send_response(code)  # reason phrase from http.server's fixed table
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if getattr(self, "command", None) != "HEAD" and code >= 200 and code not in (204, 304):
                self.wfile.write(body)

        # The end-to-end deadline runs from when the connection opened, so header reading
        # counts too. A connection whose headers are not complete by then is closed without
        # a reply (there is no request yet to answer); one with headers gets 504.
        def setup(self):
            self.arrived = time.monotonic()
            self.headers_done = threading.Event()
            self.dropped = False
            super().setup()
            self._header_timer = threading.Timer(service.deadline, self._drop_if_no_headers)
            self._header_timer.daemon = True
            self._header_timer.start()

        def _drop_if_no_headers(self):
            if not self.headers_done.is_set():
                self.dropped = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        def parse_request(self):
            ok = super().parse_request()
            self.headers_done.set()
            self._header_timer.cancel()
            if self.dropped:  # the deadline closed the connection mid-headers: no reply
                self.close_connection = True
                return False
            return ok

        def finish(self):
            self._header_timer.cancel()
            super().finish()

        def _send(self, code, obj):
            if code != 200 and "display" not in obj:
                obj = dict(obj, display=error_display(code))
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self, length, started):
            """The body, read under what is left of the end-to-end deadline; None if it
            runs out. Each read waits at most the time remaining, so a slow sender cannot
            hold the request past the deadline."""
            settimeout = getattr(self.connection, "settimeout", None)
            buf = b""
            try:
                while len(buf) < length:
                    left = service.deadline - (time.monotonic() - started)
                    if left <= 0:
                        return None
                    if settimeout:
                        settimeout(left)
                    chunk = self.rfile.read1(length - len(buf))
                    if not chunk:
                        break  # short body: the JSON check answers 400
                    buf += chunk
            except (socket.timeout, TimeoutError):
                return None
            finally:
                if settimeout:
                    settimeout(self.timeout)
            if time.monotonic() - started >= service.deadline:
                return None
            return buf

        def _peer_ok(self):
            if allowed_address(self.client_address[0]):
                return True
            self._send(403, {"status": "refused", "reason": "not on the tailnet"})
            return False

        def do_GET(self):
            if not self._peer_ok():
                return
            if self.path != "/health":
                return self._send(404, {"status": "not found"})
            self._send(200, {"ok": True, "model": service.model_health(),
                             "busy": service.busy.locked()})

        def do_POST(self):
            started = self.arrived
            if not self._peer_ok():
                return
            if self.path != "/ask":
                return self._send(404, {"status": "not found"})
            if not service.authorised(self.headers.get("Authorization")):
                return self._send(401, {"status": "unauthorised"})
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self._send(411, {"status": "length required"})
            if length <= 0 or length > MAX_BODY:
                return self._send(413, {"status": "too large", "reason": "max %d bytes" % MAX_BODY})
            raw = self._read_body(length, started)
            if raw is None:  # the deadline runs from arrival, body reading included
                return self._send(*service._timed_out())
            try:
                body = json.loads(raw.decode("utf-8"))
                question = body["question"]
                if not isinstance(question, str):
                    raise TypeError
            except (ValueError, KeyError, TypeError, UnicodeDecodeError):
                return self._send(400, {"status": "bad request",
                                        "reason": 'send JSON: {"question": "..."}'})
            question = question.strip()
            if not question or "\x00" in question or len(question) > MAX_QUESTION:
                return self._send(400, {"status": "bad request",
                                        "reason": "question must be 1-%d characters" % MAX_QUESTION})
            if not service.busy.acquire(blocking=False):
                return self._send(429, {"status": "busy",
                                        "reason": "one question at a time; try again shortly"})
            code, payload = service.ask_within(question, started)
            payload = dict(payload)
            reason_code = payload.pop(REASON_KEY, None)
            if code != 200:
                payload = dict(payload, display=error_display(code))
            self._send(code, payload)
            # Once per request, here and only here: a model worker that finishes after a
            # 504 records nothing more (P2.7 step C2, the capture point).
            service.record_failure(question, code, payload, reason_code)

    return Handler


def llama_health(endpoint=None):
    """`up` or `down`. The same guards as inference: the endpoint must be loopback and
    resolve to it, the request goes to the pinned address, and a redirect is refused."""
    try:
        model = adapter_mod.LlamaCppAdapter(endpoint=endpoint, timeout=3)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                             adapter_mod.RefuseRedirects())
        req = urllib.request.Request(model.endpoint + "/health",
                                     headers={"Host": model.host_header})
        with opener.open(req, timeout=3) as r:
            return "up" if r.status == 200 else "down"
    except Exception:
        return "down"


class _Server(ThreadingHTTPServer):
    # A timeout on the listening socket, so accept() never blocks the serving loop: if the
    # selector reports a connection that is gone by the time accept() runs, the loop gets a
    # timeout (an OSError the stdlib already swallows), goes back to polling, and still sees
    # shutdown(). Without it, shutdown() could wait for ever: the suspected cause of the
    # intermittent P2.4 test hang, not proven (Codex consultation, 8 Oct). Accepted connections get their own timeout in Handler.setup().
    ACCEPT_TIMEOUT = 0.5

    def server_activate(self):
        super().server_activate()
        self.socket.settimeout(self.ACCEPT_TIMEOUT)

    def handle_error(self, request, client_address):
        # No traceback: a fixed event only (a dropped connection, a client gone mid-reply).
        sys.stderr.write("%s connection error\n" % time.strftime("%Y-%m-%dT%H:%M:%S"))


def build_server(host, port, service):
    check_bind(host)
    return _Server((host, port), make_handler(service))


def setup_failures(service, env, store_factory=None, store_path=None):
    """P2.7: capture is off unless the launcher turns it on (D110; the loop's go). Retention
    is not tied to that switch: §7a's 90 days apply to every stored failure, so the sweep
    runs whenever the store exists, even after capture is turned off again. Returns the
    store whose retention was started, or None."""
    store_factory = store_factory or failures_mod.FailureStore
    store_path = Path(store_path or failures_mod.STORE)
    capture_on = env.get("DBRAIN_FAILURE_CAPTURE") == "on"
    if not (capture_on or store_path.exists()):
        return None
    store = store_factory()
    store.start_retention()
    service.failures = store if capture_on else None
    return store


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dbrain.serve", description=__doc__.split("\n")[0])
    ap.add_argument("--host", default=None, help="Tailscale address (default: from the Tailscale CLI)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args(argv)

    try:
        today_local()
    except Exception:
        raise SystemExit("no time-zone database for %s: refusing to start rather than guess "
                         "today's date" % TIMEZONE)
    host = args.host or tailscale_ipv4()
    if not host:
        raise SystemExit("no Tailscale address found: install Tailscale and log in, or pass --host")
    try:
        check_bind(host)
    except BindRefused as exc:
        raise SystemExit(str(exc))
    bearer = keychain_token()
    service = Service(
        bearer,
        model_factory=lambda: adapter_mod.LlamaCppAdapter(timeout=REQUEST_TIMEOUT - 10),
        model_health=llama_health,
    )
    del bearer
    setup_failures(service, os.environ)
    server = build_server(host, args.port, service)
    sys.stderr.write("%s dbrain-ask listening on http://%s:%d (profile %s)\n"
                     % (time.strftime("%Y-%m-%dT%H:%M:%S"), host, args.port, service.profile.name))
    server.serve_forever()


if __name__ == "__main__":
    main()

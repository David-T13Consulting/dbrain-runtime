"""Turn a question into a cited answer, an incomplete answer, or an abstention.

`supported` is deliberately unreachable unless a verifier is supplied.

The lexical grounding check catches an answer that cites real evidence while talking
about something else, but it cannot tell a true claim from a plausible false one built
from the evidence's own vocabulary: "tempo means marathon pace or slower" scores 80%
against evidence saying "or faster". Documenting that limitation is not enough - a
verdict of `supported` next to a contradiction is simply wrong. So grounding is treated
as a *necessary* condition, and the sufficient one is an entailment check this module
does not implement. With no verifier, the best available verdict is `incomplete`.

Supplying a verifier is T4.1 / P2.1 work. Until then the service never claims support
it cannot demonstrate.
"""
import re

from . import adapter as adapter_mod
from . import config, index, retrieve
from .retrieve import terms

CITE = re.compile(r"\[(E\d+)\]")

SUPPORTED, INCOMPLETE, WITHHELD = "supported", "incomplete", "withheld"
# Trace-only: a query that got no answer from the model. Never shown as a verdict; the
# CLI fails loudly instead.
FAILED = "failed"

PROMPT = """You answer only from the evidence below, about David's own notes.

Rules:
- Use only the evidence. If it does not answer the question, say so plainly.
- Cite the evidence you used by its bracketed id, like [E1]. Cite nothing else.
- Do not guess, and do not use knowledge from outside the evidence.

Question: %s

Evidence:
%s

Answer (with [En] citations):"""


# Phrases with which a model declines. If it says it cannot answer, the service must not
# dress that up as an answer just because a citation is attached.
DECLINED = (
    "insufficient evidence", "no evidence", "not enough evidence", "cannot answer",
    "can't answer", "unable to answer", "does not say", "doesn't say", "not stated",
    "not specified", "no information", "i don't know", "i do not know",
    "the evidence does not", "nothing in the evidence", "remains unknown",
    "is unknown", "not known", "no mention", "does not mention", "doesn't mention",
)

# Fraction of the answer's content words that must also appear in the cited evidence.
#
# Set deliberately low. This is a *grounding* check, not entailment verification: it
# catches an answer that cites real evidence while talking about something else
# entirely - the "moon is made of cheese [E1]" failure, which scores 0.
#
# Kept low on purpose. Measured overlap for a *correct* short answer against the real
# corpus was 0.20, because a two-line chunk shares little vocabulary with a one-sentence
# paraphrase. A higher bar would reject good answers while still not catching
# contradictions, which is why this is only a necessary condition and never a sufficient
# one. Nothing here is faithfulness checking: that is T4.1 / P2.1 work.
GROUNDING_THRESHOLD = 0.15


def _declines(text):
    low = text.lower()
    return any(phrase in low for phrase in DECLINED)


def grounding_overlap(answer_text, cited_chunks):
    """Share of the answer's content words that appear in the evidence it cited."""
    stripped = CITE.sub(" ", answer_text)
    words = terms(stripped)
    if not words:
        return 0.0
    evidence = set()
    for chunk in cited_chunks:
        evidence.update(terms(chunk.heading + " " + chunk.text))
    if not evidence:
        return 0.0
    hit = sum(1 for w in words if w in evidence)
    return hit / float(len(words))


class Result:
    def __init__(self, question, status, answer, citations, hits, timing=None, invalid=(),
                 files=None, grounding=None, reason=None, skipped=None):
        self.question = question
        self.status = status
        self.answer = answer
        self.citations = citations      # vault paths with line ranges
        self.hits = hits
        self.timing = timing or {}
        self.invalid = tuple(invalid)
        self.files = files or {}          # every note indexed for this query
        self.grounding = grounding        # overlap score, or None if not computed
        self.reason = reason              # why this verdict, in one phrase
        self.skipped = list(skipped or [])  # (path, reason): read, refused by the label rules, never indexed

    def render(self):
        header = "[%s]" % self.status.upper()
        if self.reason:
            header += " (%s)" % self.reason
        lines = ["%s %s" % (header, self.answer.strip())]
        if self.citations:
            lines.append("")
            lines.append("Sources:")
            for c in self.citations:
                lines.append("  - %s" % c)
        if self.invalid:
            lines.append("")
            lines.append(
                "Ignored invented citations: %s" % ", ".join(self.invalid)
            )
        return "\n".join(lines)


def ask(question, profile=None, model=None, vault=None, limit=4, verifier=None, prompt=None):
    """Answer `question` from the profile's slice of the vault.

    `verifier`, when given, is called as verifier(answer_text, cited_chunks) -> bool and
    decides entailment. Without one, no answer is reported as `supported`.

    `prompt` replaces PROMPT for one caller (the MVP's fact-first wording) without
    changing the prompt version the P2.2 measurements are pinned to.
    """
    profile = profile or config.DEFAULT
    skipped = []
    chunks, files = index.build(profile, vault, skipped=skipped)
    ix = retrieve.Index(chunks)
    hits = ix.search(question, limit=limit)

    if not hits:
        # Abstain before calling the model at all: there is nothing to ground an answer in.
        return Result(
            question,
            WITHHELD,
            "No supporting evidence in the %s corpus, so I am not answering. "
            "Widen the allowlist or ask about something the notes cover." % profile.name,
            [],
            [],
            files=files, skipped=skipped,
            reason="no evidence retrieved",
        )

    model = model or adapter_mod.LlamaCppAdapter()
    # Label the evidence E1..En for this query only. Global chunk ids are sparse once
    # retrieval has filtered them, and a model asked to cite [C18] out of four options
    # is being invited to guess.
    labelled = [("E%d" % i, c) for i, (_, c) in enumerate(hits, 1)]
    evidence = "\n\n".join(
        "[%s] %s (%s)\n%s" % (label, c.heading or "(no heading)", c.cite(), c.text)
        for label, c in labelled
    )
    try:
        text, elapsed = model.generate((prompt or PROMPT) % (question, evidence))
    except adapter_mod.ModelUnavailable as exc:
        # Round D finding 3: the corpus was read and the model was called, so this query
        # still owes a trace (condition 6). Attach what is known; the caller writes it.
        # Round E finding 4: the exception text can quote the model server's error object,
        # a redirect URL or a finish_reason, any of which can echo the question. The
        # trace gets the exception class only, never remote text.
        exc.partial = Result(
            question, FAILED, "", [], hits, {}, (), files=files, skipped=skipped,
            reason=type(exc).__name__,
        )
        raise

    by_id = dict(labelled)
    cited = []
    invalid = []
    for cid in CITE.findall(text):
        if cid in by_id and cid not in cited:
            cited.append(cid)
        elif cid not in by_id and cid not in invalid:
            invalid.append(cid)

    # Four ways to fail to be "supported", checked in order of severity.
    cited_chunks = [by_id[c] for c in cited]
    overlap = grounding_overlap(text, cited_chunks) if cited_chunks else 0.0

    if _declines(text):
        # The model itself said it could not answer. Report that, not a false positive.
        status, reason = WITHHELD, "the model declined on the available evidence"
    elif invalid:
        status, reason = INCOMPLETE, "cited an id that was never offered: %s" % ", ".join(invalid)
    elif not cited:
        status, reason = INCOMPLETE, "answered without citing any evidence"
    elif overlap < GROUNDING_THRESHOLD:
        # Cited something real, but the answer is largely not about it.
        status, reason = INCOMPLETE, (
            "only %.0f%% of the answer's wording appears in the evidence it cited "
            "(threshold %.0f%%)" % (overlap * 100, GROUNDING_THRESHOLD * 100)
        )
    elif verifier is None:
        # Grounding is necessary but not sufficient. No entailment check is available,
        # so support is not claimed. See the module docstring.
        status, reason = INCOMPLETE, (
            "grounded (%.0f%% lexical overlap) but entailment unverified: no verifier "
            "is available, so support is not claimed" % (overlap * 100)
        )
    elif not verifier(text, cited_chunks):
        status, reason = INCOMPLETE, "a verifier rejected the answer as unsupported"
    else:
        status, reason = SUPPORTED, (
            "cited evidence, %.0f%% lexical overlap, entailment confirmed" % (overlap * 100)
        )

    return Result(
        question,
        status,
        text,
        [c.cite() for c in cited_chunks],
        hits,
        {"model_seconds": elapsed},
        invalid,
        files=files, skipped=skipped,
        grounding=round(overlap, 3),
        reason=reason,
    )

"""Corpus profiles: which parts of the vault a query may read.

A profile is data, not convention. scope.py enforces it; nothing else may widen it.
"""
import os
from pathlib import Path

VAULT = Path(os.environ.get("DBRAIN_VAULT", Path.home() / "Work" / "Dbrain2"))

# Traces and results live outside the vault (P2.2 "Done when" 6), in a git-ignored folder
# of this repository (David, Q23, 5 Oct: as lean as possible while keeping the boundary).
# Never in the vault: the vault is the corpus, and every cloud agent reads it.
ARTIFACTS = Path(
    os.environ.get("DBRAIN_ARTIFACTS", Path(__file__).resolve().parent.parent / "artifacts")
)

# The service's §7b logs (data policy v0.7): the per-question trace, the service log and
# the model server's log, with the failure loop's audit log beside them. Outside the vault
# and outside git, on the host (P2.7 step C3b): a git-ignored folder inside a repository
# is still inside git. Measurement evidence stays under ARTIFACTS.
LOGS = Path(
    os.environ.get("DBRAIN_LOGS", Path.home() / "Library" / "Application Support" / "dbrain" / "logs")
)


class Profile:
    """An allowlist. `roots` are directories; `files` are individual notes."""

    def __init__(self, name, roots=(), files=(), sections=None, exclude_hidden=False,
                 skip_unlabelled=False):
        self.name = name
        self.roots = tuple(roots)
        self.files = tuple(files)
        # Whole-vault profiles: never read `.git`, `.obsidian` or any other dot-folder.
        self.exclude_hidden = exclude_hidden
        # Whole-vault profiles: a note with no `classification`, another label, or
        # frontmatter outside the allowlisted language is skipped and recorded instead of
        # failing every query (Q46); it never reaches the model. A recognisable `client`
        # label still stops every query (index.ProhibitedNote). Narrow profiles keep the
        # strict default: fail loudly.
        self.skip_unlabelled = skip_unlabelled
        # Optional {file: (start_prefix, end_prefix)} to bound what is indexed inside a
        # file. The 1 Oct retrieval baseline indexed only section 0 of the roadmap, so
        # reproducing its cases means indexing the same span, not the whole note.
        self.sections = dict(sections or {})

    def __repr__(self):
        return "Profile(%r, %d roots, %d files)" % (self.name, len(self.roots), len(self.files))


# The allowlist P2.2 specifies, as Cowork corrected it on 2 Oct ("Done when" 2): exactly
# the corpus the 10 dev cases were written against - three named files, with the roadmap
# bounded to section 0, and no roots. The first version of the brief listed the Running
# and Local AI folders and 02_Architecture as roots; no dev case covers them, and a root
# over 02_Architecture let evaluation notes - including the 5 Oct results note itself -
# into the corpus they measure (round C finding 1). Widening the corpus (M5) is a
# separate, versioned change with its own cases.
#
# Run reports record a content hash of the indexed spans, so a note that changes between
# runs shows up as a different corpus rather than silently.
SCOPED_FILES = (
    "AGENTS.md",
    "10 Projects/AI Second Brain/02_Architecture/data-policy.md",
    "10 Projects/AI Second Brain/04_Roadmap.md",
)
SCOPED_SECTIONS = {
    # Section 0 is the live amendment; sections 1+ are superseded work packages.
    # The 1 Oct prototype bounded the roadmap the same way.
    "10 Projects/AI Second Brain/04_Roadmap.md": ("## 0.", "## 1."),
}

DEFAULT = Profile("default", files=SCOPED_FILES, sections=SCOPED_SECTIONS)

# Kept as a name because run reports since 2 Oct record it. Same allowlist as DEFAULT
# now that the brief's allowlist *is* the dev-case corpus.
DEVCASES = Profile("devcases", files=SCOPED_FILES, sections=SCOPED_SECTIONS)

# MVP-phone (David, Q34, 5 Oct): the whole vault, read-only, for questions from the
# iPhone. Root "" is the vault itself. Dot-folders are excluded; the reserved acceptance
# cases live outside the vault, so they are unreachable by construction. Not used by the
# dev-case measurements, which stay on the three-file corpus.
MVP_WHOLE_VAULT = Profile("mvp-whole-vault", roots=("",), exclude_hidden=True,
                          skip_unlabelled=True)

PROFILES = {p.name: p for p in (DEFAULT, DEVCASES, MVP_WHOLE_VAULT)}

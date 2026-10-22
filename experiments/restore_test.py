#!/usr/bin/env -S python3 -I -B
"""T4.2 restore test (PL1 step 5; data policy §7: before any runtime write).

Narrow contract (David, Q148, 21 Oct): two repositories only, the vault and the runtime,
as they are. Anything unusual about either is refused, not handled.

  1. Bad-commit rollback: in a local clone, a synthetic bad commit overwrites one regular
     tracked file and deletes another; `git revert` must bring back exactly the original
     tree.
  2. Restore after loss of the working copy: the clone is deleted, then the state is
     restored by cloning the repository's fixed GitHub URL (its off-machine copy). The
     restored HEAD and tree must equal the state snapshotted before the drills; `git fsck
     --full` must pass; the checkout must be clean.

Uncommitted tracked changes and commits not on GitHub fail the test: a restore would lose
them. Out of scope, and the report says so: untracked and ignored files, and everything
outside the repository (models, logs, failure store); whether any backup holds them is
not tested.

The repository under test, its remote and the vault are only read: no bytecode is written
for this script's imports, the temporary folder is located without probing, and its
location is checked before anything is created. Git runs with no
inherited GIT_* variables, no system or global config or attributes, no replace objects,
no optional locks, no prompts, no hooks and no fsmonitor. The GitHub clone reads the
credential from the Keychain through a helper that answers `get` only, so nothing is ever
stored or erased (David, Q150, these two clones only). The report goes to a fixed folder
outside every git working tree and outside the vault.

    python3 -I -B experiments/restore_test.py vault
    python3 -I -B experiments/restore_test.py runtime

`-I -B` is required, and checked before anything else is imported: `-B` stops bytecode
writes from interpreter start, `-I` ignores PYTHON* variables such as PYTHONPYCACHEPREFIX.
Every outcome, including a bad argument, an import failure or Ctrl-C, ends in a report.
"""
import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.dont_write_bytecode):
    sys.stdout.write('{"test": "T4.2 restore test", "ok": false, "error": "refused: run as '
                     'python3 -I -B experiments/restore_test.py vault|runtime", "not_tested": '
                     '"untracked and ignored files, and state outside the repository"}\n')
    sys.exit(2)

import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True  # importing dbrain must not write __pycache__ in the runtime
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

try:
    from dbrain import config, outpath  # noqa: E402
    IMPORT_ERROR = None
except Exception as _exc:  # reported by main(), which then writes to stdout only
    config = outpath = None
    IMPORT_ERROR = "%s: %s" % (type(_exc).__name__, _exc)

HOME = Path.home()
_CORE = {"core.repositoryformatversion": "0", "core.filemode": "true", "core.bare": "false",
         "core.logallrefupdates": "true", "core.ignorecase": "true",
         "core.hookspath": ".githooks",
         "remote.origin.fetch": "+refs/heads/*:refs/remotes/origin/*"}


def _pinned(branch, url, **extra):
    cfg = dict(_CORE, **{"remote.origin.url": url, "branch.%s.remote" % branch: "origin",
                         "branch.%s.merge" % branch: "refs/heads/" + branch,
                         "branch.%s.vscode-merge-base" % branch: "origin/" + branch})
    cfg.update(extra)
    return cfg


_VAULT_URL = "https://github.com/David-T13Consulting/Dbrain2.git"
_RUNTIME_URL = "https://github.com/David-T13Consulting/dbrain-runtime.git"
# Every local config key and its exact value, as the two repositories have them (read 21
# Oct). Anything else is refused: an extra key (fsmonitor, sparse checkout, extensions,
# includes, url rewrites, filters, LFS, submodules, credentials, http, a second remote), a
# changed value (core.filemode=false would hide executable-bit changes), a missing key or a
# repeated one.
REPOS = {
    "vault": {"path": HOME / "Work" / "Dbrain2", "branch": "master", "url": _VAULT_URL,
              "config": _pinned("master", _VAULT_URL, **{"core.symlinks": "false"})},
    "runtime": {"path": HOME / "Work" / "dbrain-runtime", "branch": "main", "url": _RUNTIME_URL,
                "config": _pinned("main", _RUNTIME_URL, **{"core.precomposeunicode": "true"})},
}
REPORTS = HOME / "Library" / "Application Support" / "dbrain" / "t42"
REPOS_VAULT = REPOS["vault"]["path"]  # fixed, whatever DBRAIN_VAULT says
TIMEOUT = 300  # seconds per git call

# May be absent (the editor writes it); if present, its value is pinned like the rest.
OPTIONAL_KEY_SUFFIX = ".vscode-merge-base"
# Present in .git only for an unusual setup or an operation in progress.
DENIED_GIT_PATHS = ("shallow", "commondir", "worktrees", "modules", "lfs",
                    "objects/info/alternates", "objects/info/http-alternates",
                    "info/sparse-checkout", "info/attributes", "info/grafts", "refs/replace",
                    "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge",
                    "rebase-apply", "BISECT_LOG", "index.lock")

GIT_ARGS = ["-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
            "-c", "user.name=restore-test", "-c", "user.email=restore-test@invalid",
            "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
            "-c", "core.attributesFile=/dev/null", "-c", "protocol.file.allow=user"]
# Read-only credential access for the GitHub clone (Q150): the helper list is emptied,
# then one helper is added that passes `get` to osxkeychain and ignores `store`/`erase`.
CREDENTIAL_ARGS = ["-c", "credential.helper=",
                   "-c", 'credential.helper=!f() { [ "$1" = get ] && exec git credential-osxkeychain get; }; f']


class RestoreFailed(Exception):
    pass


def git_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null", GIT_ATTR_NOSYSTEM="1",
               GIT_NO_REPLACE_OBJECTS="1", GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    return env


def git(cwd, *args, extra=()):
    try:
        res = subprocess.run(["git"] + GIT_ARGS + list(extra) + list(args), cwd=str(cwd),
                             capture_output=True, env=git_env(), timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RestoreFailed("git %s timed out after %d s" % (args[0], TIMEOUT)) from None
    if res.returncode != 0:
        err = res.stderr.decode("utf-8", "replace").strip()[-300:]
        raise RestoreFailed("git %s failed (%d): %s" % (" ".join(args), res.returncode, err))
    try:
        return res.stdout.decode("utf-8")
    except UnicodeDecodeError:
        raise RestoreFailed("git %s printed output that is not UTF-8" % args[0]) from None


def tree(cwd, rev="HEAD"):
    return git(cwd, "rev-parse", rev + "^{tree}").strip()


def _inside(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def _check_config(path, spec):
    want, seen = spec["config"], {}
    for item in git(path, "config", "--local", "--no-includes", "--list", "-z").split("\0"):
        if not item:
            continue
        key, _, value = item.partition("\n")
        if key in seen:
            raise RestoreFailed("repeated local git config key: %s" % key)
        seen[key] = value
        if key == "remote.origin.url":
            if value != spec["url"]:
                raise RestoreFailed("origin is not the fixed GitHub URL")
            continue
        if key not in want:
            raise RestoreFailed("unexpected local git config key: %s" % key)
        if value != want[key]:
            raise RestoreFailed("unexpected value for local git config key %s" % key)
    missing = sorted(k for k in want if k not in seen and not k.endswith(OPTIONAL_KEY_SUFFIX))
    if missing:
        raise RestoreFailed("missing local git config key: %s" % ", ".join(missing))


def preflight(spec):
    """One check per class of unusual setup; each is a refusal (D74 consult, Q148)."""
    path, branch = Path(spec["path"]), spec["branch"]
    if os.path.realpath(str(path)) != str(path) or not stat.S_ISDIR(os.lstat(str(path)).st_mode):
        raise RestoreFailed("repository path is a link or not a directory")
    gitdir = path / ".git"
    if not stat.S_ISDIR(os.lstat(str(gitdir)).st_mode):
        raise RestoreFailed(".git is not a plain directory")
    if git(path, "rev-parse", "--absolute-git-dir").strip() != str(gitdir):
        raise RestoreFailed("git directory is not the repository's own .git")
    if git(path, "rev-parse", "--path-format=absolute", "--git-common-dir").strip() != str(gitdir):
        raise RestoreFailed("shared git directory (worktree)")
    _check_config(path, spec)
    for rel in DENIED_GIT_PATHS:
        if os.path.lexists(str(gitdir / rel)):
            raise RestoreFailed("unusual git state: .git/%s" % rel)
    packed = gitdir / "packed-refs"
    if packed.exists() and b"refs/replace/" in packed.read_bytes():
        raise RestoreFailed("unusual git state: replace refs")
    if git(path, "symbolic-ref", "-q", "HEAD").strip() != "refs/heads/" + branch:
        raise RestoreFailed("not on the fixed branch %s" % branch)
    commit = git(path, "rev-parse", "HEAD").strip()
    for entry in git(path, "ls-tree", "-r", "-z", "--full-tree", commit).split("\0"):
        if not entry:
            continue
        meta, name = entry.split("\t", 1)
        if meta.split()[0] not in ("100644", "100755"):
            raise RestoreFailed("tree holds a link, submodule or other non-file entry")
        if name.rsplit("/", 1)[-1] in (".gitattributes", ".gitmodules"):
            raise RestoreFailed("tree holds %s" % name.rsplit("/", 1)[-1])
    for entry in git(path, "ls-files", "-v", "-z").split("\0"):
        if entry and not entry.startswith("H "):
            raise RestoreFailed("index entry with an unusual flag (assume-unchanged, skip-worktree)")
    if git(path, "status", "--porcelain", "--untracked-files=no").strip():
        raise RestoreFailed("tracked files have uncommitted changes, which a restore would lose")
    return {"commit": commit, "tree": tree(path), "branch": branch, "url": spec["url"]}


def _overwrite(path, text):
    fd = os.open(str(path), os.O_WRONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise RestoreFailed("refusing to overwrite a non-regular or hard-linked file")
        os.ftruncate(fd, 0)
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)


def rollback_drill(spec, snap, tmp):
    work = tmp / "work"
    git(tmp, "clone", "--quiet", "--no-local", "--no-hardlinks", "--branch", snap["branch"],
        str(spec["path"]), str(work))
    if git(work, "rev-parse", "HEAD").strip() != snap["commit"]:
        raise RestoreFailed("local clone is not at the snapshotted commit")
    if git(work, "status", "--porcelain").strip():
        raise RestoreFailed("local clone is not clean after checkout (colliding paths?)")
    # Preflight refused every non-regular entry, so every tracked path is a regular file.
    files = [f for f in git(work, "ls-files", "-z").split("\0") if f]
    if len(files) < 2:
        raise RestoreFailed("need at least two tracked files for the rollback drill")
    victim, gone = files[0], files[-1]
    _overwrite(work / victim, "BAD COMMIT: synthetic overwrite by the restore test\n")
    if not stat.S_ISREG(os.lstat(str(work / gone)).st_mode):
        raise RestoreFailed("refusing to delete a non-regular file")
    os.unlink(str(work / gone))
    git(work, "add", "-A")
    git(work, "commit", "--quiet", "-m", "restore test: synthetic bad commit")
    if tree(work) == snap["tree"]:
        raise RestoreFailed("the synthetic bad commit did not change the tree")
    git(work, "revert", "--no-edit", "HEAD")
    if tree(work) != snap["tree"]:
        raise RestoreFailed("revert did not restore the original tree")
    if git(work, "status", "--porcelain").strip():
        raise RestoreFailed("working copy not clean after the revert")
    return work, {"overwritten": victim, "deleted": gone, "tree_after_revert": tree(work),
                  "ok": True}


def restore_drill(work, snap, tmp):
    shutil.rmtree(str(work))
    if work.exists():
        raise RestoreFailed("the disposable working copy could not be deleted")
    restored = tmp / "restored"
    git(tmp, "clone", "--quiet", "--branch", snap["branch"], snap["url"], str(restored),
        extra=CREDENTIAL_ARGS)
    got = git(restored, "rev-parse", "HEAD").strip()
    if got != snap["commit"]:
        raise RestoreFailed("GitHub's %s is not the snapshotted commit (unpushed, rewound "
                            "or moved on)" % snap["branch"])
    if tree(restored) != snap["tree"]:
        raise RestoreFailed("restored tree differs from the snapshot")
    git(restored, "fsck", "--full", "--no-progress")
    if git(restored, "status", "--porcelain").strip():
        raise RestoreFailed("restored working copy is not clean")
    return {"restored_commit": got, "restored_tree": tree(restored), "fsck": "ok", "ok": True}


def temp_parent(tmp_parent=None):
    """Where the drills' temporary folder goes, found without writing anything:
    `tempfile.gettempdir()` probes candidates by creating files in them, so it is not used.
    The location is checked before the folder is created."""
    parent = Path(os.path.realpath(str(tmp_parent or os.environ.get("TMPDIR") or "/tmp")))
    for root in [s["path"] for s in REPOS.values()] + [config.VAULT, REPOS_VAULT]:
        if _inside(parent, root):
            raise RestoreFailed("temporary folder lies inside a repository or the vault")
    outpath.assert_outside_git(parent)
    if not stat.S_ISDIR(os.lstat(str(parent)).st_mode):
        raise RestoreFailed("temporary folder is not a directory")
    return parent


def run(name, tmp_parent=None):
    spec = REPOS[name]
    parent = temp_parent(tmp_parent)
    snap = preflight(spec)
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="t42-", dir=str(parent)) as d:
        work, rollback = rollback_drill(spec, snap, Path(d))
        restore = restore_drill(work, snap, Path(d))
    if preflight(spec)["commit"] != snap["commit"]:
        raise RestoreFailed("the repository changed during the test; rerun")
    return {
        "test": "T4.2 restore test", "repo": name, "branch": snap["branch"],
        "state_tested": {"commit": snap["commit"], "tree": snap["tree"]},
        "remote": snap["url"],
        "started": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started)),
        "seconds": round(time.time() - started, 1),
        "rollback": rollback, "restore": restore,
        "scope": "the committed state named above, restored from GitHub without the "
                 "original working tree, git metadata or object store",
        "not_tested": NOT_TESTED,
        "ok": True,
    }


def write_report(name, report, folder=None):
    folder = Path(folder or REPORTS)
    out = folder / ("%s-%s.json" % (name, time.strftime("%Y%m%d-%H%M%S")))
    outpath.assert_outside_vault(out)
    with outpath.open_for_write(out, outside_git=True) as f:
        f.write(json.dumps(report, indent=1) + "\n")
    return out


NOT_TESTED = ("untracked and ignored files, and state outside the repository (models, logs, "
              "failure store): whether any backup holds them")


def _emit(text):
    """Last sink: stdout. Returns False if even that fails."""
    try:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
        return True
    except (OSError, ValueError):
        return False


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    name = argv[0] if len(argv) == 1 else None
    report = {"test": "T4.2 restore test", "repo": name, "ok": False, "not_tested": NOT_TESTED}
    try:
        if IMPORT_ERROR:
            raise RuntimeError("import failed: " + IMPORT_ERROR)
        if name not in REPOS:
            raise RestoreFailed("usage: restore_test.py %s" % "|".join(sorted(REPOS)))
        report = run(name)
    except (Exception, KeyboardInterrupt) as exc:  # every outcome ends in a report
        report["error"] = "%s: %s" % (type(exc).__name__, exc)
    if IMPORT_ERROR or name not in REPOS:
        return 2 if _emit(json.dumps(report, indent=1)) else 3
    try:
        out = write_report(name, report)
    except (Exception, KeyboardInterrupt) as exc:
        # The report could not be saved: print all of it, marked failed, so nothing is lost.
        report = dict(report, ok=False,
                      report_write_error="%s: %s" % (type(exc).__name__, exc))
        return 2 if _emit(json.dumps(report, indent=1)) else 3
    if not _emit(json.dumps({"ok": report["ok"], "out": str(out)})):
        return 3
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

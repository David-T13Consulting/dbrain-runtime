"""P2.7 step C1: the failure store and its audit log, with planted data only.

Everything runs in temporary folders: no real question is read or stored, and nothing is
written under ~/Library/Application Support/dbrain.
"""
import json
import os
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path

from dbrain import failures, outpath

PLANTED_Q = "PLANTED-Q-4e1d which bag does Zoe need on Friday"
PLANTED_A = "PLANTED-A-9b77 the blue bag"


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class StoreFixture(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="dbrain-failures-test-"))
        self.clock = Clock()
        self.store = failures.FailureStore(store=self.home / "failures",
                                           logs=self.home / "logs", clock=self.clock)

    def audit_lines(self):
        path = self.home / "logs" / failures.AUDIT_NAME
        return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []

    def stored(self):
        return sorted((self.home / "failures").glob("*.json"))


class CaptureTest(StoreFixture):
    def test_each_signal_is_captured_and_audited(self):
        cases = [(200, {"status": s, "answer": PLANTED_A}) for s in
                 ("unclear", "incomplete", "withheld", "malformed")]
        cases += [(503, {"status": "unavailable", "answer": ""}),
                  (504, {"status": "timeout", "answer": ""})]
        for code, payload in cases:
            with self.subTest(status=payload["status"]):
                self.assertIsNotNone(self.store.capture(PLANTED_Q, code, payload))
        self.assertEqual(len(self.stored()), len(cases))
        self.assertEqual([e["event"] for e in self.audit_lines()],
                         ["capturing", "captured"] * len(cases))
        self.assertEqual(sorted(e["signal"] for e in self.audit_lines() if e["event"] == "capturing"),
                         sorted(["unclear", "incomplete", "withheld", "malformed",
                                 "unavailable", "timeout"]))

    def test_no_signal_writes_nothing(self):
        for code, payload in ((200, {"status": "supported"}), (200, {"status": "paused"}),
                              (423, {"status": "paused"}), (200, {})):
            self.assertIsNone(self.store.capture(PLANTED_Q, code, payload))
        self.assertEqual(self.stored(), [])
        self.assertEqual(self.audit_lines(), [])

    def test_the_store_holds_the_text_and_is_private(self):
        fid = self.store.capture(PLANTED_Q, 200, {"status": "incomplete", "answer": PLANTED_A})
        path = self.home / "failures" / (fid + ".json")
        record = json.loads(path.read_text())
        self.assertEqual((record["question"], record["answer"]), (PLANTED_Q, PLANTED_A))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.home / "failures").stat().st_mode), 0o700)

    def test_the_audit_log_never_holds_question_or_answer_text(self):
        self.store.capture(PLANTED_Q, 200, {"status": "incomplete", "answer": PLANTED_A})
        self.clock.t += 91 * 86400
        self.store.purge()
        text = (self.home / "logs" / failures.AUDIT_NAME).read_text()
        for planted in ("PLANTED-Q-4e1d", "PLANTED-A-9b77", "Zoe", "blue bag"):
            self.assertNotIn(planted, text)
        self.assertEqual([e["event"] for e in self.audit_lines()],
                         ["capturing", "captured", "deleting", "deleted"])


class ConcurrencyAndRecoveryTest(StoreFixture):
    def test_concurrent_appends_never_corrupt_the_audit_log(self):
        def worker(n):
            for i in range(40):
                self.store._audit("probe", id="%d-%d" % (n, i))
        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        lines = (self.home / "logs" / failures.AUDIT_NAME).read_text().splitlines()
        self.assertEqual(len([l for l in lines if l.strip()]), 8 * 40)
        for line in lines:
            if line.strip():
                json.loads(line)              # every line a complete record

    def test_a_bad_entry_does_not_block_a_lost_deletion_from_being_reconciled(self):
        fid = self.store.capture(PLANTED_Q, 200, {"status": "withheld", "answer": ""})
        (self.home / "failures" / "bad.json").mkdir()      # unreadable as a record
        self.store._audit("deleting", id=fid, reason="retention 90 days")
        os.unlink(self.home / "failures" / (fid + ".json"))   # deleted, "deleted" record lost
        out = self.store.reconcile()
        self.assertEqual(out["deleted"], [fid])
        self.assertIn("reconcile_error", [e["event"] for e in self.audit_lines()])

    def test_an_incomplete_record_is_not_reconciled_as_captured(self):
        self.store.capture(PLANTED_Q, 200, {"status": "withheld", "answer": ""})   # creates folder
        for name, body in (("1-a.json", '{"id": "1-a"}'),
                           ("2-b.json", '{"id": "2-b", "ts": 1, "signal": "nope", "question": "", "answer": ""}')):
            (self.home / "failures" / name).write_text(body)
            os.chmod(self.home / "failures" / name, 0o600)
        self.assertEqual(self.store.reconcile()["captured"], [])

    def test_a_store_swapped_for_a_symlink_between_captures_is_refused(self):
        self.store.capture(PLANTED_Q, 200, {"status": "withheld", "answer": ""})
        other = self.home / "other"
        other.mkdir(mode=0o700)
        (self.home / "failures").rename(self.home / "failures-moved")
        (self.home / "failures").symlink_to(other)
        with self.assertRaises(outpath.OutsideVaultError):
            self.store.capture(PLANTED_Q, 200, {"status": "withheld", "answer": ""})
        self.assertEqual(list(other.iterdir()), [])

    def test_a_store_that_is_already_a_symlink_is_refused_at_construction(self):
        base = Path(tempfile.mkdtemp(prefix="dbrain-redirect-"))
        (base / "target").mkdir(mode=0o700)
        (base / "failures").symlink_to(base / "target")
        with self.assertRaises(outpath.OutsideVaultError):
            failures.FailureStore(store=base / "failures", logs=base / "logs")


class RoundFourRegressionTest(StoreFixture):
    def test_a_symlink_made_right_after_the_check_is_refused_not_saved(self):
        base = Path(tempfile.mkdtemp(prefix="dbrain-construct-race-"))
        target = base / "elsewhere"
        target.mkdir(mode=0o700)
        real_check = outpath.assert_outside_git
        swapped = []

        def check_then_swap(path):
            real = real_check(path)
            if Path(path).name == "failures" and not swapped:
                (base / "failures").mkdir(mode=0o700, exist_ok=True)
                (base / "failures").rename(base / "failures-old")
                (base / "failures").symlink_to(target)          # swapped after the check
                swapped.append(True)
            return real
        outpath.assert_outside_git = check_then_swap
        try:
            store = failures.FailureStore(store=base / "failures", logs=base / "logs")
        finally:
            outpath.assert_outside_git = real_check
        self.assertTrue(swapped)
        with self.assertRaises(outpath.OutsideVaultError):
            store.capture(PLANTED_Q, 200, {"status": "incomplete", "answer": ""})
        self.assertEqual(list(target.iterdir()), [])

    def test_a_reconcile_racing_a_capture_records_one_completion(self):
        real = self.store._audit
        racer = []

        def audit(event, **fields):
            if event == "captured" and not fields.get("reconciled") and not racer:
                t = threading.Thread(target=self.store.reconcile)
                t.start()
                racer.append(t)
                time.sleep(0.2)              # the reconcile waits for the store lock
            return real(event, **fields)
        self.store._audit = audit
        fid = self.store.capture(PLANTED_Q, 200, {"status": "withheld", "answer": ""})
        racer[0].join(5)
        self.store._audit = real
        done = [e for e in self.audit_lines() if e["event"] == "captured" and e["id"] == fid]
        self.assertEqual(len(done), 1)

    def test_records_missing_status_fields_are_not_reconciled(self):
        self.store.capture(PLANTED_Q, 200, {"status": "withheld", "answer": ""})   # creates folder
        base = {"ts": 1, "signal": "withheld", "question": "q", "answer": ""}
        for fid, extra in (("1-a", {}), ("2-b", {"status_code": "200", "status": "x"}),
                           ("3-c", {"status_code": 200, "status": 5}),
                           ("4-d", {"status_code": True, "status": "x"}),
                           ("5-e", {"status_code": 200})):          # status missing
            rec = dict(base, id=fid, **extra)
            (self.home / "failures" / (fid + ".json")).write_text(json.dumps(rec))
            os.chmod(self.home / "failures" / (fid + ".json"), 0o600)
        self.assertEqual(self.store.reconcile()["captured"], [])


class RetentionTest(StoreFixture):
    def test_a_failure_is_deleted_after_90_days_and_the_deletion_is_audited(self):
        old = self.store.capture(PLANTED_Q, 200, {"status": "withheld", "answer": ""})
        self.clock.t += 60 * 86400
        recent = self.store.capture(PLANTED_Q, 200, {"status": "withheld", "answer": ""})
        self.clock.t += 31 * 86400          # old is 91 days, recent 31 days
        self.assertEqual(self.store.purge(), [old])
        self.assertEqual([p.stem for p in self.stored()], [recent])
        deleted = [e for e in self.audit_lines() if e["event"] == "deleted"]
        self.assertEqual(len([e for e in self.audit_lines() if e["event"] == "deleting"]), 1)
        self.assertEqual([(e["id"], e["reason"]) for e in deleted], [(old, "retention 90 days")])

    def test_malformed_records_fall_back_to_file_age_and_the_sweep_goes_on(self):
        folder = self.home / "failures"
        self.store.capture(PLANTED_Q, 200, {"status": "withheld", "answer": ""})   # creates it
        old = self.clock.t - 200 * 86400
        for name, body in (("a.json", "[]"), ("b.json", '{"ts": "x"}'), ("c.json", '{"ts": NaN}'),
                           ("d.json", "not json")):
            (folder / name).write_text(body)
            os.chmod(folder / name, 0o600)
            os.utime(folder / name, (old, old))
        (folder / "e.json").mkdir()          # cannot be deleted with unlink: audited error
        deleted = self.store.purge()
        self.assertEqual(sorted(deleted), ["a", "b", "c", "d"])
        errors = [e for e in self.audit_lines() if e["event"] == "purge_error"]
        self.assertEqual([e["id"] for e in errors], ["e"])

    def test_a_lost_completion_record_is_reconciled(self):
        real = self.store._audit

        def flaky(event, **fields):
            if event == "captured":
                raise OSError("disk full")
            return real(event, **fields)
        self.store._audit = flaky
        with self.assertRaises(OSError):
            self.store.capture(PLANTED_Q, 200, {"status": "incomplete", "answer": ""})
        self.store._audit = real
        self.assertEqual(len(self.stored()), 1)
        self.assertEqual(self.store.reconcile(), {"captured": [self.stored()[0].stem], "deleted": []})
        events = [e["event"] for e in self.audit_lines()]
        self.assertEqual(events, ["capturing", "captured"])
        self.assertEqual(self.store.reconcile(), {"captured": [], "deleted": []})

    def test_a_lost_deletion_record_is_reconciled_from_the_store(self):
        fid = self.store.capture(PLANTED_Q, 200, {"status": "withheld", "answer": ""})
        self.clock.t += 91 * 86400
        real = self.store._audit

        def flaky(event, **fields):
            if event == "deleted":
                raise OSError("disk full")
            return real(event, **fields)
        self.store._audit = flaky
        self.assertEqual(self.store.purge(), [fid])      # unlinked; its "deleted" record lost
        self.store._audit = real
        self.assertEqual(self.stored(), [])
        self.assertEqual(self.store.reconcile()["deleted"], [fid])
        self.assertIn("deleted", [e["event"] for e in self.audit_lines()])
        self.assertEqual(self.store.reconcile(), {"captured": [], "deleted": []})

    def test_a_failed_unlink_is_not_reconciled_as_deleted(self):
        fid = self.store.capture(PLANTED_Q, 200, {"status": "withheld", "answer": ""})
        self.store._audit("deleting", id=fid, reason="retention 90 days")   # intent, no unlink
        self.assertEqual(self.store.reconcile()["deleted"], [])
        self.assertEqual([p.stem for p in self.stored()], [fid])

    def test_a_half_written_record_is_never_reconciled_and_leftovers_are_removed(self):
        self.store.capture(PLANTED_Q, 200, {"status": "withheld", "answer": ""})   # creates folder
        folder = self.home / "failures"
        (folder / "123-abc.json").write_text('{"id": "123-abc", "question": "trunc')
        os.chmod(folder / "123-abc.json", 0o600)
        (folder / (failures.TMP_PREFIX + "999-def")).write_text("partial")
        os.chmod(folder / (failures.TMP_PREFIX + "999-def"), 0o600)
        self.assertEqual(self.store.reconcile()["captured"], [])
        old = self.clock.t - 2 * 3600
        os.utime(folder / (failures.TMP_PREFIX + "999-def"), (old, old))
        self.store.purge()
        self.assertFalse((folder / (failures.TMP_PREFIX + "999-def")).exists())

    def test_a_half_written_audit_line_does_not_swallow_the_next_record(self):
        self.store.capture(PLANTED_Q, 200, {"status": "withheld", "answer": ""})
        log = self.home / "logs" / failures.AUDIT_NAME
        with open(log, "a") as fh:
            fh.write('{"ts": 1, "event": "capt')            # a fragment, no newline
        self.store.capture(PLANTED_Q, 200, {"status": "withheld", "answer": ""})
        events = [e["event"] for e in self.store._audit_events()]
        self.assertEqual(events, ["capturing", "captured"] * 2)

    def test_a_failed_intent_record_stores_nothing(self):
        def broken(event, **fields):
            raise OSError("disk full")
        self.store._audit = broken
        with self.assertRaises(OSError):
            self.store.capture(PLANTED_Q, 200, {"status": "incomplete", "answer": ""})
        self.assertEqual(self.stored(), [])

    def test_retention_runs_by_itself(self):
        self.store.capture(PLANTED_Q, 200, {"status": "withheld", "answer": ""})
        self.clock.t += 91 * 86400
        self.store.start_retention(interval=3600)
        for _ in range(100):
            if not self.stored():
                break
            time.sleep(0.02)
        self.assertEqual(self.stored(), [])

    def test_nothing_is_deleted_before_90_days(self):
        self.store.capture(PLANTED_Q, 200, {"status": "withheld", "answer": ""})
        self.clock.t += 89 * 86400
        self.assertEqual(self.store.purge(), [])
        self.assertEqual(len(self.stored()), 1)


class RoundOneC1RegressionTest(StoreFixture):
    """Step C1, review 1 (9 Oct): no silent double failure; durable intents; no FIFO hang."""

    def capture_stderr(self, fn):
        import io
        import sys as _sys
        saved, _sys.stderr = _sys.stderr, io.StringIO()
        try:
            result = fn()
            return result, _sys.stderr.getvalue()
        finally:
            _sys.stderr = saved

    def test_a_failure_whose_error_record_also_fails_is_reported_without_text(self):
        self.store.capture(PLANTED_Q, 200, {"status": "withheld", "answer": PLANTED_A})
        folder = self.home / "failures"
        (folder / "e.json").mkdir()                       # unreadable: purge error
        self.clock.t += 91 * 86400
        real = self.store._audit

        def broken_errors(event, **fields):
            if event.endswith("_error"):
                raise OSError("disk full")
            return real(event, **fields)
        self.store._audit = broken_errors
        for step, event in ((self.store.purge, "purge_error"), (self.store.reconcile, "reconcile_error")):
            with self.subTest(step=event):
                _, err = self.capture_stderr(step)
                self.assertIn("failure %s e not audited: OSError" % event, err)
                for planted in ("PLANTED-Q-4e1d", "PLANTED-A-9b77", "Zoe"):
                    self.assertNotIn(planted, err)

    def test_the_intent_is_synced_before_the_unlink_and_the_unlink_after(self):
        self.store.capture(PLANTED_Q, 200, {"status": "withheld", "answer": ""})
        self.clock.t += 91 * 86400
        calls = []
        real_sync, real_unlink, real_audit = failures._sync, os.unlink, self.store._audit

        def sync(fd):
            calls.append("sync")
            return real_sync(fd)

        def unlink(*a, **k):
            calls.append("unlink")
            return real_unlink(*a, **k)

        def audit(event, **fields):
            calls.append(event)
            return real_audit(event, **fields)
        failures._sync, os.unlink, self.store._audit = sync, unlink, audit
        try:
            self.assertEqual(len(self.store.purge()), 1)
        finally:
            failures._sync, os.unlink, self.store._audit = real_sync, real_unlink, real_audit
        i = calls.index("unlink")
        self.assertEqual(calls[i - 3:i], ["deleting", "sync", "sync"])   # log file, log folder
        self.assertEqual(calls[i + 1:i + 3], ["sync", "deleted"])        # store folder first

    def test_a_planted_fifo_does_not_block_the_sweep(self):
        old = self.store.capture(PLANTED_Q, 200, {"status": "withheld", "answer": ""})
        os.mkfifo(self.home / "failures" / "f.json", 0o600)
        self.clock.t += 91 * 86400
        done = []
        t = threading.Thread(target=lambda: done.append((self.store.reconcile(), self.store.purge())))
        t.daemon = True
        t.start()
        t.join(5)
        self.assertTrue(done, "the sweep blocked on a FIFO")
        self.assertEqual(done[0][1], [old])
        self.assertEqual([e["id"] for e in self.audit_lines() if e["event"] == "purge_error"], ["f"])


class BoundaryTest(unittest.TestCase):
    def test_a_store_inside_a_git_working_tree_is_refused(self):
        repo = Path(tempfile.mkdtemp(prefix="dbrain-git-test-"))
        (repo / ".git").mkdir()
        for kw in ({"store": repo / "ignored" / "failures"}, {"logs": repo / "logs"}):
            with self.subTest(**{k: str(v) for k, v in kw.items()}):
                with self.assertRaises(outpath.OutsideVaultError):
                    failures.FailureStore(**dict({"store": Path(tempfile.mkdtemp()),
                                                  "logs": Path(tempfile.mkdtemp())}, **kw))

    def test_a_store_inside_the_vault_is_refused(self):
        vault = Path(tempfile.mkdtemp(prefix="dbrain-vault-test-"))
        saved = outpath.config.VAULT
        outpath.config.VAULT = vault
        try:
            store = failures.FailureStore(store=vault / "failures", logs=Path(tempfile.mkdtemp()))
            with self.assertRaises(outpath.OutsideVaultError):
                store.capture(PLANTED_Q, 200, {"status": "incomplete", "answer": ""})
        finally:
            outpath.config.VAULT = saved
        self.assertFalse((vault / "failures").exists())

    def test_the_default_store_and_logs_are_separate_folders(self):
        # Paths only: nothing under the real Application Support folder is touched.
        self.assertNotEqual(failures.STORE, failures.LOGS)
        self.assertNotIn(failures.LOGS, [failures.STORE] + list(failures.STORE.parents))
        self.assertNotIn(failures.STORE, [failures.LOGS] + list(failures.LOGS.parents))
        self.assertEqual(failures.STORE.parent, failures.LOGS.parent)

    def test_a_git_entry_appearing_after_construction_is_refused_at_write_time(self):
        # A worktree-style `.git` FILE, created after the store object exists.
        base = Path(tempfile.mkdtemp(prefix="dbrain-git-race-"))
        store = failures.FailureStore(store=base / "s" / "failures", logs=base / "s" / "logs")
        (base / "s").mkdir(exist_ok=True)
        (base / "s" / ".git").write_text("gitdir: /elsewhere\n")
        with self.assertRaises(outpath.OutsideVaultError):
            store.capture(PLANTED_Q, 200, {"status": "incomplete", "answer": ""})
        self.assertFalse((base / "s" / "failures").exists())

    def test_a_git_folder_reached_through_another_letter_case_is_refused(self):
        base = Path(tempfile.mkdtemp(prefix="dbrain-case-"))
        (base / "Repo").mkdir()
        (base / "Repo" / ".git").mkdir()
        alias = base / "repo"
        if not alias.exists():
            self.skipTest("case-sensitive volume: no case alias to test")
        # Skip the constructor's early check, so the write-time check on the descriptor chain
        # is what is tested. Real paths (a temporary folder sits under the /var symlink),
        # and the lock the constructor would create.
        real_base = Path(os.path.realpath(str(base)))
        store = failures.FailureStore.__new__(failures.FailureStore)
        store.store, store.logs, store.clock = real_base / "repo" / "failures", real_base / "logs", time.time
        store._lock = threading.Lock()
        with self.assertRaises(outpath.OutsideVaultError):
            store.capture(PLANTED_Q, 200, {"status": "incomplete", "answer": ""})
        self.assertFalse((base / "Repo" / "failures").exists())
        self.assertFalse((real_base / "logs").exists() and
                         "captured" in (real_base / "logs" / failures.AUDIT_NAME).read_text())

    def test_a_symlink_swapped_in_for_the_store_is_refused_by_purge(self):
        base = Path(tempfile.mkdtemp(prefix="dbrain-swap-"))
        clock = Clock()
        store = failures.FailureStore(store=base / "failures", logs=base / "logs", clock=clock)
        store.capture(PLANTED_Q, 200, {"status": "incomplete", "answer": ""})
        other = base / "elsewhere"
        other.mkdir(mode=0o700)
        (other / "keep.json").write_text('{"ts": 0}')
        (base / "failures").rename(base / "failures-moved")
        (base / "failures").symlink_to(other)
        clock.t += 365 * 86400
        with self.assertRaises(outpath.OutsideVaultError):
            store.purge()
        self.assertTrue((other / "keep.json").exists())

    def test_an_existing_folder_open_to_others_is_refused(self):
        base = Path(tempfile.mkdtemp(prefix="dbrain-perm-"))
        (base / "failures").mkdir(mode=0o755)
        os.chmod(base / "failures", 0o755)
        store = failures.FailureStore(store=base / "failures", logs=base / "logs")
        with self.assertRaises(outpath.OutsideVaultError):
            store.capture(PLANTED_Q, 200, {"status": "incomplete", "answer": ""})
        self.assertEqual(list((base / "failures").iterdir()), [])

    def test_an_existing_audit_log_open_to_others_is_refused_and_nothing_is_stored(self):
        base = Path(tempfile.mkdtemp(prefix="dbrain-perm-log-"))
        (base / "logs").mkdir(mode=0o700)
        (base / "logs" / failures.AUDIT_NAME).write_text("")
        os.chmod(base / "logs" / failures.AUDIT_NAME, 0o644)
        store = failures.FailureStore(store=base / "failures", logs=base / "logs")
        with self.assertRaises(outpath.OutsideVaultError):
            store.capture(PLANTED_Q, 200, {"status": "incomplete", "answer": ""})
        self.assertFalse((base / "failures").exists())


if __name__ == "__main__":
    unittest.main()

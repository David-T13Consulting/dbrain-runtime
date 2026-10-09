import unittest

from dbrain import adapter, answer, config, index, scope
from tests import fake_vault


class VerdictTest(unittest.TestCase):
    def setUp(self):
        self.vault, _, _ = fake_vault.build()
        self.profile = fake_vault.PROFILE

    def ask(self, question, reply=None, verifier=None):
        model = adapter.StubAdapter(reply=reply)
        result = answer.ask(question, profile=self.profile, model=model, vault=self.vault,
                            verifier=verifier)
        return result, model

    @staticmethod
    def confirming_verifier():
        """Stands in for the entailment check this module does not implement, so the
        plumbing around `supported` can still be tested."""
        return lambda text, chunks: True

    def test_no_evidence_withholds_and_never_calls_the_model(self):
        result, model = self.ask("What is the capital of France?")
        self.assertEqual(result.status, answer.WITHHELD)
        self.assertEqual(result.citations, [])
        self.assertEqual(model.calls, [], "the model was called with nothing to ground on")

    def test_grounded_answer_with_valid_citation_is_supported_given_a_verifier(self):
        result, _ = self.ask("What does tempo mean?", reply="Marathon pace or faster [E1]",
                             verifier=self.confirming_verifier())
        self.assertEqual(result.status, answer.SUPPORTED)
        self.assertTrue(result.citations)
        self.assertRegex(result.citations[0], r"\.md:\d+-\d+$")

    def test_answer_citing_nothing_is_incomplete_not_supported(self):
        result, _ = self.ask("What does tempo mean?", reply="Marathon pace or faster.")
        self.assertEqual(result.status, answer.INCOMPLETE)
        self.assertEqual(result.citations, [])

    def test_invented_citation_is_rejected_and_downgrades_the_verdict(self):
        result, _ = self.ask("What does tempo mean?", reply="Says so [E1] and also [E99]")
        self.assertEqual(result.status, answer.INCOMPLETE)
        self.assertIn("E99", result.invalid)

    def test_a_bare_citation_with_no_answer_text_is_not_supported(self):
        """An answer that is only a citation is not an answer."""
        result, _ = self.ask("What does tempo mean?", reply="[E1]")
        self.assertEqual(result.status, answer.INCOMPLETE)

    def test_citations_name_a_path_and_line_range(self):
        result, _ = self.ask("What does tempo mean?",
                             reply="Tempo means marathon pace or faster [E1]",
                             verifier=self.confirming_verifier())
        self.assertEqual(result.status, answer.SUPPORTED)
        path, lines = result.citations[0].rsplit(":", 1)
        self.assertTrue(path.endswith(".md"))
        start, end = lines.split("-")
        self.assertLessEqual(int(start), int(end))

    def test_evidence_given_to_the_model_comes_only_from_the_allowlist(self):
        result, model = self.ask("What does tempo mean?", reply="[E1]")
        prompt = model.calls[0]
        self.assertNotIn("Not in any allowlist", prompt)   # 90 System/secrets.md
        self.assertNotIn("Read-only unless asked", prompt)  # vault-root AGENTS.md


class ReasonCodeTest(unittest.TestCase):
    """P2.7 step C1b (D113): every verdict carries a fixed reason code, so a cited and
    grounded but unverified answer is told apart from a real defect. Status and readable
    reason stay exactly as before."""

    def setUp(self):
        self.vault, _, _ = fake_vault.build()
        self.profile = fake_vault.PROFILE

    def ask(self, reply=None, question="What does tempo mean?", verifier=None):
        return answer.ask(question, profile=self.profile, vault=self.vault,
                          model=adapter.StubAdapter(reply=reply), verifier=verifier)

    def test_each_path_gets_its_code_and_keeps_its_status(self):
        cases = [
            (dict(question="What is the capital of France?"), answer.WITHHELD, answer.NO_EVIDENCE),
            (dict(reply="The evidence does not say [E1]"), answer.WITHHELD, answer.DECLINED),
            (dict(reply="Says so [E1] and also [E99]"), answer.INCOMPLETE, answer.INVALID_CITATION),
            (dict(reply="Marathon pace or faster."), answer.INCOMPLETE, answer.NO_CITATION),
            (dict(reply="The moon is made of cheese [E1]"), answer.INCOMPLETE, answer.LOW_GROUNDING),
            (dict(reply="Tempo means marathon pace or faster [E1]"), answer.INCOMPLETE, answer.UNVERIFIED),
            (dict(reply="Tempo means marathon pace or faster [E1]", verifier=lambda t, c: False),
             answer.INCOMPLETE, answer.VERIFIER_REJECTED),
            (dict(reply="Tempo means marathon pace or faster [E1]", verifier=lambda t, c: True),
             answer.SUPPORTED, answer.VERIFIED),
        ]
        for kwargs, status, code in cases:
            with self.subTest(code=code):
                result = self.ask(**kwargs)
                self.assertEqual((result.status, result.reason_code), (status, code))
                self.assertIsInstance(result.reason_code, str)

    def test_the_codes_are_these_literal_strings(self):
        """The codes are a contract with the capture step (C2) and the audit counts: pin
        them as literals, not through the constants they are compared with."""
        self.assertEqual(
            (answer.NO_EVIDENCE, answer.DECLINED, answer.INVALID_CITATION, answer.NO_CITATION,
             answer.LOW_GROUNDING, answer.UNVERIFIED, answer.VERIFIER_REJECTED,
             answer.VERIFIED, answer.MODEL_UNAVAILABLE),
            ("no_evidence", "declined", "invalid_citation", "no_citation", "low_grounding",
             "unverified", "verifier_rejected", "verified", "model_unavailable"))
        self.assertEqual(self.ask(reply="The evidence does not say [E1]").reason_code, "declined")
        self.assertEqual(self.ask(reply="Tempo means marathon pace or faster [E1]").reason_code,
                         "unverified")

    def test_unverified_is_not_a_defect_and_the_real_defects_are(self):
        self.assertNotIn(answer.UNVERIFIED, answer.DEFECTS)
        self.assertEqual(answer.DEFECTS, {answer.INVALID_CITATION, answer.NO_CITATION,
                                          answer.LOW_GROUNDING, answer.VERIFIER_REJECTED})

    def test_the_readable_reason_is_unchanged(self):
        """The phone's status line is derived from the readable reason (serve.plain_status),
        so the reason text must stay what it was."""
        self.assertIn("entailment unverified",
                      self.ask(reply="Tempo means marathon pace or faster [E1]").reason)
        self.assertEqual(self.ask(reply="Marathon pace or faster.").reason,
                         "answered without citing any evidence")
        self.assertEqual(self.ask(reply="Says so [E1] and also [E99]").reason,
                         "cited an id that was never offered: E99")

    def test_a_model_outage_carries_its_code_on_the_partial_result(self):
        class Down(adapter.StubAdapter):
            def generate(self, prompt, max_tokens=320):
                raise adapter.ModelUnavailable("down")
        with self.assertRaises(adapter.ModelUnavailable) as ctx:
            answer.ask("What does tempo mean?", profile=self.profile, vault=self.vault,
                       model=Down())
        self.assertEqual((ctx.exception.partial.status, ctx.exception.partial.reason_code),
                         (answer.FAILED, answer.MODEL_UNAVAILABLE))


class GroundingTest(unittest.TestCase):
    """A valid citation alone must not earn `supported`: the earlier version classified
    "The moon is made of cheese [E1]" as supported, which is the failure this guards."""

    def setUp(self):
        self.vault, _, _ = fake_vault.build()
        self.profile = fake_vault.PROFILE

    def ask(self, reply, question="What does tempo mean?"):
        return answer.ask(question, profile=self.profile,
                          model=adapter.StubAdapter(reply=reply), vault=self.vault)

    def test_an_offtopic_answer_citing_real_evidence_is_not_supported(self):
        for reply in (
            "The moon is made of cheese [E1]",
            "Bitcoin reached a new all-time high in Tokyo yesterday [E1]",
            "David should buy a Mac mini with 32 GB of unified memory [E1]",
        ):
            result = self.ask(reply)
            self.assertNotEqual(result.status, answer.SUPPORTED, reply)
            self.assertEqual(result.grounding, 0.0, reply)

    def test_a_model_declining_is_reported_as_withheld_not_as_an_answer(self):
        for reply in (
            "Insufficient evidence to answer [E1]",
            "The evidence does not say [E1]",
            "I don't know [E1]",
            "Not stated in the notes [E1]",
        ):
            result = self.ask(reply)
            self.assertEqual(result.status, answer.WITHHELD, reply)

    def test_a_faithful_paraphrase_is_grounded_but_not_claimed_as_supported(self):
        """Without a verifier the service must not claim support, even for a good answer."""
        for reply in (
            "Tempo means marathon pace or faster [E1]",
            "A tempo session is continuous work at marathon pace or faster [E1]",
        ):
            result = self.ask(reply)
            self.assertEqual(result.status, answer.INCOMPLETE, reply)
            self.assertIn("entailment unverified", result.reason)

    def test_supported_is_reachable_only_with_a_verifier(self):
        reply = "Tempo means marathon pace or faster [E1]"
        self.assertEqual(self.ask(reply).status, answer.INCOMPLETE)
        with_verifier = answer.ask(
            "What does tempo mean?", profile=self.profile, vault=self.vault,
            model=adapter.StubAdapter(reply=reply), verifier=lambda text, chunks: True,
        )
        self.assertEqual(with_verifier.status, answer.SUPPORTED)
        self.assertIn("entailment confirmed", with_verifier.reason)

    def test_a_verifier_that_rejects_downgrades_the_verdict(self):
        result = answer.ask(
            "What does tempo mean?", profile=self.profile, vault=self.vault,
            model=adapter.StubAdapter(reply="Tempo means marathon pace or faster [E1]"),
            verifier=lambda text, chunks: False,
        )
        self.assertEqual(result.status, answer.INCOMPLETE)
        self.assertIn("verifier rejected", result.reason)

    def test_a_contradiction_is_never_supported_even_with_high_overlap(self):
        """The case a lexical check cannot catch: same words, opposite meaning. It is kept
        out of `supported` by requiring a verifier, not by detecting the contradiction."""
        result = self.ask("Tempo means marathon pace or slower [E1]")
        self.assertNotEqual(result.status, answer.SUPPORTED)

    def test_the_verdict_carries_a_reason(self):
        self.assertIn("overlap", self.ask("Tempo means marathon pace or faster [E1]").reason)
        self.assertIn("threshold", self.ask("The moon is made of cheese [E1]").reason)

    def test_a_verifier_can_be_fooled_by_nothing_in_this_module(self):
        """The limitation is pinned where it belongs: in the docstring, and in the fact
        that `supported` cannot be produced here at all."""
        self.assertIn("unreachable unless a verifier", answer.__doc__ or "")

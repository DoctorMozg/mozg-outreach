from mozg_outreach.letters.checks import LetterChecker
from mozg_outreach.letters.frame import LetterFrame
from mozg_outreach.letters.models import LetterBrief

BODY = (
    "Congrats on the 2024 Series B for Katana. Growing teams often see AWS bills climb "
    "faster than headcount, and that is where we help.\n\n"
    "Worth comparing notes on your setup?"
)


def kinds(checker: LetterChecker, subject: str | None, text: str) -> set[str]:
    return {p.kind for p in checker.check(subject, text)}


class TestCheck:
    def test_clean_letter_passes(self, email_brief: LetterBrief, email_frame: LetterFrame) -> None:
        checker = LetterChecker(email_brief, email_frame)
        assert checker.check("Katana's AWS bill", email_frame.assemble(BODY)) == []

    def test_subject_rules(self, email_brief: LetterBrief, email_frame: LetterFrame) -> None:
        checker = LetterChecker(email_brief, email_frame)
        text = email_frame.assemble(BODY)
        assert kinds(checker, None, text) == {"subject"}
        assert kinds(checker, " ".join(["x" * 14] * 4), text) == {"subject"}
        dm = LetterChecker(email_brief.model_copy(update={"subject_max_chars": None}), email_frame)
        assert kinds(dm, "Hello", text) == {"subject"}

    def test_broken_frame_budget_and_punctuation(
        self, email_brief: LetterBrief, email_frame: LetterFrame
    ) -> None:
        checker = LetterChecker(email_brief, email_frame)
        text = f"Hey Jane,\n\n{BODY} — {'word ' * 40}\n\nCheers,\nStan"
        assert kinds(checker, "Katana's AWS bill", text) == {"frame", "word_budget", "punctuation"}

    def test_markdown_body_is_flagged(
        self, email_brief: LetterBrief, email_frame: LetterFrame
    ) -> None:
        checker = LetterChecker(email_brief, email_frame)
        text = email_frame.assemble(BODY + "\n\n- one point\n- another point")
        assert kinds(checker, "Katana's AWS bill", text) == {"markdown"}


class TestEvidence:
    def test_numbers_must_come_from_evidence_except_call_length(
        self, email_brief: LetterBrief, email_frame: LetterFrame
    ) -> None:
        checker = LetterChecker(email_brief, email_frame)
        body = "Katana raised $20M in 2024 and cut costs by 37% across 3 regions. A 20 min call?"
        assert checker.unsupported_numbers("Saving 12 hours", body) == ["3", "37"]
        # "202" is part of 2024 in the evidence but not a number there.
        assert checker.unsupported_numbers(None, "Up 202 seats since 2024.") == ["202"]
        assert checker.unsupported_numbers(None, "B2B teams on EC2 in Q3 grew 14M.") == ["14"]

    def test_unverified_names_are_listed_for_review(
        self, email_brief: LetterBrief, email_frame: LetterFrame
    ) -> None:
        checker = LetterChecker(email_brief, email_frame)
        body = (
            "Saw that Katana's team moved to AWS. We did the same at Wise and Bolt. "
            "I think Tuesday works."
        )
        assert checker.unverified_names(body) == ["Bolt", "Wise"]

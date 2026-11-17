import pytest

from mozg_outreach.letters.checks import LetterChecker
from mozg_outreach.letters.cold import ColdRules, reading_grade
from mozg_outreach.letters.frame import LetterFrame
from mozg_outreach.letters.models import LetterBrief, TouchContext, TouchPurpose
from tests.letters.test_checks import kinds

OPENER = (
    "Congrats on the Series B for Katana. Growing teams often see the AWS bill climb faster "
    "than the team.\n\nWorth a look at how your setup compares?"
)
FOLLOW_UP = "Katana is hiring a platform engineer. That seat often stays open for months."


def rules(brief: LetterBrief, purpose: TouchPurpose | None = None) -> ColdRules:
    if purpose is None:
        return ColdRules(brief)
    touch = TouchContext(
        step=2, total=3, day=3, purpose=purpose, talking_points="", in_thread=True, earlier=[]
    )
    return ColdRules(brief.model_copy(update={"touch": touch}))


class TestHardRules:
    def test_a_clean_opener_passes(self, email_brief: LetterBrief) -> None:
        assert rules(email_brief).problems("Katana's AWS bill", OPENER) == []

    @pytest.mark.parametrize(
        ("subject", "problem"),
        [
            ("AWS", "subject has 1 words, keep it to 2-4"),
            ("Katana's AWS bill after the round", "subject has 6 words, keep it to 2-4"),
            ("AWS bill?", "subject has a question mark or a digit"),
            ("Katana's 2024 bill", "subject has a question mark or a digit"),
        ],
    )
    def test_subject_is_two_to_four_plain_words(
        self, email_brief: LetterBrief, subject: str, problem: str
    ) -> None:
        assert rules(email_brief).problems(subject, OPENER) == [problem]

    @pytest.mark.parametrize(
        ("first", "problem"),
        [
            ("I hope you are well.", "first sentence starts with 'i hope'; open on the reader"),
            ("We help SaaS teams.", "first sentence starts with 'we'; open on the reader"),
            (
                "Congrats on the round " + "and the hires " * 6 + "this year.",
                "first sentence has 24 words, at most 20",
            ),
        ],
    )
    def test_first_sentence_is_short_and_about_the_reader(
        self, email_brief: LetterBrief, first: str, problem: str
    ) -> None:
        body = f"{first} Worth a look at your setup?"
        assert rules(email_brief).problems(None, body) == [problem]

    def test_an_opener_asks_exactly_one_question(self, email_brief: LetterBrief) -> None:
        body = OPENER + " Or should I ask someone else?"
        assert rules(email_brief).problems(None, body) == [
            "an opener asks exactly one question, this has 2"
        ]
        assert rules(email_brief).problems(None, "Congrats on the round.") == [
            "an opener asks exactly one question, this has 0"
        ]

    def test_a_first_letter_never_asks_for_a_meeting(self, email_brief: LetterBrief) -> None:
        body = "Congrats on the round. Free for a 15-minute call on Tuesday?"
        [problem] = rules(email_brief).problems(None, body)
        assert problem.startswith("a first letter asks about interest, not for a meeting")
        # From a follow-up on, a call is a fair ask.
        assert rules(email_brief, "follow_up").problems(None, body) == []

    def test_a_follow_up_never_just_bumps(self, email_brief: LetterBrief) -> None:
        body = f"Just following up on my note. {FOLLOW_UP}"
        assert rules(email_brief, "follow_up").problems(None, body) == [
            "a follow-up brings something new, never 'just following up'"
        ]
        assert rules(email_brief, "breakup").problems(None, FOLLOW_UP) == []

    def test_cold_rules_reach_email_and_dm_in_english_only(
        self, email_brief: LetterBrief, email_frame: LetterFrame
    ) -> None:
        text = email_frame.assemble("We help teams. " + OPENER)
        assert "cold_rule" in kinds(LetterChecker(email_brief, email_frame), "AWS bill", text)
        cover = email_brief.model_copy(update={"channel": "cover_letter"})
        assert "cold_rule" not in kinds(LetterChecker(cover, email_frame), "AWS bill", text)
        german = email_brief.model_copy(update={"language": "de"})
        assert "cold_rule" not in kinds(LetterChecker(german, email_frame), "AWS bill", text)


class TestWarnings:
    def test_a_plain_short_letter_has_none(self, email_brief: LetterBrief) -> None:
        assert rules(email_brief).warnings(OPENER) == []

    def test_dense_writing_is_flagged(self, email_brief: LetterBrief) -> None:
        body = (
            "Organizational infrastructure modernization initiatives frequently necessitate "
            "comprehensive architectural reconsideration across interdependent operational "
            "environments, particularly when international expansion and regulatory obligations "
            "accelerate rather unexpectedly."
        )
        notes = rules(email_brief).warnings(body)
        assert any(n.startswith("Reads at grade") for n in notes)
        assert any(n.startswith("1 sentence(s) over 22 words") for n in notes)

    def test_sender_talk_facts_and_hype_are_flagged(self, email_brief: LetterBrief) -> None:
        body = (
            "We are excited! Our team and our partners help. Katana raised $20M in 2024. "
            "Katana runs on AWS. Katana closed a Series B. Is your setup ready?"
        )
        notes = rules(email_brief).warnings(body)
        assert "Talks about the sender more than the reader (3 to 1)." in notes
        assert "3 sentences carry facts; one or two land better than a list." in notes
        assert "Enthusiasm reads as a pitch: excited" in notes


def test_reading_grade_tracks_plain_against_dense_text() -> None:
    assert reading_grade("The cat sat on the mat. It was warm.") < 2
    assert reading_grade("Comprehensive organizational modernization necessitates investment.") > 15
    assert reading_grade("") == 0.0

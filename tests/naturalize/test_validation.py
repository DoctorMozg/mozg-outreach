import pytest

from mozg_outreach.naturalize.models import NaturalizeInput, NaturalizeResult, WordBudget
from mozg_outreach.naturalize.validation import RewriteValidator, body_word_count, word_count

ORIGINAL = (
    "Hi Anna,\n\n"
    "I saw Katana raised a Series B extension in October. We help SaaS teams in Tallinn "
    "cut cloud spend by 35% in 8 weeks without slowing releases, and it is a fixed-price "
    "engagement. Details are at https://example.com/case. Would a 20-minute call next week "
    "be useful to compare notes on your AWS setup?\n\n"
    "Thanks,\nMax"
)
# Same frame, entities and length within 5%; only wording changed.
GOOD = ORIGINAL.replace("I saw Katana raised", "I noticed Katana closed").replace(
    "and it is a fixed-price engagement", "and the engagement is fixed-price"
)


@pytest.fixture
def validator() -> RewriteValidator:
    request = NaturalizeInput(
        text=ORIGINAL,
        channel="email",
        severity="light",
        length_tolerance_pct=5,
        word_budget=WordBudget(min=40, max=180),
        preserve=["Katana", "Series B"],
        allowed_punctuation=[],
        voice_profile=None,
        exemplars=[],
    )
    return RewriteValidator(request)


def result(text: str, *needed: str) -> NaturalizeResult:
    return NaturalizeResult(
        text=text, changes=["Plainer verbs."], specificity_needed=list(needed), concerns=[]
    )


def test_clean_rewrite_is_kept(validator: RewriteValidator) -> None:
    outcome = validator.accept(result(GOOD))
    assert not outcome.reverted
    assert outcome.text == GOOD
    assert outcome.unresolved == []
    assert outcome.changes == ["Plainer verbs."]


class TestRevert:
    @pytest.mark.parametrize(
        ("text", "key"),
        [
            (ORIGINAL.replace(" without slowing releases", ""), "length_drift"),
            (GOOD.replace("35%", "a third"), "dropped:35%"),
            (GOOD.replace("Katana", "your company"), "dropped:Katana"),
            (
                GOOD.replace("https://example.com/case", "our site"),
                "dropped:https://example.com/case",
            ),
            (GOOD.replace("Hi Anna,", "Hello Anna,"), "frame:salutation"),
            (GOOD.replace("Thanks,\nMax", "Best,\nMax"), "frame:sign_off"),
            (GOOD.replace(", and the", " \u2014 the"), "punctuation:em_dash"),
        ],
    )
    def test_damage_restores_original(
        self, validator: RewriteValidator, text: str, key: str
    ) -> None:
        outcome = validator.accept(result(text))
        assert outcome.reverted
        assert outcome.text == ORIGINAL
        assert outcome.changes == []
        assert key in [p.key for p in outcome.damage]

    def test_problem_already_in_original_is_not_damage(self) -> None:
        dirty = ORIGINAL.replace(", and it is", "; it is")
        request = NaturalizeInput.model_validate(
            {
                "text": dirty,
                "channel": "email",
                "severity": "light",
                "length_tolerance_pct": 5,
                "word_budget": None,
                "preserve": [],
                "allowed_punctuation": [],
                "voice_profile": None,
                "exemplars": [],
            }
        )
        outcome = RewriteValidator(request).accept(result(dirty.replace("I saw", "I noticed")))
        assert not outcome.reverted
        assert [p.key for p in outcome.unresolved] == ["punctuation:semicolon"]


class TestCleanup:
    def test_curly_quotes_are_straightened_not_reverted(self, validator: RewriteValidator) -> None:
        outcome = validator.accept(result(GOOD.replace("I noticed", "I\u2019ve noticed")))
        assert not outcome.reverted
        assert "I've noticed" in outcome.text

    def test_markers_move_to_review_notes(self, validator: RewriteValidator) -> None:
        marked = GOOD.replace("in 8 weeks", "in 8 weeks [SPECIFICITY NEEDED]")
        outcome = validator.accept(result(marked, "Which client saw the 35% cut?"))
        assert not outcome.reverted
        assert "[SPECIFICITY NEEDED]" not in outcome.text
        assert outcome.text == GOOD
        assert outcome.review_notes[0] == "Needs a real fact: Which client saw the 35% cut?"
        assert "cut cloud spend by 35% in 8 weeks" in outcome.review_notes[1]


def test_word_count_treats_hyphens_and_apostrophes_as_one_word() -> None:
    assert word_count("It's a fixed-price, 20-minute call.") == 5


def test_budget_counts_the_body_only() -> None:
    assert body_word_count("Hi Anna,\n\nOne two three.\n\nThanks,\nMax") == 3
    assert body_word_count("Just one line here.") == 4

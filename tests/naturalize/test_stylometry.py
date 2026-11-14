import json
from pathlib import Path

import pytest

from mozg_outreach.naturalize.stylometry import StyleAnalyzer, StyleFeatures

FIXTURES = Path(__file__).parent / "fixtures"


def texts(name: str) -> list[str]:
    loaded: list[str] = json.loads((FIXTURES / f"{name}.json").read_text())
    return loaded


@pytest.fixture(scope="module")
def casual() -> StyleFeatures:
    return StyleAnalyzer().features(texts("casual"))


@pytest.fixture(scope="module")
def formal() -> StyleFeatures:
    return StyleAnalyzer().features(texts("formal"))


class TestFeatures:
    def test_frame_habits(self, casual: StyleFeatures, formal: StyleFeatures) -> None:
        assert casual.greetings == {"hey": 3, "hi": 1}
        assert casual.sign_offs == {"thanks": 3, "cheers": 1}
        assert formal.greetings == {"dear": 3}
        assert formal.sign_offs == {"kind regards": 3}

    def test_casual_and_formal_habits_differ(
        self, casual: StyleFeatures, formal: StyleFeatures
    ) -> None:
        assert casual.contractions_per_100 > 5 > formal.contractions_per_100
        assert casual.questions_per_100 > formal.questions_per_100 == 0
        assert casual.exclamations_per_100 > 0 == formal.exclamations_per_100
        assert casual.sentence_words_mean < formal.sentence_words_mean
        assert formal.final_period_rate == 1.0

    def test_frame_lines_are_not_sentences(self) -> None:
        features = StyleAnalyzer().features(["Hi Ann,\n\nOne two three four.\n\nThanks,\nMax"])
        assert features.sentence_words_mean == 4.0

    def test_empty_input_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least one text"):
            StyleAnalyzer().features([])


class TestDistance:
    def test_same_style_is_closer_than_other_style(
        self, casual: StyleFeatures, formal: StyleFeatures
    ) -> None:
        all_casual = texts("casual")
        held_in = StyleAnalyzer().features(all_casual[:2])
        held_out = StyleAnalyzer().features(all_casual[2:])
        same = StyleAnalyzer.distance(held_in, held_out)
        other = StyleAnalyzer.distance(held_in, formal)
        assert same < other

    def test_identical_is_zero_and_bounded(
        self, casual: StyleFeatures, formal: StyleFeatures
    ) -> None:
        assert StyleAnalyzer.distance(casual, casual) == 0.0
        assert 0.0 < StyleAnalyzer.distance(casual, formal) <= 1.0

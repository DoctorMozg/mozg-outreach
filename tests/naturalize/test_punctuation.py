import pytest

from mozg_outreach.naturalize.punctuation import PunctuationChecker, straighten_quotes


def names(text: str, checker: PunctuationChecker | None = None) -> list[str]:
    return [f"{i.line}:{i.name}" for i in (checker or PunctuationChecker()).check(text)]


class TestBans:
    @pytest.mark.parametrize(
        ("char", "name"),
        [
            ("\u2014", "em_dash"),
            ("\u2013", "en_dash"),
            ("\u201cquoted\u201d", "curly_quote"),
            ("it\u2019s", "curly_quote"),
            ("\u2026", "ellipsis"),
            ("\u2192", "arrow"),
            (";", "semicolon"),
            ("a - b", "spaced_hyphen"),
            ("a -- b", "double_hyphen"),
            ("wait...", "three_dots"),
            ("\u00ab", "non_ascii"),
            ("a\u00a0b", "non_ascii"),
        ],
    )
    def test_each_kind_is_reported(self, char: str, name: str) -> None:
        assert names(f"Clean line.\nIt works {char} mostly.\n") == [f"2:{name}"]

    def test_each_kind_once_per_line(self) -> None:
        assert names("One; two; three \u2014 four \u2014 five.") == ["1:em_dash", "1:semicolon"]

    def test_quoted_text_is_not_exempt(self) -> None:
        assert names('He said "wait \u2014 no".') == ["1:em_dash"]

    def test_clean_ascii_letter(self) -> None:
        text = "Hi Anna,\n\nWe cut AWS spend 35% (50-300 people, 50 - 300 too).\n\nThanks,\nMax"
        assert names(text) == []


class TestExemptions:
    def test_names_and_currency_are_fine(self) -> None:
        assert names("Marilin M\u00e4gi raised \u20ac14M.") == []

    def test_urls_emails_and_preserved_terms(self) -> None:
        checker = PunctuationChecker(preserve=["Fast\u2192Lane"])
        text = "See https://x.io/a--b;c or max--ops@x.io about Fast\u2192Lane."
        assert names(text, checker) == []

    def test_preserved_term_does_not_hide_the_rest(self) -> None:
        checker = PunctuationChecker(preserve=["Fast\u2192Lane"])
        assert names("Fast\u2192Lane is new; try it.", checker) == ["1:semicolon"]

    def test_allowed_soft_ban(self) -> None:
        checker = PunctuationChecker(allowed=["semicolon"])
        assert names("Short; sweet \u2014 done.", checker) == ["1:em_dash"]


def test_straighten_quotes_is_ascii() -> None:
    assert straighten_quotes("\u201cIt\u2019s\u201d\u00a0ok") == '"It\'s" ok'

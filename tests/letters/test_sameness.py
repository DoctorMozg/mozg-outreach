from mozg_outreach.letters.sameness import Phrasing, echoes

ACME = (
    "Hello Ann,\n\nCongrats on opening the Abu Dhabi clinic in March. A second site usually "
    "doubles the intake calls.\n\nWorth a look at how other clinics handle that?\n\n"
    "Best regards,\nStan"
)

RECENT = (
    Phrasing(company="Bolt", opening="Saw the new warehouse.", ask=None),
    Phrasing(
        company="Acme",
        opening="Congrats on opening the Abu Dhabi clinic in March.",
        ask="Worth a look at how other clinics handle that?",
    ),
)


class TestPhrasing:
    def test_reads_the_opening_and_the_last_question(self) -> None:
        phrasing = Phrasing.of("Acme", ACME)
        assert phrasing == Phrasing(
            company="Acme",
            opening="Congrats on opening the Abu Dhabi clinic in March.",
            ask="Worth a look at how other clinics handle that?",
        )

    def test_a_letter_with_only_a_salutation_has_none(self) -> None:
        assert Phrasing.of("Acme", "Hello Ann,") is None


class TestEchoes:
    def test_a_near_copy_names_the_company_it_echoes(self) -> None:
        letter = ACME.replace("Abu Dhabi", "Sharjah").replace("Ann", "Bo")
        notes = echoes(RECENT, letter)

        assert [n.split(":")[0] for n in notes] == [
            "The opening is close to the letter to Acme",
            "The ask is close to the letter to Acme",
        ]

    def test_a_different_letter_is_fine(self) -> None:
        letter = (
            "Hi Bo,\n\nYour team posted three intake roles this week. That is a lot of phone time."
            "\n\nWould a short note on routing those calls help?\n\nThanks,\nStan"
        )
        assert echoes(RECENT, letter) == []

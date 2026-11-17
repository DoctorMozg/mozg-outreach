import httpx
import pytest
from pydantic import SecretStr

from mozg_outreach.pipelines.biz.profile_search import (
    BraveProfiles,
    ProfileHit,
    ProfileSearchError,
    best_profile,
    names_person,
)

RESULTS = [
    # Same surname, another person.
    {
        "url": "https://ae.linkedin.com/in/omar-al-yousuf",
        "title": "Omar Al Yousuf - Al-Futtaim | LinkedIn",
        "description": "",
    },
    # Right person, another employer.
    {
        "url": "https://www.linkedin.com/in/haidar-other",
        "title": "Haidar Al Yousuf - Partner - Gulf Capital | LinkedIn",
        "description": "Experience: Gulf Capital",
    },
    {
        "url": "https://ae.linkedin.com/in/haidar-al-yousuf-1a2b?trk=public",
        "title": "Haidar Al Yousuf - Managing Director - HealthHub | LinkedIn",
        "description": "Managing Director at <strong>Al-Futtaim</strong> HealthHub &amp; clinics",
    },
]


def brave(seen: list[httpx.Request], status: int = 200) -> BraveProfiles:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json={"web": {"results": RESULTS}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return BraveProfiles(client, SecretStr("test-key"))


async def test_finds_the_profile_that_names_the_person_and_the_company() -> None:
    seen: list[httpx.Request] = []

    hit = await brave(seen).find("Dr. Haidar Al Yousuf", "Al-Futtaim HealthHub")

    assert hit is not None
    assert hit.url == "https://www.linkedin.com/in/haidar-al-yousuf-1a2b"
    assert hit.snippet == "Managing Director at Al-Futtaim HealthHub & clinics"
    [request] = seen
    assert request.url.params["q"] == 'site:linkedin.com/in "Haidar Al Yousuf" Al-Futtaim HealthHub'
    assert request.headers["X-Subscription-Token"] == "test-key"


async def test_no_matching_result_is_none() -> None:
    assert await brave([]).find("Sara Khan", "Al-Futtaim HealthHub") is None


async def test_a_refused_search_raises() -> None:
    with pytest.raises(ProfileSearchError, match="429"):
        await brave([], status=429).find("Haidar Al Yousuf", "HealthHub")


def test_names_ignore_honorifics_accents_and_middle_names() -> None:
    assert names_person("Dr. José María Pérez", "Jose Perez | LinkedIn")
    assert not names_person("Jose Perez", "Jose Martinez")


class TestWrongPeople:
    """Shaped like real Brave results for a Dubai audit firm; people and firms are invented."""

    FIRM = "Zorvex Chartered Accountants"

    def test_a_namesake_working_elsewhere_is_rejected(self) -> None:
        namesake = ProfileHit(
            url="https://www.linkedin.com/in/farid-rana-example",
            title="Farid Rana - Sr Engineer - Northwind Systems | LinkedIn",
            snippet="Chartered engineer. Accountants and auditors are my clients.",
        )
        assert best_profile([namesake], "Farid Ul Hamid Rana", self.FIRM) is None

    def test_profession_words_are_not_the_company(self) -> None:
        auditor = ProfileHit(
            url="https://au.linkedin.com/in/omar-saeed-example",
            title="Omar Saeed | LinkedIn",
            snippet="Chartered Accountants Australia and New Zealand member.",
        )
        assert best_profile([auditor], "Omar Saeed", self.FIRM) is None

    def test_the_brand_in_the_employer_slot_is_accepted(self) -> None:
        director = ProfileHit(
            url="https://ae.linkedin.com/in/farid-ul-hamid-rana-example",
            title=f"Farid Ul Hamid Rana - Director - {self.FIRM} | LinkedIn",
            snippet="",
        )
        assert best_profile([director], "Farid Ul Hamid Rana", self.FIRM) is not None

    async def test_a_first_name_alone_is_not_searched(self) -> None:
        seen: list[httpx.Request] = []
        assert await brave(seen).find("Omar", self.FIRM) is None
        assert seen == []

    def test_a_first_name_alone_never_matches_a_result(self) -> None:
        omar = ProfileHit(
            url="https://www.linkedin.com/in/omar-zorvex-example",
            title="Omar - General Manager - Zorvex | LinkedIn",
            snippet="",
        )
        assert best_profile([omar], "Omar", self.FIRM) is None

    @pytest.mark.parametrize(
        ("name", "company", "title"),
        [
            (
                "Mila Orlova",
                "Brightline Advocates",
                "Mila Orlova - Legal Associate - Harbor Point Advocates | LinkedIn",
            ),
            ("Sara Hamdan", "Dubai Airport Free Zone Authority", "Sara Hamdan - Dubai CommerCity"),
        ],
        ids=["same profession", "same city"],
    )
    def test_sharing_a_profession_or_city_is_not_working_there(
        self, name: str, company: str, title: str
    ) -> None:
        hit = ProfileHit(url="https://www.linkedin.com/in/someone", title=title, snippet="")
        assert best_profile([hit], name, company) is None

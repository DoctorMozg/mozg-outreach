import httpx
import pytest
from pydantic import SecretStr

from mozg_outreach.pipelines.biz.profile_search import (
    BraveProfiles,
    ProfileSearchError,
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

import json

import httpx
import pytest

from mozg_outreach.pipelines.biz.directories import (
    CbuaeInsuranceRegister,
    DirectoryError,
    PropertyFinderBrokers,
    VaraRegister,
)

PF_URL = PropertyFinderBrokers.url


def broker(broker_id: int, name: str, agents: int) -> dict[str, object]:
    return {
        "id": broker_id,
        "urlSlug": f"slug-{broker_id}",
        "name": name,
        "location": "Dubai",
        "totalAgents": agents,
        "totalProperties": 40,
        "licenseNumber": "123",
        "email": "kept@out.example",
    }


def broker_page(*brokers: dict[str, object]) -> str:
    data = {"props": {"pageProps": {"brokers": {"data": list(brokers), "meta": {"page": 1}}}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'


def client(pages: dict[str, str | bytes]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        body = pages.get(str(request.url))
        return httpx.Response(404) if body is None else httpx.Response(200, content=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestPropertyFinder:
    async def test_reads_pages_and_skips_tiny_brokers(self) -> None:
        pages: dict[str, str | bytes] = {
            PF_URL: broker_page(broker(1, "Big Homes", 40), broker(2, "Holiday Lets", 2)),
            f"{PF_URL}?page=2": broker_page(broker(3, "Mid Realty", 12)),
        }
        async with client(pages) as http:
            found = await PropertyFinderBrokers().read(http, PF_URL, limit=5)

        assert [c.name for c in found] == ["Big Homes", "Mid Realty"]
        assert found[0].source_url == "https://www.propertyfinder.ae/en/broker/slug-1-1"
        assert found[0].location == "Dubai, United Arab Emirates"
        assert "40 agents" in (found[0].description or "")
        assert "kept@out.example" not in found[0].model_dump_json()

    async def test_stops_at_the_limit(self) -> None:
        pages: dict[str, str | bytes] = {
            PF_URL: broker_page(*(broker(i, f"B{i}", 20) for i in range(5)))
        }
        async with client(pages) as http:
            found = await PropertyFinderBrokers().read(http, PF_URL, limit=2)
        assert len(found) == 2

    async def test_a_changed_first_page_is_an_error(self) -> None:
        async with client({PF_URL: "<html>redesigned</html>"}) as http:
            with pytest.raises(DirectoryError, match="__NEXT_DATA__"):
                await PropertyFinderBrokers().read(http, PF_URL, limit=5)


VARA_PAGE = """<table>
<tr><th>Licensee</th><th>Licence Type</th><th>Reference</th><th>Licensed Activities</th>
<th>Licence Issued</th><th>Status</th><th>CMA Registration Number</th></tr>
<tr><td>Acme Exchange FZE</td><td>VASP Licence</td><td>VL/1</td>
<td><p>Exchange Services</p><p>Broker-Dealer Services</p></td>
<td>2026/10/05</td><td>Active</td><td>CMA-1</td></tr>
<tr><td>Gone Ltd</td><td>VASP Licence</td><td>VL/2</td><td>Custody</td>
<td>2025/01/01</td><td>Revoked</td><td>CMA-2</td></tr>
</table>"""


async def test_vara_keeps_active_licensees_with_activities() -> None:
    async with client({VaraRegister.url: VARA_PAGE}) as http:
        found = await VaraRegister().read(http, VaraRegister.url, limit=10)
    [acme] = found
    assert acme.name == "Acme Exchange FZE"
    assert acme.description == "VARA VASP Licence: Exchange Services; Broker-Dealer Services."


# Text as pypdf extracts it from the CBUAE register: English and Arabic columns run together.
CBUAE_TEXT = """No Institution Name License Type License Category REG. NO
1.  Adamjee Insurance Company Limited Insurance
Company
Foreign 044  اجنبية شركة تأمين 1.
2.  YAS TAKAFUL Insurance
Company
takaful 084  تكافلي 2.
43 ALMOTAKAMELAH INSURANCE
SERVICES
Insurance Broker National 286  وطنية وسيط تأمين 43
44 Naboodah Insurance Brokerage (LLC. m.
.. m m) Insurance Broker National 217  وطنية 44
11.  Ajit Keshav Adhikari Loss & Damage Adjusters    Individual 137 فرد 11."""


def test_cbuae_rows_put_brokers_first_and_skip_individuals() -> None:
    rows = CbuaeInsuranceRegister.rows(CBUAE_TEXT, "https://centralbank.ae/media/x/cb-register.pdf")

    assert [(r.name, r.sector) for r in rows] == [
        ("ALMOTAKAMELAH INSURANCE SERVICES", "Insurance broker"),
        ("Naboodah Insurance Brokerage (LLC. m. .. m m)", "Insurance broker"),
        ("Adamjee Insurance Company Limited", "Insurance company"),
        ("YAS TAKAFUL", "Insurance company"),
    ]
    assert rows[0].description == "CBUAE-licensed insurance broker (national), register no. 286."


class TestMatching:
    @pytest.mark.parametrize(
        ("sector", "region", "serves"),
        [
            ("Real estate brokerages (Dubai)", "United Arab Emirates", True),
            ("Real estate brokerages", "Estonia", False),
            ("Insurance brokers and smaller insurers", "Dubai", False),
            ("Real estate brokerages", None, False),
        ],
    )
    def test_property_finder_serves_uae_real_estate_only(
        self, sector: str, region: str | None, serves: bool
    ) -> None:
        assert PropertyFinderBrokers().serves(sector, region) is serves

    def test_cbuae_handles_a_newer_monthly_pdf(self) -> None:
        newer = "https://www.centralbank.ae/media/ab12cd/cb-register-october-of-2026.pdf"
        assert CbuaeInsuranceRegister().handles(newer)
        assert not CbuaeInsuranceRegister().handles("https://centralbank.ae/en/insurance/")

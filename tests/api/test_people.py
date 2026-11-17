from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from mozg_outreach.domain import ItemKind
from mozg_outreach.pipelines.biz.enrichment_models import CompanyDossier, ContactsResult
from mozg_outreach.pipelines.biz.outreach import DOSSIER
from mozg_outreach.storage.models import Item
from tests.api.conftest import Api, open_api
from tests.pipelines.biz.conftest import load_fixture
from tests.pipelines.conftest import RecordedRunner

URL = "/api/items/{}/people/linkedin"


@pytest.fixture
async def api(tmp_path: Path) -> AsyncIterator[Api]:
    async for opened in open_api(tmp_path, RecordedRunner({})):
        yield opened


async def katana(api: Api) -> int:
    dossier = CompanyDossier(contacts=load_fixture("enrichment/contacts", ContactsResult))
    project_id = await api.project("biz")
    with api.services.db.session() as session:
        item = Item(
            project_id=project_id,
            kind=ItemKind.COMPANY,
            dedup_key="katanamrp.com",
            title="Katana",
            details={DOSSIER: dossier.model_dump(mode="json")},
        )
        session.add(item)
        session.flush()
        return item.id


class TestLinkedIn:
    async def test_a_pasted_profile_lands_on_the_person_and_in_the_registry(self, api: Api) -> None:
        item_id = await katana(api)

        response = await api.client.put(
            URL.format(item_id),
            json={"name": "Jane Example", "url": "https://uk.linkedin.com/in/jane-example/?trk=x"},
        )

        assert response.status_code == 204
        item = await api.ok("GET", f"/api/items/{item_id}")
        details = item["details"]
        assert isinstance(details, dict)
        contacts = CompanyDossier.model_validate(details[DOSSIER]).contacts
        assert contacts is not None
        [jane] = contacts.key_people
        assert jane.linkedin == "https://www.linkedin.com/in/jane-example"
        entities = item["entities"]
        assert isinstance(entities, list)
        [person] = [e for e in entities if e["name"] == "Jane Example"]
        found = await api.ok("GET", f"/api/entities/{person['entity_id']}")
        identifiers = found["identifiers"]
        assert isinstance(identifiers, list)
        assert {"kind": "linkedin", "value": "linkedin.com/in/jane-example"}.items() <= next(
            i for i in identifiers if i["kind"] == "linkedin"
        ).items()

    @pytest.mark.parametrize(
        ("body", "status"),
        [
            ({"name": "Jane Example", "url": "https://example.com/jane"}, 422),
            ({"name": "Nobody", "url": "https://www.linkedin.com/in/x"}, 404),
        ],
    )
    async def test_refusals(self, api: Api, body: dict[str, str], status: int) -> None:
        item_id = await katana(api)
        response = await api.client.put(URL.format(item_id), json=body)
        assert response.status_code == status

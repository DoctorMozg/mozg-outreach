import pytest

from mozg_outreach.agents import AgentRunError
from mozg_outreach.cvs.models import CvProfileDraft, CvSummary
from mozg_outreach.domain import ItemKind
from mozg_outreach.storage.models import Item
from tests.api.conftest import Api

SUMMARY = CvSummary(
    language="en",
    headline="Senior Python backend engineer",
    seniority="senior",
    titles=["Senior Backend Engineer"],
    domains=["payments"],
    core_skills=["Python", "FastAPI"],
)
PROFILED = CvProfileDraft(summary=SUMMARY, concerns=[])


async def upload(api: Api, pdf: bytes, name: str = "Backend") -> dict[str, object]:
    response = await api.client.post(
        "/api/cvs",
        params={"name": name, "filename": "jane.pdf"},
        content=pdf,
        headers={"content-type": "application/pdf"},
    )
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


class TestLibrary:
    async def test_upload_profiles_the_cv_then_edit_download_and_delete(
        self, api: Api, cv_pdf: bytes
    ) -> None:
        api.script({"cv-profiler": [PROFILED]})
        uploaded = await upload(api, cv_pdf)
        cv = uploaded["cv"]
        assert isinstance(cv, dict)
        assert cv["summary"] == SUMMARY.model_dump()
        assert uploaded["concerns"] == []
        [sent] = api.runner.payloads["cv-profiler"]
        assert "FastAPI and PostgreSQL" in sent.model_dump()["cv_text"]

        edited = SUMMARY.model_copy(update={"headline": "Python platform engineer"})
        patched = await api.ok(
            "PATCH", f"/api/cvs/{cv['id']}", {"name": "Platform", "summary": edited.model_dump()}
        )
        assert (patched["name"], patched["summary"]) == ("Platform", edited.model_dump())
        [listed] = await api.ok_list("/api/cvs")
        assert listed == patched

        download = await api.client.get(f"/api/cvs/{cv['id']}/pdf")
        assert download.content == cv_pdf
        assert download.headers["content-type"] == "application/pdf"
        assert 'filename="jane.pdf"' in download.headers["content-disposition"]

        assert (await api.client.delete(f"/api/cvs/{cv['id']}")).status_code == 204
        assert await api.ok_list("/api/cvs") == []

    async def test_a_file_without_text_is_refused(self, api: Api) -> None:
        response = await api.client.post(
            "/api/cvs", params={"name": "Scan", "filename": "s.pdf"}, content=b"not a pdf"
        )
        assert response.status_code == 422
        assert "not a PDF" in response.json()["detail"]
        assert await api.ok_list("/api/cvs") == []

    async def test_a_failed_profile_keeps_the_cv_to_profile_again(
        self, api: Api, cv_pdf: bytes
    ) -> None:
        failure = AgentRunError("cv-profiler", "error_max_turns", "ran out of turns")
        api.script({"cv-profiler": [failure, PROFILED]})
        uploaded = await upload(api, cv_pdf)
        cv = uploaded["cv"]
        assert isinstance(cv, dict)
        assert cv["summary"] is None
        assert "ran out of turns" in str(uploaded["concerns"])

        again = await api.ok("POST", f"/api/cvs/{cv['id']}/profile")
        assert again["summary"] == SUMMARY.model_dump()


class TestItemChoice:
    @pytest.fixture
    async def job_id(self, api: Api) -> int:
        project_id = await api.project("seeker")
        with api.services.db.session() as session:
            item = Item(project_id=project_id, kind=ItemKind.JOB, dedup_key="j", title="Job")
            session.add(item)
            session.flush()
            return item.id

    async def test_user_picks_a_cv_and_clears_the_pick(
        self, api: Api, cv_pdf: bytes, job_id: int
    ) -> None:
        api.script({"cv-profiler": [PROFILED]})
        cv = (await upload(api, cv_pdf))["cv"]
        assert isinstance(cv, dict)

        picked = await api.ok("PUT", f"/api/items/{job_id}/cv", {"cv_id": cv["id"]})
        assert (picked["cv_id"], picked["picked_by"]) == (cv["id"], "user")
        detail = await api.ok("GET", f"/api/items/{job_id}")
        assert isinstance(detail["details"], dict)
        assert detail["details"]["cv_choice"]["cv_id"] == cv["id"]

        missing = await api.client.put(f"/api/items/{job_id}/cv", json={"cv_id": 999})
        assert missing.status_code == 404
        await api.client.put(f"/api/items/{job_id}/cv", json={"cv_id": None})
        cleared = await api.ok("GET", f"/api/items/{job_id}")
        assert isinstance(cleared["details"], dict)
        assert "cv_choice" not in cleared["details"]

    async def test_a_company_lead_takes_no_cv(self, api: Api, cv_pdf: bytes) -> None:
        project_id = await api.biz_project()
        with api.services.db.session() as session:
            item = Item(project_id=project_id, kind=ItemKind.COMPANY, dedup_key="c", title="C")
            session.add(item)
            session.flush()
            item_id = item.id
        response = await api.client.put(f"/api/items/{item_id}/cv", json={"cv_id": None})
        assert response.status_code == 409

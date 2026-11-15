from mozg_outreach.agents import AgentRunError
from mozg_outreach.api.schemas import BriefDraftOut
from mozg_outreach.domain import BriefFact, FieldSpec
from mozg_outreach.pipelines.biz.models import BizBrief
from mozg_outreach.pipelines.briefs import BizBriefDraft, BizBriefInput, SeekerBriefDraft
from mozg_outreach.pipelines.fields import FieldProposal
from mozg_outreach.pipelines.seeker.models import SearchScope, SeekerBrief
from mozg_outreach.storage.models import FieldSpecVersion, JsonObject
from tests.pipelines.biz.conftest import load_fixture
from tests.pipelines.seeker.conftest import CV_TEXT

from .conftest import Api, biz_brief

SITE = "https://acme.example"
ABOUT = "https://acme.example/about"


def proposal() -> FieldProposal:
    return FieldProposal(spec=load_fixture("field_proposal", FieldProposal).spec, rationale="r")


def fact(label: str, source_url: str | None) -> BriefFact:
    return BriefFact(label=label, value="v", source_url=source_url)


class TestBizBrief:
    async def test_keeps_facts_from_fetched_pages_and_drafts_fields(self, api: Api) -> None:
        project_id = await api.project()
        written = load_fixture("brief", BizBrief)
        # Another spelling of a fetched page counts; a page never fetched does not.
        written.facts = [fact("Founded", "https://www.acme.example/about/"), fact("Clients", None)]
        written.facts.append(fact("Team", "https://acme.example/team"))
        api.script(
            {
                "biz-brief-writer": [BizBriefDraft(brief=written, concerns=["icp guessed"])],
                "field-proposer": [proposal()],
            }
        )
        api.runner.fetches["biz-brief-writer"] = [SITE, ABOUT]

        draft = BriefDraftOut.model_validate(
            await api.ok(
                "POST",
                f"/api/projects/{project_id}/brief/generate",
                {"site_url": SITE, "note": "Germany only"},
            )
        )

        [sent] = api.runner.payloads["biz-brief-writer"]
        assert sent == BizBriefInput(site_url=f"{SITE}/", note="Germany only")
        assert BizBrief.model_validate(draft.brief).facts == [written.facts[0]]
        assert draft.concerns[0] == "icp guessed"
        assert len(draft.concerns) == 3
        assert all("left out" in c for c in draft.concerns[1:])
        project = await api.ok("GET", f"/api/projects/{project_id}")
        assert project["brief"] is None, "the draft is not saved"
        with api.services.db.session() as session:
            version = session.get(FieldSpecVersion, draft.field_version)
            assert version is not None
            assert version.accepted_at is None
            assert FieldSpec.model_validate(version.spec) == proposal().spec

    async def test_needs_the_site(self, api: Api) -> None:
        project_id = await api.project()
        refused = await api.client.post(
            f"/api/projects/{project_id}/brief/generate", json={"cv_text": CV_TEXT}
        )
        assert refused.status_code == 422
        assert "site_url" in refused.text

    async def test_a_failed_writer_is_a_bad_gateway(self, api: Api) -> None:
        project_id = await api.project(brief=biz_brief())
        api.script({"biz-brief-writer": [AgentRunError("biz-brief-writer", "error", "boom")]})
        failed = await api.client.post(
            f"/api/projects/{project_id}/brief/generate", json={"site_url": SITE}
        )
        assert failed.status_code == 502
        assert "boom" in failed.text


class TestSeekerBrief:
    async def test_keeps_scopes_and_cv_facts_even_when_fields_fail(self, api: Api) -> None:
        current: JsonObject = {"cv_text": CV_TEXT, "preferences": "old", "jobs": None}
        project_id = await api.project("seeker", current)
        written = SeekerBriefDraft(
            preferences="Remote EU, contract or freelance.",
            facts=[fact("Languages", None), fact("Portfolio", "https://me.example")],
            concerns=[],
        )
        api.script(
            {
                "seeker-brief-writer": [written],
                "field-proposer": [AgentRunError("field-proposer", "error", "boom")],
            }
        )

        draft = BriefDraftOut.model_validate(
            await api.ok("POST", f"/api/projects/{project_id}/brief/generate", {"cv_text": CV_TEXT})
        )

        brief = SeekerBrief.model_validate(draft.brief)
        assert (brief.cv_text, brief.preferences) == (CV_TEXT, written.preferences)
        assert (brief.jobs, brief.gigs) == (None, SearchScope(recency_days=30))
        assert brief.facts == [fact("Languages", None)]
        assert draft.field_version is None
        assert any("Portfolio" in c for c in draft.concerns)
        assert any("Report fields were not drafted" in c for c in draft.concerns)

    async def test_needs_a_full_cv(self, api: Api) -> None:
        project_id = await api.project("seeker")
        refused = await api.client.post(
            f"/api/projects/{project_id}/brief/generate", json={"cv_text": "too short"}
        )
        assert refused.status_code == 422

from sqlalchemy import select

from mozg_outreach.domain import FieldDefinition, FieldSpec, FieldType, FieldValue, RunStatus
from mozg_outreach.pipelines.fields import FieldProposal, FieldResearchResult, ResearchedValue
from mozg_outreach.pipelines.job.models import JobStrategy, JudgeResult
from mozg_outreach.runs.flow import GateAnswer
from mozg_outreach.runs.job import JobSearchState
from mozg_outreach.sources.models import SourceItem
from mozg_outreach.storage.models import FieldSpecVersion, Item, JsonObject, Run
from mozg_outreach.storage.repositories import FieldSpecRepository
from tests.pipelines.conftest import Scripted
from tests.pipelines.job.conftest import judgment
from tests.pipelines.leads.conftest import CAREERS, company, identity

from .conftest import FEED_URL, NOW, Harness

SPEC = FieldSpec(
    fields=[
        FieldDefinition(id="team_size", label="Team size", type=FieldType.NUMBER, description="d"),
        FieldDefinition(id="visa", label="Visa", type=FieldType.BOOLEAN, description="d"),
    ]
)
OTHER_URL = "https://boards.greenhouse.io/acme/jobs/2"


def accept_spec(harness: Harness, project_id: int) -> None:
    with harness.db.session() as session:
        repo = FieldSpecRepository(session)
        repo.accept(project_id, repo.propose(project_id, SPEC).version)


def research(team_size: object, visa: object) -> FieldResearchResult:
    return FieldResearchResult(
        values=[
            ResearchedValue.model_validate(
                {"field_id": "team_size", "value": team_size, "source_url": CAREERS, "quote": "q"}
            ),
            ResearchedValue.model_validate(
                {"field_id": "visa", "value": visa, "source_url": CAREERS, "quote": "q"}
            ),
        ],
        concerns=[],
    )


async def finished_search(harness: Harness, project_id: int, options: JsonObject) -> Run:
    run_id = harness.manager.start(project_id, "job.search", {"options": options})
    await harness.manager.wait(run_id)
    harness.manager.answer_gate(run_id, GateAnswer(approve=True))
    await harness.manager.wait(run_id)
    with harness.db.session() as session:
        run = session.get(Run, run_id)
        assert run is not None
        session.expunge(run)
    assert run.status == RunStatus.COMPLETE, run.error
    return run


class TestFieldProposal:
    async def test_job_proposes_from_preferences_without_cv_or_brief_review(
        self, harness: Harness
    ) -> None:
        project_id = harness.job_project()
        harness.script({"field-proposer": [FieldProposal(spec=SPEC, rationale="r")]})
        run_id = harness.manager.start(project_id, "job.fields", {})
        await harness.manager.wait(run_id)

        [sent] = harness.runner.payloads["field-proposer"]
        assert sent.model_dump(mode="json")["mode"] == "job"
        assert sent.model_dump(mode="json")["brief"] == {"preferences": "Remote EU"}
        assert "brief-reviewer" not in harness.runner.payloads
        with harness.db.session() as session:
            draft = session.scalars(select(FieldSpecVersion)).one()
            assert (draft.project_id, draft.accepted_at) == (project_id, None)


class TestSearchFields:
    async def test_top_lead_gets_typed_values_and_the_rest_are_skipped(
        self, harness: Harness, strategy: JobStrategy
    ) -> None:
        project_id = harness.job_project()
        accept_spec(harness, project_id)
        harness.feed.extra.append(
            SourceItem(
                source="greenhouse",
                source_item_id="acme/2",
                url=OTHER_URL,
                kind="job",
                title="Data Platform Engineer",
                raw_text="Python and PostgreSQL services. Remote within the EU.",
                raw_payload={},
                published_at=NOW,
                fetched_at=NOW,
                org_name="Acme",
                location="Remote - EU",
            )
        )
        judged = JudgeResult(judgments=[judgment(FEED_URL), judgment(OTHER_URL)], concerns=[])
        harness.script(
            {
                "job-strategist": [strategy],
                "job-listing-judge": [judged],
                "lead-identifier": [identity(company()), identity(company())],
                "field-researcher": [research(12, "yes")],
            }
        )
        run = await finished_search(harness, project_id, {"research_fields_top": 1})

        [request] = harness.runner.payloads["field-researcher"]
        sent = request.model_dump()
        state = JobSearchState.model_validate(run.state)
        top_id, other_id = state.item_ids
        with harness.db.session() as session:
            top, other = session.get(Item, top_id), session.get(Item, other_id)
            assert top is not None
            assert other is not None
            assert (top.score_total or 0) >= (other.score_total or 0)
            assert sent["listing"]["url"] == top.dedup_key
            assert "visa" not in top.fields, "a non-boolean visa value is dropped"
            assert FieldValue.model_validate(top.fields["team_size"]).value == 12
            assert not {"team_size", "visa"} & set(other.fields)
        assert sent["subject"]["domain"] == "acme.example", "identity facts feed the subject"
        assert any("'visa'" in c for c in state.concerns)

    async def test_no_accepted_spec_or_zero_top_skips_research(
        self, harness: Harness, strategy: JobStrategy
    ) -> None:
        project_id = harness.job_project()
        script: dict[str, list[Scripted]] = {
            "job-strategist": [strategy],
            "job-listing-judge": [JudgeResult(judgments=[judgment(FEED_URL)], concerns=[])],
            "lead-identifier": [identity()],
        }
        harness.script(script)
        await finished_search(harness, project_id, {})
        accept_spec(harness, project_id)
        harness.script(script)
        await finished_search(harness, project_id, {"research_fields_top": 0})

        assert "field-researcher" not in harness.runner.payloads

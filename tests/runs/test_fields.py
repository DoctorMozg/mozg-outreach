import pytest
from sqlalchemy import select

from mozg_outreach.domain import (
    BriefFact,
    FieldDefinition,
    FieldSpec,
    FieldType,
    FieldValue,
    RunStatus,
)
from mozg_outreach.pipelines.fields import FieldProposal, FieldResearchResult, ResearchedValue
from mozg_outreach.pipelines.seeker.models import JudgeResult, SeekerStrategy
from mozg_outreach.pipelines.seeker.search import COLUMNS
from mozg_outreach.runs.flow import GateAnswer
from mozg_outreach.runs.seeker import SeekerSearchState
from mozg_outreach.sources.models import SourceItem
from mozg_outreach.storage.models import FieldSpecVersion, Item, JsonObject, Run
from mozg_outreach.storage.repositories import FieldSpecRepository
from tests.pipelines.conftest import Scripted
from tests.pipelines.leads.conftest import CAREERS, company, identity
from tests.pipelines.seeker.conftest import judgment

from .conftest import FEED_URL, NOW, Harness

SPEC = FieldSpec(
    fields=[
        FieldDefinition(id="team_size", label="Team size", type=FieldType.NUMBER, description="d"),
        FieldDefinition(id="visa", label="Visa", type=FieldType.BOOLEAN, description="d"),
    ]
)
OTHER_URL = "https://boards.greenhouse.io/acme/jobs/2"
NOTICE = BriefFact(label="Notice period", value="One month")


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
    run_id = harness.manager.start(project_id, "seeker.search", {"options": options})
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
    async def test_seeker_proposes_from_preferences_without_cv_or_brief_review(
        self, harness: Harness
    ) -> None:
        project_id = harness.seeker_project(gigs=True, facts=[NOTICE])
        harness.script({"field-proposer": [FieldProposal(spec=SPEC, rationale="r")]})
        run_id = harness.manager.start(project_id, "seeker.fields", {})
        await harness.manager.wait(run_id)

        [sent] = harness.runner.payloads["field-proposer"]
        assert sent.model_dump(mode="json")["mode"] == "seeker"
        assert sent.model_dump(mode="json")["brief"] == {
            "preferences": "Remote EU",
            "facts": [NOTICE.model_dump()],
            "kinds": ["job", "gig"],
        }
        assert "brief-reviewer" not in harness.runner.payloads
        with harness.db.session() as session:
            draft = session.scalars(select(FieldSpecVersion)).one()
            assert (draft.project_id, draft.accepted_at) == (project_id, None)

    @pytest.mark.parametrize("repeat", ["apply_url", "pay"])
    async def test_built_in_columns_are_sent_and_repeats_dropped(
        self, harness: Harness, repeat: str
    ) -> None:
        project_id = harness.seeker_project()
        proposed = FieldSpec(fields=[*SPEC.fields, definition(repeat)])
        harness.script({"field-proposer": [FieldProposal(spec=proposed, rationale="r")]})
        run_id = harness.manager.start(project_id, "seeker.fields", {})
        await harness.manager.wait(run_id)

        [sent] = harness.runner.payloads["field-proposer"]
        assert sent.model_dump()["built_in"] == list(COLUMNS)
        assert draft_ids(harness) == ["team_size", "visa"]

    async def test_an_accepted_field_keeps_its_place_even_if_built_in(
        self, harness: Harness
    ) -> None:
        project_id = harness.seeker_project()
        accepted = FieldSpec(fields=[definition("apply_url")])
        with harness.db.session() as session:
            repo = FieldSpecRepository(session)
            repo.accept(project_id, repo.propose(project_id, accepted).version)
        proposed = FieldSpec(fields=[definition("apply_url"), *SPEC.fields])
        harness.script({"field-proposer": [FieldProposal(spec=proposed, rationale="r")]})
        run_id = harness.manager.start(project_id, "seeker.fields", {})
        await harness.manager.wait(run_id)

        assert draft_ids(harness) == ["apply_url", "team_size", "visa"]


def definition(field_id: str) -> FieldDefinition:
    return FieldDefinition(id=field_id, label=field_id, type=FieldType.TEXT, description="d")


def draft_ids(harness: Harness) -> list[str]:
    with harness.db.session() as session:
        draft = session.scalars(
            select(FieldSpecVersion).where(FieldSpecVersion.accepted_at.is_(None))
        ).one()
        return [f.id for f in FieldSpec.model_validate(draft.spec).fields]


class TestSearchFields:
    async def test_top_lead_gets_typed_values_and_the_rest_are_skipped(
        self, harness: Harness, strategy: SeekerStrategy
    ) -> None:
        project_id = harness.seeker_project()
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
                "seeker-strategist": [strategy],
                "seeker-judge": [judged],
                "lead-identifier": [identity(company()), identity(company())],
                "field-researcher": [research(12, "yes")],
            }
        )
        run = await finished_search(harness, project_id, {"research_fields_top": 1})

        [request] = harness.runner.payloads["field-researcher"]
        sent = request.model_dump()
        state = SeekerSearchState.model_validate(run.state)
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
        self, harness: Harness, strategy: SeekerStrategy
    ) -> None:
        project_id = harness.seeker_project()
        script: dict[str, list[Scripted]] = {
            "seeker-strategist": [strategy],
            "seeker-judge": [JudgeResult(judgments=[judgment(FEED_URL)], concerns=[])],
            "lead-identifier": [identity()],
        }
        harness.script(script)
        await finished_search(harness, project_id, {})
        accept_spec(harness, project_id)
        harness.script(script)
        await finished_search(
            harness, project_id, {"research_fields_top": 0, "reuse_last_strategy": False}
        )

        assert "field-researcher" not in harness.runner.payloads

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from mozg_outreach.domain import (
    FieldDefinition,
    FieldSpec,
    FieldType,
    FieldValue,
    ItemCandidate,
    ItemKind,
    ItemScore,
    ProjectMode,
    ScoreAxis,
)
from mozg_outreach.letters.models import DraftReview
from mozg_outreach.storage.db import Database
from mozg_outreach.storage.models import Item, Project, Run
from mozg_outreach.storage.repositories import (
    DraftRepository,
    FieldSpecConflictError,
    FieldSpecRepository,
    InteractionRepository,
    ItemRepository,
    NewDraft,
    RunEventRepository,
    VoiceRepository,
)


def spec(*fields: tuple[str, FieldType]) -> FieldSpec:
    return FieldSpec(
        fields=[
            FieldDefinition(id=fid, label=fid.title(), type=ftype, description="d")
            for fid, ftype in fields
        ]
    )


class TestFieldSpecVersions:
    def test_draft_is_not_current_until_accepted(self, session: Session, project: Project) -> None:
        repo = FieldSpecRepository(session)
        v1 = repo.propose(project.id, spec(("tech_stack", FieldType.LIST)))
        assert v1.version == 1
        assert repo.current(project.id) is None

        repo.accept(project.id, 1)
        repo.propose(project.id, spec(("tech_stack", FieldType.LIST), ("hq", FieldType.TEXT)))

        current = repo.current(project.id)
        assert current is not None
        assert current.version == 1

        repo.accept(project.id, 2)
        current = repo.current(project.id)
        assert current is not None
        assert current.version == 2
        assert [f.id for f in FieldSpec.model_validate(current.spec).fields] == ["tech_stack", "hq"]

    def test_accepted_field_cannot_change_type(self, session: Session, project: Project) -> None:
        repo = FieldSpecRepository(session)
        repo.propose(project.id, spec(("headcount", FieldType.NUMBER)))
        repo.accept(project.id, 1)
        with pytest.raises(FieldSpecConflictError, match="headcount: number -> text"):
            repo.propose(project.id, spec(("headcount", FieldType.TEXT)))

    def test_draft_field_may_change_type(self, session: Session, project: Project) -> None:
        repo = FieldSpecRepository(session)
        repo.propose(project.id, spec(("headcount", FieldType.NUMBER)))
        assert repo.propose(project.id, spec(("headcount", FieldType.TEXT))).version == 2

    def test_accept_unknown_version(self, session: Session, project: Project) -> None:
        with pytest.raises(LookupError, match="version 7"):
            FieldSpecRepository(session).accept(project.id, 7)

    def test_spec_rejects_duplicate_and_malformed_ids(self) -> None:
        with pytest.raises(ValidationError, match="duplicate field ids"):
            spec(("hq", FieldType.TEXT), ("hq", FieldType.URL))
        with pytest.raises(ValidationError, match="pattern"):
            spec(("Bad Id", FieldType.TEXT))


class TestRunEvents:
    def test_seq_is_per_run_and_replay_is_ordered(
        self, session: Session, project: Project, run: Run
    ) -> None:
        other = Run(project_id=project.id, pipeline="biz_research")
        session.add(other)
        session.flush()
        repo = RunEventRepository(session)

        for kind in ("started", "tool_use", "gate_open"):
            repo.append(run.id, kind, {})
        repo.append(other.id, "started", {})

        assert [(e.seq, e.kind) for e in repo.after(run.id)] == [
            (1, "started"),
            (2, "tool_use"),
            (3, "gate_open"),
        ]
        assert [e.seq for e in repo.after(run.id, seq=1)] == [2, 3]
        assert [e.seq for e in repo.after(other.id)] == [1]


def candidate(key: str, title: str) -> ItemCandidate:
    return ItemCandidate(kind=ItemKind.COMPANY, dedup_key=key, title=title)


class TestItems:
    def test_same_dedup_key_merges_fields(
        self, session: Session, project: Project, run: Run
    ) -> None:
        repo = ItemRepository(session)
        first = repo.upsert(
            project.id,
            run.id,
            ItemCandidate(
                kind=ItemKind.COMPANY,
                dedup_key="acme.io",
                title="Acme",
                fields={"hq": FieldValue(value="Berlin", source_url="https://acme.io/about")},
            ),
        )
        second = repo.upsert(
            project.id,
            run.id,
            ItemCandidate(
                kind=ItemKind.COMPANY,
                dedup_key="acme.io",
                title="Acme GmbH",
                fields={"headcount": FieldValue(value=120)},
            ),
        )

        assert first.id == second.id
        assert second.title == "Acme GmbH"
        assert set(second.fields) == {"hq", "headcount"}
        assert len(list(session.scalars(select(Item)))) == 1

    def test_details_and_score_round_trip(
        self, session: Session, project: Project, run: Run
    ) -> None:
        repo = ItemRepository(session)
        item = repo.upsert(project.id, run.id, candidate("acme.io", "Acme"))
        axis = ScoreAxis(axis="fit", value=None, weight=2, reason="not enriched yet")
        repo.set_detail(item, "verdict", axis)
        repo.set_detail(item, "dossier", axis)
        repo.set_detail(item, "dossier", None)
        repo.set_score(item, ItemScore(total=71.5, axes=[axis], blockers=[]))
        session.expire_all()

        stored = session.get(Item, item.id)
        assert stored is not None
        assert set(stored.details) == {"verdict"}
        assert ScoreAxis.model_validate(stored.details["verdict"]) == axis
        assert stored.score_total == 71.5
        assert stored.score is not None
        assert ItemScore.model_validate(stored.score).axes == [axis]

    def test_report_is_best_first_with_unscored_last(
        self, session: Session, project: Project, run: Run
    ) -> None:
        repo = ItemRepository(session)
        for key, total in [("a.io", 40.0), ("b.io", None), ("c.io", 90.0)]:
            item = repo.upsert(project.id, run.id, candidate(key, key))
            if total is not None:
                repo.set_score(item, ItemScore(total=total, axes=[], blockers=[]))

        assert [i.dedup_key for i in repo.report(project.id)] == ["c.io", "a.io", "b.io"]


def test_deleting_project_cascades(db: Database) -> None:
    with db.session() as s:
        p = Project(name="p", mode=ProjectMode.SEEKER, sender_name=None, brief=None)
        s.add(p)
        s.flush()
        s.add(Run(project_id=p.id, pipeline="job_search"))
        project_id = p.id

    with db.session() as s:
        s.delete(s.get_one(Project, project_id))

    with db.session() as s:
        assert list(s.scalars(select(Run))) == []


class TestVoice:
    def test_samples_split_by_channel_and_held_out(self, session: Session) -> None:
        repo = VoiceRepository(session)
        repo.add_sample("email", "one")
        repo.add_sample("email", "two", held_out=True)
        repo.add_sample("dm", "three")
        assert repo.samples("email") == ["one"]
        assert repo.samples("email", held_out=True) == ["two"]

    def test_profile_is_replaced_per_channel(self, session: Session) -> None:
        repo = VoiceRepository(session)
        repo.save_profile("email", ItemCandidate(kind=ItemKind.COMPANY, dedup_key="v1", title="v1"))
        repo.save_profile("email", ItemCandidate(kind=ItemKind.COMPANY, dedup_key="v2", title="v2"))
        stored = repo.profile("email")
        assert stored is not None
        assert stored["dedup_key"] == "v2"
        assert repo.profile("dm") is None


class TestDraftsAndInteractions:
    @pytest.fixture
    def item_id(self, session: Session, project: Project, run: Run) -> int:
        return ItemRepository(session).upsert(project.id, run.id, candidate("acme.io", "Acme")).id

    def test_regenerated_letter_gets_next_version(self, session: Session, item_id: int) -> None:
        drafts = DraftRepository(session)
        review = DraftReview(
            address="jane@acme.example",
            address_confidence="published",
            hook_used="h",
            angle_used="a",
            reader_calibration="role priorities",
            naturalized=True,
            before_naturalize="draft",
            changes=[],
            problems=[],
            review_notes=[],
        )
        for body in ("first", "second"):
            new = NewDraft(channel="email", recipient="Jane", subject="S", body=body, review=review)
            drafts.add(item_id, new)
        drafts.add(item_id, new.model_copy(update={"recipient": "Ann", "body": "other"}))

        rows = [(d.recipient, d.version, d.body) for d in drafts.for_item(item_id)]
        assert rows == [("Ann", 1, "other"), ("Jane", 1, "first"), ("Jane", 2, "second")]
        assert drafts.for_item(item_id)[0].review["reader_calibration"] == "role priorities"

    def test_interactions_are_logged_in_order(self, session: Session, item_id: int) -> None:
        log = InteractionRepository(session)
        log.log(item_id, "email_sent", "to Jane")
        log.log(item_id, " reply ")
        assert [(i.kind, i.note) for i in log.history(item_id)] == [
            ("email_sent", "to Jane"),
            ("reply", ""),
        ]
        with pytest.raises(ValueError, match="empty"):
            log.log(item_id, "  ")

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from mozg_outreach.domain import EntityKind, FieldValue, ItemCandidate, ItemKind
from mozg_outreach.pipelines.biz.duplicates import CompanyDuplicates, MergeError
from mozg_outreach.storage.models import (
    Draft,
    Entity,
    Interaction,
    Item,
    ItemLink,
    MailThread,
    Project,
)

DOMAIN = {"value": "crs.example", "source_url": None, "quote": None, "confidence": "unverified"}


class Leads:
    def __init__(self, session: Session, project: Project) -> None:
        self.session, self.project = session, project

    def item(
        self, key: str, title: str, details: dict[str, object] | None = None, **fields: object
    ) -> Item:
        row = Item(
            project_id=self.project.id,
            kind=ItemKind.COMPANY,
            dedup_key=key,
            title=title,
            fields=fields,
            details=details or {},
        )
        self.session.add(row)
        self.session.flush()
        return row

    def draft(self, item: Item, step: int) -> Draft:
        row = Draft(item_id=item.id, channel="email", recipient="Ann", step=step, body="Hi")
        self.session.add(row)
        self.session.flush()
        return row

    def thread(self, item: Item, draft: Draft) -> MailThread:
        row = MailThread(
            item_id=item.id,
            recipient="Ann",
            step=draft.step,
            draft_id=draft.id,
            address="ann@crs.example",
            gmail_thread_id=item.id + 1000,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def link(self, item: Item, entity: Entity, role: str = "employee") -> None:
        self.session.add(ItemLink(item_id=item.id, entity_id=entity.id, role=role))
        self.session.flush()


@pytest.fixture
def leads(session: Session, project: Project) -> Leads:
    return Leads(session, project)


def candidate(key: str, title: str, domain: str | None = None) -> ItemCandidate:
    fields = {"domain": FieldValue(value=domain)} if domain else {}
    return ItemCandidate(kind=ItemKind.COMPANY, dedup_key=key, title=title, fields=fields)


class TestKnown:
    def test_matches_older_name_forms_and_enriched_domains(
        self, session: Session, project: Project, leads: Leads
    ) -> None:
        leads.item("name:afridi-angell-llp", "Afridi & Angell LLP")
        leads.item("name:charles-russell-speechlys", "Charles Russell Speechlys", domain=DOMAIN)

        found = CompanyDuplicates(session, project.id).known(
            [
                candidate("name:afridi-angell", "Afridi & Angell Legal Consultants"),
                candidate("crs.example", "Charles Russell Speechlys LLP", domain="crs.example"),
                candidate("new.example", "New Firm", domain="new.example"),
            ]
        )

        assert [c.dedup_key for c in found] == [
            "name:afridi-angell-llp",
            "name:charles-russell-speechlys",
            "new.example",
        ]

    def test_two_candidates_for_one_lead_keep_the_first(
        self, session: Session, project: Project, leads: Leads
    ) -> None:
        leads.item("name:hadef", "Hadef & Partners")
        found = CompanyDuplicates(session, project.id).known(
            [candidate("name:hadef", "Hadef & Partners"), candidate("name:hadef", "Hadef")]
        )
        assert [c.title for c in found] == ["Hadef & Partners"]


class TestMerge:
    def test_groups_keep_the_lead_with_gmail_mail(
        self, session: Session, project: Project, leads: Leads
    ) -> None:
        planned = leads.item(
            "name:crs", "Charles Russell Speechlys", {"approach": {}}, domain=DOMAIN
        )
        sent = leads.item(
            "name:crs-llp", "Charles Russell Speechlys LLP", {"dossier": {}}, domain=DOMAIN
        )
        leads.thread(sent, leads.draft(sent, 1))
        leads.item("other.example", "Other Firm")

        [group] = CompanyDuplicates(session, project.id).groups()

        assert group.keep.id == sent.id
        assert [d.id for d in group.drop] == [planned.id]

    def test_moves_history_mail_and_links_then_deletes_the_copy(
        self, session: Session, project: Project, leads: Leads
    ) -> None:
        keep = leads.item("name:bsa", "BSA LAW", {"approach": {"plan": 1}, "dossier": {"d": 1}})
        drop = leads.item("name:bsa-law", "BSA LAW", {"assessment": {"a": 1}, "dossier": {"d": 2}})
        drop.fields = {"domain": DOMAIN}
        stale = leads.draft(drop, 1)
        in_gmail = leads.draft(drop, 2)
        leads.thread(drop, in_gmail)
        # Even on Gmail threads and history, so the kept lead wins on its plan.
        leads.thread(keep, leads.draft(keep, 3))
        session.add_all(
            [
                Interaction(item_id=keep.id, kind="sent"),
                Interaction(item_id=drop.id, kind="replied"),
            ]
        )
        ann, bob = (
            Entity(kind=EntityKind.PERSON, name="Ann"),
            Entity(kind=EntityKind.PERSON, name="Bob"),
        )
        session.add_all([ann, bob])
        session.flush()
        leads.link(keep, ann)
        leads.link(drop, ann)
        leads.link(drop, bob)
        drop_id, stale_id, in_gmail_id = drop.id, stale.id, in_gmail.id

        merged = CompanyDuplicates(session, project.id).merge_all()

        assert [g.keep.id for g in merged] == [keep.id]
        assert session.get(Item, drop_id) is None
        assert session.get(Draft, stale_id) is None
        assert session.get_one(Draft, in_gmail_id).item_id == keep.id
        assert {t.item_id for t in session.scalars(select(MailThread))} == {keep.id}
        assert {i.item_id for i in session.scalars(select(Interaction))} == {keep.id}
        links = session.scalars(select(ItemLink).where(ItemLink.item_id == keep.id))
        assert sorted(link.entity_id for link in links) == [ann.id, bob.id]
        assert keep.details == {
            "approach": {"plan": 1},
            "dossier": {"d": 1},
            "assessment": {"a": 1},
        }
        assert keep.fields == {"domain": DOMAIN}

    def test_two_gmail_drafts_for_one_step_stop_the_merge(
        self, session: Session, project: Project, leads: Leads
    ) -> None:
        first = leads.item("name:fichte", "Fichte & Co.")
        second = leads.item("name:fichte-co", "Fichte & Co.")
        leads.thread(first, leads.draft(first, 1))
        leads.thread(second, leads.draft(second, 1))

        with pytest.raises(MergeError, match="both have a Gmail draft for Ann step 1"):
            CompanyDuplicates(session, project.id).merge_all()

"""Finds companies a project holds twice and folds each pair into the lead with the most work."""

import logging
from collections.abc import Iterable, Mapping

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mozg_outreach.domain import ItemCandidate, ItemKind
from mozg_outreach.pipelines.biz.discovery import company_key
from mozg_outreach.pipelines.biz.outreach import APPROACH, DOSSIER
from mozg_outreach.registry.identifiers import normalize_domain
from mozg_outreach.storage.models import (
    Draft,
    Interaction,
    Item,
    ItemLink,
    MailThread,
    Suppression,
)

logger = logging.getLogger(__name__)


class DuplicateLead(BaseModel):
    id: int
    title: str


class DuplicateGroup(BaseModel):
    keep: DuplicateLead
    drop: list[DuplicateLead]


class MergeError(RuntimeError):
    """Two leads can't be folded together without losing a Gmail thread."""


def company_keys(dedup_key: str, title: str, fields: Mapping[str, object]) -> set[str]:
    """Every key one company answers to: its stored key, its name today and its domain."""
    keys = {dedup_key}
    name = company_key(title)
    if name:
        keys.add(f"name:{name}")
    domain = fields.get("domain")
    value = domain.get("value") if isinstance(domain, dict) else getattr(domain, "value", None)
    if isinstance(value, str) and (normalized := normalize_domain(value)):
        keys.add(normalized)
    return keys


class CompanyDuplicates:
    """The project's company leads, indexed by every key each one answers to."""

    def __init__(self, session: Session, project_id: int) -> None:
        self._session = session
        self._items = list(
            session.scalars(
                select(Item)
                .where(Item.project_id == project_id, Item.kind == ItemKind.COMPANY)
                .order_by(Item.id)
            )
        )
        self._by_key: dict[str, Item] = {}
        for item in self._items:
            for key in company_keys(item.dedup_key, item.title, item.fields):
                self._by_key.setdefault(key, item)

    def known(self, candidates: Iterable[ItemCandidate]) -> list[ItemCandidate]:
        """Candidates keyed like the lead they already are; a second hit on one lead is dropped."""
        found: list[ItemCandidate] = []
        used: set[str] = set()
        for candidate in candidates:
            keys = company_keys(candidate.dedup_key, candidate.title, candidate.fields)
            existing = next((self._by_key[k] for k in sorted(keys) if k in self._by_key), None)
            key = existing.dedup_key if existing else candidate.dedup_key
            if key in used:
                logger.info("dropped %r: same company as an earlier candidate", candidate.title)
                continue
            used.add(key)
            found.append(candidate.model_copy(update={"dedup_key": key}))
        return found

    def groups(self) -> list[DuplicateGroup]:
        """Leads sharing any key, each group with the lead to keep first."""
        parent = {item.id: item.id for item in self._items}

        def root(item_id: int) -> int:
            while parent[item_id] != item_id:
                parent[item_id] = parent[parent[item_id]]
                item_id = parent[item_id]
            return item_id

        first: dict[str, int] = {}
        for item in self._items:
            for key in company_keys(item.dedup_key, item.title, item.fields):
                if key in first:
                    parent[root(item.id)] = root(first[key])
                else:
                    first[key] = item.id
        members: dict[int, list[Item]] = {}
        for item in self._items:
            members.setdefault(root(item.id), []).append(item)
        found = []
        for items in members.values():
            if len(items) < 2:
                continue
            keep, *drop = sorted(items, key=self._work, reverse=True)
            found.append(
                DuplicateGroup(
                    keep=DuplicateLead(id=keep.id, title=keep.title),
                    drop=[DuplicateLead(id=d.id, title=d.title) for d in drop],
                )
            )
        return found

    def merge_all(self) -> list[DuplicateGroup]:
        merged = []
        for group in self.groups():
            keep = self._session.get_one(Item, group.keep.id)
            for lead in group.drop:
                self.merge(keep, self._session.get_one(Item, lead.id))
            merged.append(group)
        return merged

    def merge(self, keep: Item, drop: Item) -> None:
        """Moves the history, Gmail threads and links of `drop` onto `keep`, then deletes it."""
        threads = list(
            self._session.scalars(select(MailThread).where(MailThread.item_id == drop.id))
        )
        taken = {
            (t.recipient, t.step)
            for t in self._session.scalars(select(MailThread).where(MailThread.item_id == keep.id))
        }
        clash = [t for t in threads if (t.recipient, t.step) in taken]
        if clash:
            raise MergeError(
                f"leads {keep.id} and {drop.id} both have a Gmail draft for "
                f"{clash[0].recipient} step {clash[0].step}"
            )
        in_gmail = {t.draft_id for t in threads}
        # The kept lead's plan replaces the other's, so drafts of that plan go unless in Gmail.
        stale = APPROACH in keep.details
        version = self._session.scalar(
            select(func.max(Draft.version)).where(Draft.item_id == keep.id)
        )
        for draft in self._session.scalars(select(Draft).where(Draft.item_id == drop.id)):
            if stale and draft.id not in in_gmail:
                self._session.delete(draft)
            else:
                draft.item_id = keep.id
                draft.version += version or 0
        for thread in threads:
            thread.item_id = keep.id
        for logged in self._session.scalars(
            select(Interaction).where(Interaction.item_id == drop.id)
        ):
            logged.item_id = keep.id
        for blocked in self._session.scalars(
            select(Suppression).where(Suppression.item_id == drop.id)
        ):
            blocked.item_id = keep.id
        linked = {
            (link.entity_id, link.role)
            for link in self._session.scalars(select(ItemLink).where(ItemLink.item_id == keep.id))
        }
        for link in self._session.scalars(select(ItemLink).where(ItemLink.item_id == drop.id)):
            if (link.entity_id, link.role) in linked:
                self._session.delete(link)
            else:
                link.item_id = keep.id
        keep.fields = {**drop.fields, **keep.fields}
        keep.details = {**drop.details, **keep.details}
        if keep.score is None:
            keep.score, keep.score_total = drop.score, drop.score_total
        self._session.flush()
        self._session.delete(drop)
        self._session.flush()
        logger.info("merged lead %d into %d (%s)", drop.id, keep.id, keep.title)

    def _work(self, item: Item) -> tuple[int, int, bool, int, bool, str, int]:
        """What would be lost by dropping a lead: sent mail first, then history, plan, drafts."""

        def count(model: type[MailThread] | type[Interaction] | type[Draft]) -> int:
            return (
                self._session.scalar(
                    select(func.count()).select_from(model).where(model.item_id == item.id)
                )
                or 0
            )

        enriched = item.enriched_at.isoformat() if item.enriched_at else ""
        return (
            count(MailThread),
            count(Interaction),
            APPROACH in item.details,
            count(Draft),
            DOSSIER in item.details,
            enriched,
            item.id,
        )

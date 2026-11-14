"""The global registry: entity profiles with their sources, connections, merges and graph."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from mozg_outreach.api.schemas import (
    EntityDetail,
    EntityOut,
    GraphEdge,
    GraphNode,
    GraphOut,
    IdentifierOut,
    LeadOut,
    MergeSuggestionOut,
    RelationOut,
    SightingOut,
)
from mozg_outreach.api.services import Services, get_or_404, services
from mozg_outreach.domain import EntityKind
from mozg_outreach.registry.suggestions import MergeSuggester
from mozg_outreach.storage.models import (
    Entity,
    EntityIdentifier,
    Item,
    ItemLink,
    MergeSuggestion,
    Relation,
    Sighting,
)

router = APIRouter(prefix="/api")
Svc = Annotated[Services, Depends(services)]
MAX_GRAPH_NODES = 500


@router.get("/entities")
async def list_entities(
    svc: Svc, kind: EntityKind | None = None, q: str = "", limit: int = Query(100, le=500)
) -> list[EntityOut]:
    with svc.db.session() as session:
        query = select(Entity).order_by(Entity.updated_at.desc()).limit(limit)
        if kind is not None:
            query = query.where(Entity.kind == kind)
        if q:
            query = query.where(Entity.name.icontains(q, autoescape=True))
        return [EntityOut.model_validate(e) for e in session.scalars(query)]


def _relations(session: Session, entity_id: int) -> list[RelationOut]:
    rows = session.scalars(
        select(Relation).where(or_(Relation.from_id == entity_id, Relation.to_id == entity_id))
    ).all()
    others = {r.to_id if r.from_id == entity_id else r.from_id for r in rows}
    named = session.scalars(select(Entity).where(Entity.id.in_(others)))
    names = {e.id: e.name for e in named}
    out: list[RelationOut] = []
    for r in rows:
        outgoing = r.from_id == entity_id
        other = r.to_id if outgoing else r.from_id
        out.append(
            RelationOut(
                type=r.type,
                direction="out" if outgoing else "in",
                other_id=other,
                other_name=names.get(other, "?"),
                detail=r.detail,
                source_url=r.source_url,
                quote=r.quote,
            )
        )
    return out


@router.get("/entities/{entity_id}")
async def get_entity(entity_id: int, svc: Svc) -> EntityDetail:
    with svc.db.session() as session:
        entity = get_or_404(session, Entity, entity_id)
        leads = session.execute(
            select(ItemLink.role, Item)
            .join(Item, Item.id == ItemLink.item_id)
            .where(ItemLink.entity_id == entity_id)
        )
        return EntityDetail.model_validate(
            {
                **EntityOut.model_validate(entity).model_dump(),
                "profile": entity.profile,
                "identifiers": [
                    IdentifierOut.model_validate(i)
                    for i in session.scalars(
                        select(EntityIdentifier).where(EntityIdentifier.entity_id == entity_id)
                    )
                ],
                "sightings": [
                    SightingOut.model_validate(s)
                    for s in session.scalars(
                        select(Sighting)
                        .where(Sighting.entity_id == entity_id)
                        .order_by(Sighting.seen_at.desc())
                    )
                ],
                "relations": _relations(session, entity_id),
                "leads": [
                    LeadOut(item_id=i.id, project_id=i.project_id, title=i.title, role=role)
                    for role, i in leads
                ],
            }
        )


class GraphBuilder:
    """Collects nodes and edges once each, stopping at MAX_GRAPH_NODES."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self.nodes: dict[str, GraphNode] = {}
        self.edges: dict[tuple[str, str, str], GraphEdge] = {}

    def add_entities(self, ids: set[int]) -> None:
        room = MAX_GRAPH_NODES - len(self.nodes)
        for e in self._session.scalars(select(Entity).where(Entity.id.in_(ids)).limit(room)):
            kind: Literal["person", "company"] = (
                "company" if e.kind == EntityKind.COMPANY else "person"
            )
            self.nodes[f"e{e.id}"] = GraphNode(id=f"e{e.id}", kind=kind, label=e.name)

    def add_items(self, items: list[Item]) -> None:
        for i in items[: MAX_GRAPH_NODES - len(self.nodes)]:
            self.nodes[f"i{i.id}"] = GraphNode(id=f"i{i.id}", kind="lead", label=i.title)

    def edge(self, source: str, target: str, label: str) -> None:
        if source in self.nodes and target in self.nodes:
            self.edges[(source, target, label)] = GraphEdge(
                source=source, target=target, label=label
            )

    def relations_among(self, ids: set[int]) -> list[Relation]:
        return list(
            self._session.scalars(
                select(Relation).where(or_(Relation.from_id.in_(ids), Relation.to_id.in_(ids)))
            )
        )

    def result(self) -> GraphOut:
        return GraphOut(nodes=list(self.nodes.values()), edges=list(self.edges.values()))


def _project_graph(session: Session, project_id: int) -> GraphOut:
    graph = GraphBuilder(session)
    items = list(session.scalars(select(Item).where(Item.project_id == project_id)))
    links = list(
        session.scalars(select(ItemLink).where(ItemLink.item_id.in_([i.id for i in items])))
    )
    graph.add_items(items)
    ids = {link.entity_id for link in links}
    graph.add_entities(ids)
    return _connect(graph, links, ids)


def _entity_graph(session: Session, entity_id: int, depth: int) -> GraphOut:
    get_or_404(session, Entity, entity_id)
    graph = GraphBuilder(session)
    ids, frontier = {entity_id}, {entity_id}
    for _ in range(depth):
        found = graph.relations_among(frontier)
        frontier = ({r.from_id for r in found} | {r.to_id for r in found}) - ids
        ids |= frontier
    graph.add_entities(ids)
    links = list(session.scalars(select(ItemLink).where(ItemLink.entity_id.in_(ids))))
    item_ids = {link.item_id for link in links}
    graph.add_items(list(session.scalars(select(Item).where(Item.id.in_(item_ids)))))
    return _connect(graph, links, ids)


def _connect(graph: GraphBuilder, links: list[ItemLink], ids: set[int]) -> GraphOut:
    for link in links:
        graph.edge(f"i{link.item_id}", f"e{link.entity_id}", link.role)
    for r in graph.relations_among(ids):
        graph.edge(f"e{r.from_id}", f"e{r.to_id}", r.type)
    return graph.result()


@router.get("/graph")
async def graph(
    svc: Svc,
    project_id: int | None = None,
    entity_id: int | None = None,
    depth: int = Query(1, ge=1, le=3),
) -> GraphOut:
    """Leads of a project with their people and companies, or an entity's neighbourhood."""
    with svc.db.session() as session:
        if project_id is not None and entity_id is None:
            return _project_graph(session, project_id)
        if entity_id is not None and project_id is None:
            return _entity_graph(session, entity_id, depth)
    raise HTTPException(422, "pass exactly one of project_id or entity_id")


@router.get("/merge-suggestions")
async def list_suggestions(svc: Svc, status: str = "pending") -> list[MergeSuggestionOut]:
    with svc.db.session() as session:
        rows = session.scalars(
            select(MergeSuggestion)
            .where(MergeSuggestion.status == status)
            .order_by(MergeSuggestion.id)
        )
        out: list[MergeSuggestionOut] = []
        for s in rows:
            a, b = session.get(Entity, s.entity_a_id), session.get(Entity, s.entity_b_id)
            if a is None or b is None:
                continue  # cascades remove these; a stale row is not worth failing the list
            out.append(
                MergeSuggestionOut(
                    id=s.id,
                    reason=s.reason,
                    status=s.status,
                    a=EntityOut.model_validate(a),
                    b=EntityOut.model_validate(b),
                )
            )
        return out


@router.post("/merge-suggestions/{suggestion_id}/accept")
async def accept_suggestion(suggestion_id: int, svc: Svc) -> EntityOut:
    with svc.db.session() as session:
        return EntityOut.model_validate(MergeSuggester(session).accept(suggestion_id))


@router.post("/merge-suggestions/{suggestion_id}/reject", status_code=204)
async def reject_suggestion(suggestion_id: int, svc: Svc) -> None:
    with svc.db.session() as session:
        MergeSuggester(session).reject(suggestion_id)

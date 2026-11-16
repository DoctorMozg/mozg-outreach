"""User-selected biz leads: enrich them, link their people in the registry, draft letters."""

import logging
from datetime import date
from typing import get_args

from sqlalchemy.orm import Session

from mozg_outreach.domain import FieldSpec, FieldValue, ItemCandidate
from mozg_outreach.letters.models import RevisionTarget, SenderSettings
from mozg_outreach.naturalize.models import Channel
from mozg_outreach.naturalize.naturalizer import Naturalizer, VoiceContext
from mozg_outreach.naturalize.voice import VoiceProfile
from mozg_outreach.pipelines.biz.approach import ApproachPlanner, ApproachRequest
from mozg_outreach.pipelines.biz.approach_models import StoredApproach
from mozg_outreach.pipelines.biz.assessment_models import CompanyAssessment
from mozg_outreach.pipelines.biz.enrichment import (
    BizEnrichment,
    CompanyDomain,
    brief_from_candidate,
)
from mozg_outreach.pipelines.biz.enrichment_models import CompanyDossier
from mozg_outreach.pipelines.biz.letter_briefs import angles_from
from mozg_outreach.pipelines.biz.letters import (
    BizLetterWriter,
    LetterBatch,
    LetterRequest,
)
from mozg_outreach.pipelines.biz.models import BizBrief, Strategy
from mozg_outreach.pipelines.biz.regions import GCC_PLACES, mentions
from mozg_outreach.pipelines.biz.registry_ingest import BizRegistryIngest
from mozg_outreach.pipelines.steps import PipelineSteps
from mozg_outreach.registry.ingest import IngestContext
from mozg_outreach.scoring.biz import BizScoreInput, BizScorer
from mozg_outreach.storage.models import ENRICHED_AT, Item, Project, utcnow
from mozg_outreach.storage.repositories import (
    DraftRepository,
    FieldSpecRepository,
    ItemRepository,
    NewDraft,
    VoiceRepository,
)

logger = logging.getLogger(__name__)

# Keys of the item's `details` sections this pipeline owns.
DOSSIER = "dossier"
ASSESSMENT = "assessment"
APPROACH = "approach"


class BizOutreach:
    def __init__(self, session: Session, steps: PipelineSteps) -> None:
        self._session = session
        self._steps = steps
        self._items = ItemRepository(session)

    async def enrich(
        self, item: Item, strategy: Strategy, run_id: int | None = None
    ) -> CompanyDossier:
        """Run the enrichment agents, store the dossier, rescore and register the people."""
        candidate = stored_candidate(item)
        dossier = await BizEnrichment(self._steps).enrich(brief_from_candidate(candidate, strategy))
        self._items.set_detail(item, DOSSIER, dossier)
        item.details = {**item.details, ENRICHED_AT: utcnow().isoformat()}
        ingest = BizRegistryIngest(self._session)
        context = IngestContext(project_id=item.project_id, run_id=run_id, item_id=item.id)
        company = ingest.company(candidate, context)
        ingest.dossier(company, dossier, context)
        self._remember_domain(item, candidate, dossier)
        self._rescore(item, strategy, dossier)
        logger.info("enriched %s: %d concerns", item.title, len(dossier.concerns))
        return dossier

    async def plan_approach(self, item: Item, strategy: Strategy) -> StoredApproach:
        """Plan who to contact, how and when, from the stored dossier."""
        if DOSSIER not in item.details:
            raise ValueError(f"item {item.id} is not enriched yet; enrich it before planning")
        project = self._session.get(Project, item.project_id)
        brief = BizBrief.model_validate(project.brief) if project and project.brief else None
        approach = await ApproachPlanner(self._steps).plan(
            ApproachRequest(
                company=item.title,
                fields=_fields(item),
                dossier=CompanyDossier.model_validate(item.details[DOSSIER]),
                strategy=strategy,
                buyer_roles=brief.icp.buyer_roles if brief else [],
                today=date.today(),
                previous=StoredApproach.model_validate(item.details[APPROACH])
                if APPROACH in item.details
                else None,
            )
        )
        self._items.set_detail(item, APPROACH, approach)
        return approach

    async def write_letters(
        self, item: Item, strategy: Strategy, revision: RevisionTarget | None = None
    ) -> LetterBatch:
        """Draft one letter per reachable contact, or rewrite one, each stored as a new version."""
        if DOSSIER not in item.details:
            raise ValueError(
                f"item {item.id} is not enriched yet; enrich it before writing letters"
            )
        project = self._session.get(Project, item.project_id)
        if project is None or not project.sender_name:
            raise ValueError(f"project {item.project_id} has no sender name to sign letters with")
        request = LetterRequest(
            company=item.title,
            fields=_fields(item),
            dossier=CompanyDossier.model_validate(item.details[DOSSIER]),
            angles=angles_from(strategy.outreach_angles),
            sender=SenderSettings.model_validate(
                {
                    "name": project.sender_name,
                    "tone": project.sender_tone,
                    "voice": strategy.sender_voice,
                }
            ),
            background=BizBrief.model_validate(project.brief).background()
            if project.brief
            else None,
            voices={channel: self._voice(channel) for channel in get_args(Channel)},
            revision=revision,
            approach=StoredApproach.model_validate(item.details[APPROACH])
            if APPROACH in item.details
            else None,
            opt_out=_in_gcc(item),
            existing=self._step_texts(item.id),
        )
        writer = BizLetterWriter(self._steps, Naturalizer(self._steps))
        batch = await writer.write(request)
        drafts = DraftRepository(self._session)
        for letter in batch.letters:
            drafts.add(
                item.id,
                NewDraft(
                    channel=letter.channel,
                    recipient=letter.recipient,
                    step=letter.step,
                    subject=letter.subject,
                    body=letter.text,
                    review=letter.review,
                ),
            )
        return batch

    def _step_texts(self, item_id: int) -> dict[int, str]:
        """The newest draft of each plan step; drafts come oldest version first."""
        return {d.step: d.body for d in DraftRepository(self._session).for_item(item_id) if d.step}

    def _remember_domain(
        self, item: Item, candidate: ItemCandidate, dossier: CompanyDossier
    ) -> None:
        """Store a domain enrichment found, so a re-run and the report start from it."""
        known = candidate.fields.get("domain")
        if known is not None and CompanyDomain.usable(str(known.value)):
            return
        found = CompanyDomain.from_dossier(dossier)
        if found is not None:
            self._items.merge_fields(item, {"domain": found})

    def _rescore(self, item: Item, strategy: Strategy, dossier: CompanyDossier) -> None:
        assessment = item.details.get(ASSESSMENT)
        verdict = CompanyAssessment.model_validate(assessment).verdict if assessment else None
        current = FieldSpecRepository(self._session).current(item.project_id)
        data = BizScoreInput(
            dossier=dossier,
            verdict=verdict,
            signals=strategy.search_signals,
            fields=_fields(item),
            spec=FieldSpec.model_validate(current.spec) if current else None,
            weights=strategy.scoring_weights,
        )
        self._items.set_score(item, BizScorer().score(data))

    def _voice(self, channel: Channel) -> VoiceContext:
        voices = VoiceRepository(self._session)
        stored = voices.profile(channel)
        profile = VoiceProfile.model_validate(stored) if stored else None
        return VoiceContext(profile=profile, samples=voices.samples(channel))


def _fields(item: Item) -> dict[str, FieldValue]:
    return {k: FieldValue.model_validate(v) for k, v in item.fields.items()}


def stored_candidate(item: Item) -> ItemCandidate:
    return ItemCandidate(
        kind=item.kind, dedup_key=item.dedup_key, title=item.title, fields=_fields(item)
    )


def _in_gcc(item: Item) -> bool:
    location = _fields(item).get("location")
    return location is not None and mentions(str(location.value), GCC_PLACES)

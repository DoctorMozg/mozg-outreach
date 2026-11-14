"""User-selected biz leads: enrich them, link their people in the registry, draft letters."""

import logging
from typing import get_args

from sqlalchemy.orm import Session

from mozg_outreach.domain import FieldSpec, FieldValue, ItemCandidate
from mozg_outreach.letters.models import SenderSettings
from mozg_outreach.naturalize.models import Channel
from mozg_outreach.naturalize.naturalizer import Naturalizer, VoiceContext
from mozg_outreach.naturalize.voice import VoiceProfile
from mozg_outreach.pipelines.biz.assessment_models import CompanyAssessment
from mozg_outreach.pipelines.biz.enrichment import BizEnrichment, brief_from_candidate
from mozg_outreach.pipelines.biz.enrichment_models import CompanyDossier
from mozg_outreach.pipelines.biz.letter_briefs import angles_from
from mozg_outreach.pipelines.biz.letters import (
    BizLetterWriter,
    LetterBatch,
    LetterRequest,
)
from mozg_outreach.pipelines.biz.models import Strategy
from mozg_outreach.pipelines.biz.registry_ingest import BizRegistryIngest
from mozg_outreach.pipelines.steps import PipelineSteps
from mozg_outreach.registry.ingest import IngestContext
from mozg_outreach.scoring.biz import BizScoreInput, BizScorer
from mozg_outreach.storage.models import Item, Project
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


class BizOutreach:
    def __init__(self, session: Session, steps: PipelineSteps) -> None:
        self._session = session
        self._steps = steps
        self._items = ItemRepository(session)

    async def enrich(
        self, item: Item, strategy: Strategy, run_id: int | None = None
    ) -> CompanyDossier:
        """Run the five enrichment agents, then rescore with the dossier and register its people."""
        _require_selected(item)
        candidate = stored_candidate(item)
        dossier = await BizEnrichment(self._steps).enrich(brief_from_candidate(candidate, strategy))
        self._items.set_detail(item, DOSSIER, dossier)
        ingest = BizRegistryIngest(self._session)
        context = IngestContext(project_id=item.project_id, run_id=run_id, item_id=item.id)
        company = ingest.company(candidate, context)
        ingest.dossier(company, dossier, context)
        self._rescore(item, strategy, dossier)
        logger.info("enriched %s: %d concerns", item.title, len(dossier.concerns))
        return dossier

    async def write_letters(self, item: Item, strategy: Strategy) -> LetterBatch:
        """Draft one naturalized letter per reachable contact and store each as a new version."""
        _require_selected(item)
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
            voices={channel: self._voice(channel) for channel in get_args(Channel)},
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
                    subject=letter.subject,
                    body=letter.text,
                    review=letter.review,
                ),
            )
        return batch

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


def _require_selected(item: Item) -> None:
    if not item.selected:
        raise ValueError(f"item {item.id} is not selected; only selected leads are enriched")


def _fields(item: Item) -> dict[str, FieldValue]:
    return {k: FieldValue.model_validate(v) for k, v in item.fields.items()}


def stored_candidate(item: Item) -> ItemCandidate:
    return ItemCandidate(
        kind=item.kind, dedup_key=item.dedup_key, title=item.title, fields=_fields(item)
    )

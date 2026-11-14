"""The user's writing samples per channel and the voice profile built from them."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from mozg_outreach.api.schemas import ProfileBuild, SampleIn, SampleOut, VoiceProfileOut
from mozg_outreach.api.services import Services, get_or_404, services
from mozg_outreach.naturalize.models import Channel
from mozg_outreach.naturalize.voice import MIN_SAMPLES, VoiceProfile, VoiceProfiler
from mozg_outreach.pipelines.steps import PipelineSteps
from mozg_outreach.storage.models import VoiceSample
from mozg_outreach.storage.repositories import VoiceRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/voice")
Svc = Annotated[Services, Depends(services)]


@router.get("/{channel}/samples")
async def list_samples(channel: Channel, svc: Svc) -> list[SampleOut]:
    with svc.db.session() as session:
        rows = session.scalars(
            select(VoiceSample).where(VoiceSample.channel == channel).order_by(VoiceSample.id)
        )
        return [SampleOut.model_validate(r) for r in rows]


@router.post("/{channel}/samples", status_code=201)
async def add_sample(channel: Channel, body: SampleIn, svc: Svc) -> SampleOut:
    with svc.db.session() as session:
        row = VoiceRepository(session).add_sample(channel, body.text, held_out=body.held_out)
        return SampleOut.model_validate(row)


@router.delete("/samples/{sample_id}", status_code=204)
async def delete_sample(sample_id: int, svc: Svc) -> None:
    with svc.db.session() as session:
        session.delete(get_or_404(session, VoiceSample, sample_id))


def _profile_out(channel: Channel, stored: dict[str, object] | None) -> VoiceProfileOut:
    profile = VoiceProfile.model_validate(stored) if stored else None
    return VoiceProfileOut(
        channel=channel, profile=stored, rendered=profile.render() if profile else None
    )


@router.get("/{channel}/profile")
async def get_profile(channel: Channel, svc: Svc) -> VoiceProfileOut:
    with svc.db.session() as session:
        return _profile_out(channel, VoiceRepository(session).profile(channel))


@router.post("/{channel}/profile")
async def build_profile(channel: Channel, body: ProfileBuild, svc: Svc) -> VoiceProfileOut:
    """Measure the samples and ask the profiler for notes; one agent call, so it takes a while."""
    with svc.db.session() as session:
        samples = VoiceRepository(session).samples(channel)
    if len(samples) < MIN_SAMPLES:
        raise HTTPException(
            409, f"add at least {MIN_SAMPLES} {channel} samples, have {len(samples)}"
        )
    steps = PipelineSteps(svc.runner, svc.prompts, svc.home / "workspaces" / "voice")
    profile = await VoiceProfiler(steps).build(channel, samples, body.style_prompts)
    logger.info("voice profile for %s cost $%.2f", channel, steps.cost_usd)
    with svc.db.session() as session:
        VoiceRepository(session).save_profile(channel, profile)
    return _profile_out(channel, profile.model_dump(mode="json"))

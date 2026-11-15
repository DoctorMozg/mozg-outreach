"""Draft a project brief from the user's company site (biz) or their CV (seeker)."""

import logging
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from mozg_outreach.agents import AgentEvent
from mozg_outreach.domain import BriefFact
from mozg_outreach.pipelines.biz.models import CONCERNS_DESCRIPTION, BizBrief
from mozg_outreach.pipelines.seeker.models import SeekerBrief
from mozg_outreach.pipelines.steps import PipelineSteps

logger = logging.getLogger(__name__)


class BizBriefInput(BaseModel):
    site_url: str
    note: str


class BizBriefDraft(BaseModel):
    brief: BizBrief
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)


class SeekerBriefInput(BaseModel):
    cv_text: str
    note: str


class SeekerBriefDraft(BaseModel):
    preferences: str = Field(min_length=1)
    facts: list[BriefFact]
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)


class BriefDraft(BaseModel):
    brief: BizBrief | SeekerBrief
    concerns: list[str]


def page_key(url: str) -> str:
    """Two spellings of one page compare equal: scheme, www, fragment and a final slash differ."""
    parts = urlsplit(url.strip())
    host = parts.hostname or ""
    host = host.removeprefix("www.")
    query = f"?{parts.query}" if parts.query else ""
    return f"{host}{parts.path.rstrip('/')}{query}"


class FetchLog:
    """Event sink that remembers every page an agent fetched."""

    def __init__(self) -> None:
        self.pages: set[str] = set()

    def __call__(self, event: AgentEvent) -> None:
        if event.url:
            self.pages.add(page_key(event.url))


class BriefWriter:
    def __init__(self, steps: PipelineSteps, fetched: FetchLog) -> None:
        self._steps = steps
        self._fetched = fetched

    async def biz(self, site_url: str, note: str) -> BriefDraft:
        draft = await self._steps.run(
            "shared", "biz-brief-writer", BizBriefInput(site_url=site_url, note=note), BizBriefDraft
        )
        facts, dropped = self._sourced(draft.brief.facts, need_source=True)
        brief = draft.brief.model_copy(update={"facts": facts})
        return BriefDraft(brief=brief, concerns=[*draft.concerns, *dropped])

    async def seeker(self, cv_text: str, note: str, current: SeekerBrief | None) -> BriefDraft:
        """Keeps the scopes of the project's current brief; the writer only reads the CV."""
        draft = await self._steps.run(
            "shared",
            "seeker-brief-writer",
            SeekerBriefInput(cv_text=cv_text, note=note),
            SeekerBriefDraft,
        )
        facts, dropped = self._sourced(draft.facts, need_source=False)
        scopes = current.model_dump(include={"jobs", "gigs"}) if current else {}
        brief = SeekerBrief.model_validate(
            {"cv_text": cv_text, "preferences": draft.preferences, "facts": facts, **scopes}
        )
        return BriefDraft(brief=brief, concerns=[*draft.concerns, *dropped])

    def _sourced(
        self, facts: list[BriefFact], *, need_source: bool
    ) -> tuple[list[BriefFact], list[str]]:
        """A fact citing a page must cite one the agent fetched, so no source is made up."""
        kept: list[BriefFact] = []
        dropped: list[str] = []
        for fact in facts:
            if fact.source_url is None:
                read = not need_source
            else:
                read = page_key(fact.source_url) in self._fetched.pages
            if read:
                kept.append(fact)
            else:
                cited = fact.source_url or "no page"
                dropped.append(f"Fact {fact.label!r} left out: it cites {cited}, not a page read.")
        if dropped:
            logger.info("brief writer: %d of %d facts left out", len(dropped), len(facts))
        return kept, dropped

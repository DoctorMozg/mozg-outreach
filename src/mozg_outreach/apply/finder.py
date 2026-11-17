"""When no form shows, an agent reads a page screenshot and names the control that opens it."""

import logging
from typing import Protocol

from mozg_outreach.apply.models import FormFinderInput, FormFinding
from mozg_outreach.pipelines.steps import PipelineSteps

logger = logging.getLogger(__name__)


class FormFinder(Protocol):
    async def find(self, request: FormFinderInput, screenshot: bytes) -> FormFinding: ...


class AgentFormFinder:
    def __init__(self, steps: PipelineSteps) -> None:
        self._steps = steps

    async def find(self, request: FormFinderInput, screenshot: bytes) -> FormFinding:
        return await self._steps.run(
            "seeker", "form-finder", request, FormFinding, screenshots=[screenshot]
        )

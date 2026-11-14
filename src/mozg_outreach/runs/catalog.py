"""Every pipeline a run can execute, and which one searches for each project mode."""

from mozg_outreach.domain import ProjectMode
from mozg_outreach.runs.biz import BIZ_SEARCH
from mozg_outreach.runs.fields import BIZ_FIELDS
from mozg_outreach.runs.flow import AnyFlow
from mozg_outreach.runs.freelance import FREELANCE_SEARCH
from mozg_outreach.runs.items import ITEM_FLOWS
from mozg_outreach.runs.job import JOB_SEARCH

FLOWS: tuple[AnyFlow, ...] = (BIZ_FIELDS, BIZ_SEARCH, JOB_SEARCH, FREELANCE_SEARCH, *ITEM_FLOWS)
SEARCH_PIPELINES = {
    ProjectMode.BIZ: BIZ_SEARCH.pipeline,
    ProjectMode.JOB: JOB_SEARCH.pipeline,
    ProjectMode.FREELANCE: FREELANCE_SEARCH.pipeline,
}

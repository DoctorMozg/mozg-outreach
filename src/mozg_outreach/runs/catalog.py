"""Every pipeline a run can execute, and which one searches for each project mode."""

from pydantic import BaseModel

from mozg_outreach.domain import ProjectMode
from mozg_outreach.runs.biz import BIZ_SEARCH, BizOptions
from mozg_outreach.runs.fields import BIZ_FIELDS, SEEKER_FIELDS
from mozg_outreach.runs.flow import AnyFlow
from mozg_outreach.runs.items import ITEM_FLOWS
from mozg_outreach.runs.seeker import SEEKER_SEARCH, SeekerOptions

FLOWS: tuple[AnyFlow, ...] = (BIZ_FIELDS, SEEKER_FIELDS, BIZ_SEARCH, SEEKER_SEARCH, *ITEM_FLOWS)
FIELD_PIPELINES = {
    ProjectMode.BIZ: BIZ_FIELDS.pipeline,
    ProjectMode.SEEKER: SEEKER_FIELDS.pipeline,
}
SEARCH_PIPELINES = {
    ProjectMode.BIZ: BIZ_SEARCH.pipeline,
    ProjectMode.SEEKER: SEEKER_SEARCH.pipeline,
}
SEARCH_OPTIONS: dict[ProjectMode, type[BaseModel]] = {
    ProjectMode.BIZ: BizOptions,
    ProjectMode.SEEKER: SeekerOptions,
}

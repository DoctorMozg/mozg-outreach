"""What a flow reads from its project, loaded once per phase in a short session."""

from pydantic import BaseModel

from mozg_outreach.domain import FieldSpec, ProjectMode
from mozg_outreach.letters.models import SenderSettings
from mozg_outreach.naturalize.models import Channel
from mozg_outreach.naturalize.naturalizer import VoiceContext
from mozg_outreach.naturalize.voice import VoiceProfile
from mozg_outreach.storage.db import Database
from mozg_outreach.storage.models import Item, JsonObject, Project, Run
from mozg_outreach.storage.repositories import FieldSpecRepository, VoiceRepository


class ProjectInputs(BaseModel):
    project_id: int
    mode: ProjectMode
    brief: JsonObject | None
    sender_name: str | None
    sender_tone: str
    spec: FieldSpec | None

    @classmethod
    def load(cls, db: Database, project_id: int) -> "ProjectInputs":
        with db.session() as session:
            project = session.get(Project, project_id)
            if project is None:
                raise LookupError(f"no project {project_id}")
            current = FieldSpecRepository(session).current(project_id)
            return cls(
                project_id=project_id,
                mode=project.mode,
                brief=project.brief,
                sender_name=project.sender_name,
                sender_tone=project.sender_tone,
                spec=FieldSpec.model_validate(current.spec) if current else None,
            )

    def sender(self, voice: str = "") -> SenderSettings:
        if not self.sender_name:
            raise ValueError(f"project {self.project_id} has no sender name to sign letters with")
        return SenderSettings.model_validate(
            {"name": self.sender_name, "tone": self.sender_tone, "voice": voice}
        )

    def require_brief(self) -> JsonObject:
        if not self.brief:
            raise ValueError(f"project {self.project_id} has no brief yet")
        return self.brief


def load_voice(db: Database, channel: Channel) -> VoiceContext:
    with db.session() as session:
        voices = VoiceRepository(session)
        stored = voices.profile(channel)
        return VoiceContext(
            profile=VoiceProfile.model_validate(stored) if stored else None,
            samples=voices.samples(channel),
        )


def search_state(db: Database, item_id: int) -> tuple[Item, JsonObject]:
    """The item and the state of the search run that found it, which holds its strategy."""
    with db.session() as session:
        item = session.get(Item, item_id)
        if item is None:
            raise LookupError(f"no item {item_id}")
        run = session.get(Run, item.last_run_id) if item.last_run_id else None
        if run is None or "strategy" not in run.state:
            raise ValueError(f"item {item_id} has no search run with a strategy")
        return item, run.state

import pytest

from mozg_outreach.domain import FieldValue
from mozg_outreach.pipelines.biz.enrichment_models import CompanyDossier, EmailContact
from mozg_outreach.pipelines.biz.letter_briefs import LetterBriefBuilder

ABOUT = "https://katanamrp.com/about/"


def jane_with(
    dossier: CompanyDossier,
    *,
    email: str | None = None,
    guessed: bool = False,
    linkedin: str | None = None,
) -> CompanyDossier:
    copy = dossier.model_copy(deep=True)
    assert copy.contacts
    jane = copy.contacts.key_people[0]
    jane.linkedin = linkedin
    if email:
        jane.email = EmailContact(
            address=email, kind="pattern_guess" if guessed else "published", source_url=ABOUT
        )
    return copy


class TestRecipients:
    def test_same_person_from_two_agents_is_one_contact(self, dossier: CompanyDossier) -> None:
        contacts = LetterBriefBuilder("Katana", dossier).contacts()
        assert [(c.name, c.title) for c in contacts] == [("Jane Example", "CTO")]

    @pytest.mark.parametrize(
        ("email", "guessed", "linkedin", "expected"),
        [
            ("jane@katanamrp.com", False, "linkedin.com/in/jane", ("email", "published", False)),
            (None, False, "linkedin.com/in/jane", ("dm", "linkedin", False)),
            ("jane@katanamrp.com", True, "linkedin.com/in/jane", ("dm", "linkedin", False)),
            ("jane@katanamrp.com", True, None, ("email", "guessed", False)),
            (None, False, None, ("email", "shared_inbox", True)),
        ],
    )
    def test_best_channel_wins(
        self,
        dossier: CompanyDossier,
        email: str | None,
        guessed: bool,
        linkedin: str | None,
        expected: tuple[str, str, bool],
    ) -> None:
        builder = LetterBriefBuilder(
            "Katana", jane_with(dossier, email=email, guessed=guessed, linkedin=linkedin)
        )
        [addressee] = builder.recipients().addressees
        route = addressee.route
        assert (route.channel, route.confidence, route.generic_inbox) == expected

    def test_no_route_is_skipped_with_reason(self, dossier: CompanyDossier) -> None:
        bare = dossier.model_copy(deep=True)
        assert bare.contacts
        bare.contacts.emails = [e for e in bare.contacts.emails if e.kind != "published"]
        recipients = LetterBriefBuilder("Katana", bare).recipients()
        assert recipients.addressees == []
        assert [s.contact for s in recipients.skipped] == ["Jane Example"]


class TestEvidence:
    def test_hooks_lead_with_timely_news_and_carry_sources(self, dossier: CompanyDossier) -> None:
        fields = {
            "sector": FieldValue(
                value="Manufacturing ERP", source_url=ABOUT, quote="Cloud ERP for makers"
            ),
            "hq": FieldValue(value="Tallinn", source_url=ABOUT),
        }
        with_blog = dossier.model_copy(deep=True)
        assert with_blog.tech
        with_blog.tech.tech_blog = "Engineering blog post on moving to Kubernetes (2025-09)"
        evidence = LetterBriefBuilder("Katana", with_blog).evidence(fields)

        first = evidence.hooks[0]
        assert first.source == "news"
        assert first.signal.startswith("Katana secures EUR 14M Series B extension (2025-10)")
        assert {h.source for h in evidence.hooks} == {"news", "growth", "tech", "research"}
        assert {
            "Katana",
            "Tallinn",
            "Manufacturing ERP",
            "Cogito Capital Partners",
            "EUR 60M+",
            "9",
        } <= set(evidence.verified_entities)

    def test_empty_dossier_has_no_hooks(self) -> None:
        evidence = LetterBriefBuilder("Katana", CompanyDossier()).evidence({})
        assert evidence.hooks == []
        assert evidence.verified_entities == ["Katana"]

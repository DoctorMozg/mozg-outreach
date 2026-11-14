import pytest

from mozg_outreach.domain import IdentifierKind
from mozg_outreach.registry.identifiers import name_key, normalize, normalize_domain


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.Acme.io/about", "acme.io"),
        ("acme.io:8080", "acme.io"),
        ("sub.acme.co.uk", "sub.acme.co.uk"),
        ("acme", None),
        ("not a domain.com", None),
        (None, None),
    ],
)
def test_normalize_domain(raw: str | None, expected: str | None) -> None:
    assert normalize_domain(raw) == expected


@pytest.mark.parametrize(
    ("kind", "raw", "expected"),
    [
        (IdentifierKind.EMAIL, " Anna.Kask@Acme.io ", "anna.kask@acme.io"),
        (IdentifierKind.EMAIL, "mailto:hr@acme.io", "hr@acme.io"),
        (IdentifierKind.EMAIL, "not-an-email", None),
        (
            IdentifierKind.LINKEDIN,
            "https://www.linkedin.com/in/Anna-Kask/?trk=x",
            "linkedin.com/in/anna-kask",
        ),
        (
            IdentifierKind.LINKEDIN,
            "ee.linkedin.com/company/katana-mrp/about",
            "linkedin.com/company/katana-mrp",
        ),
        (IdentifierKind.LINKEDIN, "https://linkedin.com/feed/", None),
        (IdentifierKind.TELEGRAM, "@Anna_Kask", "anna_kask"),
        (IdentifierKind.TELEGRAM, "https://t.me/anna_kask/", "anna_kask"),
        (IdentifierKind.TELEGRAM, "@ab", None),
        (IdentifierKind.DOMAIN, "", None),
    ],
)
def test_normalize(kind: IdentifierKind, raw: str, expected: str | None) -> None:
    identifier = normalize(kind, raw)
    assert (identifier.value if identifier else None) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Katana Technologies OU", "katana technologies"),
        ("Acme sp. z o.o.", "acme"),
        ("Blackwall (formerly BotGuard)", "blackwall"),
        ("Jan O Kask", "jan o kask"),
        ("O", "o"),
    ],
)
def test_name_key_strips_only_trailing_legal_suffixes(name: str, expected: str) -> None:
    assert name_key(name) == expected

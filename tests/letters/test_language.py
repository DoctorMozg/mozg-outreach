import pytest

from mozg_outreach.letters.language import detect_language


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("We are looking for a senior Python engineer to join our payments team.", "en"),
        ("Ищем Python-разработчика в команду платежей. Опыт с FastAPI и PostgreSQL.", "ru"),
        ("Шукаємо Python-розробника до команди. Досвід з FastAPI, віддалена робота.", "uk"),
        ("Wir suchen eine erfahrene Entwicklerin für unser Team in Berlin und remote.", "de"),
        ("Nous recherchons un développeur Python pour notre équipe à Paris.", "fr"),
        ("Buscamos un desarrollador Python para nuestro equipo de pagos en Madrid.", "es"),
        ("Senior Python / FastAPI / PostgreSQL", "en"),
    ],
    ids=["en", "ru", "uk", "de", "fr", "es", "terms-only"],
)
def test_detects_the_language_of_a_posting(text: str, language: str) -> None:
    assert detect_language(text) == language


def test_russian_posting_full_of_english_terms_stays_russian() -> None:
    text = (
        "Senior Backend Developer (Python, FastAPI, Kafka, Kubernetes, PostgreSQL, Redis). "
        "Удалённо, полный день, оформление по ТК РФ."
    )
    assert detect_language(text) == "ru"

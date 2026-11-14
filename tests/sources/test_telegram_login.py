from telethon.tl.types import auth

from mozg_outreach.sources.telegram_login import describe


def sent(code_type: object, next_type: object = None, timeout: int | None = None) -> auth.SentCode:
    return auth.SentCode(type=code_type, phone_code_hash="h", next_type=next_type, timeout=timeout)


class TestDescribe:
    def test_app_code_points_to_the_service_chat_and_offers_sms(self) -> None:
        text = describe(sent(auth.SentCodeTypeApp(length=5), auth.CodeTypeSms(), 60))

        assert "'Telegram' service chat" in text
        assert "5 digits" in text
        assert "resend via Sms (wait 60s)" in text

    def test_email_setup_requirement_is_explained(self) -> None:
        text = describe(sent(auth.SentCodeTypeSetUpEmailRequired(apple_signin_allowed=False)))

        assert "login email first" in text

    def test_payment_requirement_is_explained(self) -> None:
        payment = {
            "store_product": "p",
            "phone_code_hash": "h",
            "support_email_address": "s",
            "support_email_subject": "s",
            "currency": "EUR",
            "amount": 1,
            "premium_days": 7,
        }
        text = describe(auth.SentCodePaymentRequired(**payment))

        assert "payment" in text

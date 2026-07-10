from manus_mini.redaction import redact_sensitive_text


def test_redacts_authorization_bearer_tokens() -> None:
    token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secret.payload"

    redacted = redact_sensitive_text(f"Authorization: Bearer {token}")

    assert token not in redacted
    assert redacted == "Authorization: Bearer [REDACTED]"


def test_redacts_url_query_secret_values() -> None:
    redacted = redact_sensitive_text("https://x.test/cb?access_token=secret-token&ok=1")

    assert "secret-token" not in redacted
    assert redacted == "https://x.test/cb?access_token=[REDACTED]&ok=1"

from manus_mini.redaction import redact_sensitive_text, redact_sensitive_value


def test_redacts_authorization_bearer_tokens() -> None:
    token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secret.payload"

    redacted = redact_sensitive_text(f"Authorization: Bearer {token}")

    assert token not in redacted
    assert redacted == "Authorization: Bearer [REDACTED]"


def test_redacts_url_query_secret_values() -> None:
    redacted = redact_sensitive_text("https://x.test/cb?access_token=secret-token&ok=1")

    assert "secret-token" not in redacted
    assert redacted == "https://x.test/cb?access_token=[REDACTED]&ok=1"


def test_redacts_common_environment_secret_names() -> None:
    content = "\n".join(
        [
            "AWS_SECRET_ACCESS_KEY=aws-secret-value",
            "CLIENT_SECRET: client-secret-value",
            "GH_TOKEN=ghp_secretvalue123",
        ]
    )

    redacted = redact_sensitive_text(content)

    assert "aws-secret-value" not in redacted
    assert "client-secret-value" not in redacted
    assert "ghp_secretvalue123" not in redacted
    assert "AWS_SECRET_ACCESS_KEY=[REDACTED]" in redacted
    assert "CLIENT_SECRET: [REDACTED]" in redacted
    assert "GH_TOKEN=[REDACTED]" in redacted


def test_redacts_values_under_sensitive_dict_keys() -> None:
    payload = {
        "api_key": "plain-secret-value",
        "token_count": 128,
        "nested": {
            "CLIENT_SECRET": "client-secret-value",
        },
    }

    redacted = redact_sensitive_value(payload)

    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["token_count"] == 128
    assert redacted["nested"]["CLIENT_SECRET"] == "[REDACTED]"

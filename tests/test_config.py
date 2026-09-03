"""Settings behaviour that is a security property, not a preference.

Two of these encode bugs that were live in the code:

* ``DEBUG`` defaulted to True, so a deploy that set nothing published
  interactive API docs and widened CORS. The fix is only durable if a test
  fails when someone "tidies up" by deriving DEBUG from ``APP_ENV`` again --
  which reintroduces it exactly, because ``APP_ENV`` itself defaults to
  "development".
* ``CORS_ORIGINS`` as a ``list[str]`` made pydantic-settings JSON-decode the
  raw environment value before any validator ran, so a comma-separated value
  raised ``SettingsError`` at import time rather than parsing.

``_env_file=None`` on every construction is load-bearing: without it a
developer's real ``.env`` leaks into the test run and these assertions pass or
fail depending on whose machine they run on.
"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings

#: Every environment variable these tests touch, cleared before each case so a
#: developer's shell cannot influence the result.
MANAGED = (
    "APP_ENV",
    "DEBUG",
    "CORS_ORIGINS",
    "DATABASE_URL",
    "RAZORPAY_KEY_ID",
    "RAZORPAY_KEY_SECRET",
    "RAZORPAY_WEBHOOK_SECRET",
    "RESEND_API_KEY",
    "RESEND_FROM_EMAIL",
)

#: A fully configured production environment, used as the baseline the
#: individual failure cases deviate from one field at a time.
REAL_PRODUCTION = {
    "APP_ENV": "production",
    "DATABASE_URL": "postgresql://recoup:s3cret@db.neon.tech/recoup",
    "RAZORPAY_KEY_ID": "rzp_live_9f2a",
    "RAZORPAY_KEY_SECRET": "live-secret-value",
    "RAZORPAY_WEBHOOK_SECRET": "whsec_live_value",
    "RESEND_API_KEY": "re_live_value",
    "RESEND_FROM_EMAIL": "billing@recoup.in",
}


@pytest.fixture
def build(monkeypatch):
    """Construct Settings from an explicit environment and nothing else."""

    def _build(**env: str) -> Settings:
        for key in MANAGED:
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, str(value))
        return Settings(_env_file=None)

    return _build


# --- DEBUG defaults ---------------------------------------------------------


def test_unconfigured_is_not_debug(build):
    """The whole point: setting nothing must give the safe value."""

    assert build().DEBUG is False


def test_app_env_development_does_not_imply_debug(build):
    """Guards the regression, not just the fix.

    APP_ENV defaults to "development", so deriving DEBUG from it would put an
    unconfigured deploy straight back on DEBUG=True.
    """

    assert build(APP_ENV="development").DEBUG is False


def test_debug_is_opt_in(build):
    assert build(DEBUG="true").DEBUG is True


def test_docs_are_hidden_unless_debug_is_on(build):
    """The consequence that actually matters, asserted as behaviour."""

    assert build().DEBUG is False
    assert build(DEBUG="true").DEBUG is True


# --- CORS -------------------------------------------------------------------


def test_no_origins_means_no_cross_origin_access(build):
    assert build().cors_origins == []


def test_comma_separated_origins_parse(build):
    settings = build(CORS_ORIGINS="https://app.recoup.in, https://admin.recoup.in")
    assert settings.cors_origins == ["https://app.recoup.in", "https://admin.recoup.in"]


def test_json_origins_still_parse(build):
    """Someone's existing JSON-array environment must keep working."""

    assert build(CORS_ORIGINS='["https://app.recoup.in"]').cors_origins == ["https://app.recoup.in"]


def test_origins_are_never_wildcarded_implicitly(build):
    for settings in (build(), build(DEBUG="true"), build(APP_ENV="development")):
        assert "*" not in settings.cors_origins


# --- production guard -------------------------------------------------------


def test_production_refuses_placeholder_secrets(build):
    with pytest.raises(ValidationError) as caught:
        build(APP_ENV="production")

    message = str(caught.value)
    assert "placeholder" in message
    assert "RESEND_FROM_EMAIL" in message


@pytest.mark.parametrize(
    ("field", "placeholder"),
    [
        ("DATABASE_URL", "postgresql://user:password@localhost:5432/recoup"),
        ("RAZORPAY_KEY_ID", "rzp_test_xxxxxxxxxxxxxxxx"),
        ("RESEND_FROM_EMAIL", "noreply@yourdomain.com"),
    ],
)
def test_one_leftover_placeholder_is_enough_to_refuse(build, field, placeholder):
    """A single unset secret must fail the boot, not be averaged away."""

    with pytest.raises(ValidationError) as caught:
        build(**{**REAL_PRODUCTION, field: placeholder})

    assert field in str(caught.value)


def test_production_refuses_debug(build):
    with pytest.raises(ValidationError) as caught:
        build(**REAL_PRODUCTION, DEBUG="true")

    assert "DEBUG must be False in production" in str(caught.value)


def test_a_properly_configured_production_boots(build):
    settings = build(**REAL_PRODUCTION)

    assert settings.is_production is True
    assert settings.DEBUG is False
    assert settings.cors_origins == []


def test_development_is_not_subject_to_the_production_guard(build):
    """Placeholders are exactly what local development should be allowed."""

    assert build(APP_ENV="development").is_production is False


# --- driver normalisation ---------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:p@host/db",
        "postgres://u:p@host/db",
        "postgresql+asyncpg://u:p@host/db",
        "postgresql+psycopg://u:p@host/db",
    ],
)
def test_every_url_form_normalises_to_the_installed_driver(build, url):
    """asyncpg is not installed; a URL naming it must not reach SQLAlchemy."""

    settings = build(DATABASE_URL=url)

    assert settings.database_url_async.startswith("postgresql+psycopg://")
    assert "asyncpg" not in settings.database_url_async
    assert settings.database_url_async.endswith("u:p@host/db")

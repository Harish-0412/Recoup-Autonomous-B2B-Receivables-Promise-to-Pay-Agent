from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Application
    APP_NAME: str = "Recoup"
    APP_ENV: str = "development"
    #: Defaults to False so an unconfigured deploy is the *safe* one: no
    #: interactive docs, no widened CORS. When this defaulted to True, a deploy
    #: that forgot to set anything published /docs and set CORS to "*".
    #:
    #: Deliberately *not* derived from APP_ENV. Deriving it looks tidier and
    #: quietly restores the bug, because APP_ENV itself defaults to
    #: "development" -- so an unconfigured deploy would land right back on
    #: DEBUG=True. One explicit variable, safe unless you say otherwise.
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    #: Browser origins allowed to call this API, comma-separated. Empty means
    #: same-origin only, the right default for a service with no browser client.
    #: Never widened to "*" implicitly -- a real frontend names its origin.
    #:
    #: Typed as a string, not list[str], on purpose: pydantic-settings treats a
    #: list field as "complex" and JSON-decodes it straight from the
    #: environment before any validator runs, so a plain comma-separated value
    #: raises SettingsError instead of reaching a parser. Parsing lives in
    #: ``cors_origins`` below.
    CORS_ORIGINS: str = Field(
        default="",
        description="Comma-separated list of allowed browser origins",
    )

    # Database
    DATABASE_URL: str = Field(
        default="postgresql+psycopg://user:password@localhost:5432/recoup",
        description="PostgreSQL connection string for Neon/Supabase",
    )
    #: "null" disables the client-side pool, which is what you want behind a
    #: server-side pooler (Neon, Supabase, PgBouncer in transaction mode).
    DB_POOL_MODE: Literal["default", "null"] = Field(
        default="default",
        description='Set to "null" when connecting through PgBouncer',
    )
    DB_POOL_SIZE: int = Field(default=5, ge=1)
    DB_MAX_OVERFLOW: int = Field(default=10, ge=0)

    # Execution
    #: When true the executor renders every message in full and logs it instead
    #: of delivering it. No email leaves, no payment link is created, and the
    #: contact is recorded as SIMULATED so the run still exercises the caps and
    #: the ladder exactly as a live run would.
    #:
    #: Defaults to True, and that default is the safety property: a deployment
    #: that has not deliberately said "yes, send real email to real customers"
    #: must not send real email to real customers. Turning it off is a decision
    #: someone makes on purpose.
    DRY_RUN: bool = Field(
        default=True,
        description="Render and log messages instead of delivering them",
    )

    # Razorpay (Test Mode)
    RAZORPAY_KEY_ID: str = Field(
        default="rzp_test_xxxxxxxxxxxxxxxx",
        description="Razorpay test mode key ID",
    )
    RAZORPAY_KEY_SECRET: str = Field(
        default="xxxxxxxxxxxxxxxxxxxxxxxx",
        description="Razorpay test mode key secret",
    )
    RAZORPAY_WEBHOOK_SECRET: str = Field(
        default="whsec_xxxxxxxxxxxxxxxxxxxxxxxx",
        description="Razorpay webhook secret for signature verification",
    )

    # Resend (Email)
    RESEND_API_KEY: str = Field(
        default="re_xxxxxxxxxxxxxxxxxxxxxxxx",
        description="Resend API key for email delivery",
    )
    RESEND_FROM_EMAIL: str = Field(
        default="noreply@yourdomain.com",
        description="From email address for Resend",
    )
    #: Signing secret for Resend's inbound webhook (Svix scheme).
    RESEND_WEBHOOK_SECRET: str = Field(
        default="whsec_xxxxxxxxxxxxxxxxxxxxxxxx",
        description="Resend inbound webhook signing secret",
    )
    #: Domain that receives replies. Outbound mail sets Reply-To to
    #: ``reply+<invoice>.<signature>@<this domain>``, which is how an inbound
    #: reply is matched to an invoice without parsing the subject line.
    REPLY_INBOUND_DOMAIN: str = Field(
        default="reply.yourdomain.com",
        description="Domain that receives tagged reply addresses",
    )
    #: HMAC key for those tagged addresses. Without it the address format is
    #: guessable, and anyone could post a reply naming any invoice in the book.
    REPLY_ADDRESS_SECRET: str = Field(
        default="change-me-reply-address-secret",
        description="HMAC key signing tagged reply-to addresses",
    )

    # Groq LLM
    GROQ_API_KEY: str | None = Field(
        default=None,
        description="Groq API key for LLM inference",
    )
    GROQ_MODEL: str = Field(
        # llama-3.1-70b-versatile was decommissioned by Groq; requests against
        # it now fail with model_not_found.
        default="llama-3.3-70b-versatile",
        description="Groq model to use",
    )

    # Google Gemini LLM
    GEMINI_API_KEY: str | None = Field(
        default=None,
        description="Google Gemini API key for LLM inference",
    )
    GEMINI_MODEL: str = Field(
        default="gemini-1.5-flash",
        description="Gemini model to use",
    )

    # Policy Configuration
    DEFAULT_DISCOUNT_CEILING_PCT: int = Field(
        default=10,
        description="Maximum discount percentage the agent can offer",
        ge=0,
        le=100,
    )
    DEFAULT_MIN_CONTACT_GAP_DAYS: int = Field(
        default=3,
        description="Minimum days between contacts for same invoice",
        ge=1,
    )
    DEFAULT_ESCALATION_LADDER: str = Field(
        default='["reminder_1", "reminder_2", "final_notice", "human_handoff"]',
        description="JSON array of escalation steps",
    )
    DEFAULT_OPTOUT_DAYS: int = Field(
        default=30,
        description="Days to respect opt-out before re-contact",
        ge=1,
    )
    DEFAULT_MAX_CONTACTS_PER_INVOICE: int = Field(
        default=4,
        description="Total messages the agent may send about one invoice",
        ge=0,
    )
    DEFAULT_MIN_DAYS_OVERDUE_TO_CONTACT: int = Field(
        default=1,
        description="Do not contact until an invoice is at least this overdue",
        ge=0,
    )
    DEFAULT_MAX_DISCOUNT_AMOUNT: float | None = Field(
        default=None,
        description="Absolute discount ceiling in rupees, on top of the percentage",
        ge=0,
    )

    # Scheduler
    SCHEDULER_INTERVAL_SECONDS: int = Field(
        default=300,
        description="Interval in seconds for background scheduler",
        ge=60,
    )

    @property
    def escalation_ladder(self) -> list[str]:
        import json

        try:
            return [str(step) for step in json.loads(self.DEFAULT_ESCALATION_LADDER)]
        except json.JSONDecodeError:
            return ["reminder_1", "reminder_2", "final_notice", "human_handoff"]

    @property
    def cors_origins(self) -> list[str]:
        """Allowed browser origins, parsed from the comma-separated setting.

        Accepts a JSON array too, so an existing ``CORS_ORIGINS=["https://x"]``
        in someone's environment keeps working.
        """

        text = self.CORS_ORIGINS.strip()
        if not text:
            return []
        if text.startswith("["):
            import json

            try:
                return [str(origin) for origin in json.loads(text)]
            except json.JSONDecodeError:
                return []
        return [origin.strip() for origin in text.split(",") if origin.strip()]

    @model_validator(mode="after")
    def _reject_unsafe_production(self) -> "Settings":
        """Refuse to boot production with placeholder secrets or DEBUG on.

        The placeholder values below are useful locally and dangerous in
        production -- ``noreply@yourdomain.com`` in particular would try to send
        mail from a domain we do not own. Raising here stops the process at
        boot, which a deploy surfaces as a failed release, rather than at 3am on
        the first real send.
        """

        if not self.is_production:
            return self

        placeholders = {
            "DATABASE_URL": "user:password@localhost",
            "RAZORPAY_KEY_ID": "rzp_test_xxxxxxxxxxxxxxxx",
            "RAZORPAY_KEY_SECRET": "xxxxxxxxxxxxxxxxxxxxxxxx",
            "RAZORPAY_WEBHOOK_SECRET": "xxxxxxxxxxxxxxxx",
            "RESEND_API_KEY": "re_xxxxxxxxxxxxxxxxxxxx",
            "RESEND_FROM_EMAIL": "yourdomain.com",
            "RESEND_WEBHOOK_SECRET": "whsec_xxxxxxxxxxxxxxxxxxxxxxxx",
            "REPLY_INBOUND_DOMAIN": "reply.yourdomain.com",
            "REPLY_ADDRESS_SECRET": "change-me-reply-address-secret",
        }
        unset = [
            name for name, marker in placeholders.items() if marker in str(getattr(self, name, ""))
        ]
        if unset:
            raise ValueError(
                "APP_ENV=production but these still hold placeholder values: "
                + ", ".join(sorted(unset))
                + ". Set them, or run with APP_ENV=development."
            )
        if self.DEBUG:
            raise ValueError(
                "DEBUG must be False in production: it publishes /docs and "
                "widens CORS. Unset DEBUG or set it to false."
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.lower() == "production"

    @property
    def database_url_async(self) -> str:
        """Return the database URL using the async driver this project installs.

        Neon and Supabase hand out bare ``postgresql://`` URLs, and older
        configs may still name asyncpg. Both are normalised to psycopg, which
        is the driver ``pyproject.toml`` installs -- otherwise SQLAlchemy tries
        to import a driver that is not there. This normalisation is why the
        project depends on ``psycopg[binary]`` and not ``asyncpg``: for a while
        it pinned asyncpg while rewriting every URL to psycopg, so a clean
        install could not import ``app.models`` at all.
        """
        url = self.DATABASE_URL
        for prefix in ("postgresql+asyncpg://", "postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix) :]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()

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
        # CRITICAL FIX: Ignore empty strings from environment variables.
        # When Vercel/Render set empty env vars for unconfigured optional
        # secrets, we fall back to defaults instead of failing to parse.
        # This allows production deploys with partial configuration.
        env_ignore_empty=True,
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
        description="Minimum days between contacts for same invoice (statutory floor: 2)",
        ge=2,
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

    # Autonomous batch runs
    #: How often the external cron is expected to call
    #: ``POST /api/v1/tasks/run-batch``. Recorded here so the run summary can
    #: say what cadence it believes it is on; nothing in-process reads it as a
    #: timer. An in-process scheduler double-fires the moment a second replica
    #: starts, so the schedule lives outside the app and the app defends itself
    #: with an advisory lock instead.
    SCHEDULER_INTERVAL_SECONDS: int = Field(
        default=300,
        description="Expected interval between external cron triggers",
        ge=60,
    )
    #: Shared secret for the task endpoints. They send real email, so they are
    #: authenticated even though the rest of the API is not yet -- an open
    #: run-batch endpoint is an open endpoint that mails your customers.
    TASK_API_KEY: str = Field(
        default="",
        description="Bearer token the cron trigger must present",
    )
    #: Operator bearer token for dashboard/read-write API routes. When empty,
    #: those routes fall back to TASK_API_KEY so a single-operator deploy
    #: needs only one secret. Documented separately so a larger team can
    #: rotate dashboard access without touching the cron secret.
    API_KEY: str = Field(
        default="",
        description="Bearer token for operator API routes (falls back to TASK_API_KEY)",
    )
    #: Per-IP sliding-window caps for unauthenticated-touchable endpoints
    #: (ingest + both webhooks). Process-local; a multi-replica deploy
    #: should put a shared limiter (Redis) in front. See app/core/ratelimit.py.
    RATE_LIMIT_PER_MINUTE: int = Field(default=60, ge=1, le=1000)
    #: Shared rate-limiter Redis URL (redis[s]://user:pass@host:port/db).
    #: When unset, ``check_allowed`` falls back to the in-process deque.
    #: Opt-in so a deploy without Redis behaves exactly like today.
    RATE_LIMIT_REDIS_URL: str | None = Field(
        default=None,
        description="Redis URL for shared sliding-window rate limiter",
    )
    #: OTLP HTTP endpoint for OpenTelemetry traces (e.g. Grafana Cloud Tempo
    #: gateway). Unset disables network export; traces go to stdout JSON only.
    OTEL_EXPORTER_OTLP_ENDPOINT: str | None = Field(
        default=None,
        description="OTLP HTTP exporter endpoint for Grafana Cloud / Tempo",
    )
    #: OTel service name. Grafana TraceQL filters by this, so keep it stable.
    OTEL_SERVICE_NAME: str = Field(
        default="recoup",
        description="OpenTelemetry service.name resource attribute",
    )
    #: Single-tenant identifier, reserved for the future multi-tenant path.
    #: v1 is deliberately single-tenant (see docs/tenancy.md); this value is
    #: recorded on batch-run records and surfaced in /tasks/status so a later
    #: tenancy migration has a stable owner key to split on.
    BUSINESS_ID: str = Field(default="default")

    # Drift detection (nightly job)
    #: Trailing window the drift scorer aggregates over, in days. Split into
    #: three equal periods to match the feature schema's trend columns.
    DRIFT_WINDOW_DAYS: int = Field(default=90, ge=30)
    #: Operator mailbox for drift alerts. Empty means alerts are rendered and
    #: logged only -- the nightly job never guesses where to send mail.
    DRIFT_ALERT_EMAIL: str = Field(default="")
    #: Most invoices one triggered run may touch. A cron that fires while the
    #: previous run is still going should find a bounded amount of work, not a
    #: whole book. Calibrated by the Wave 6 load test (docs/load_test.md): the
    #: 30s wall-p95 SLO breaks at N ~= 2400 open invoices, so the cap sits at
    #: 80% of that. Do not raise it without a new measured pass.
    BATCH_MAX_INVOICES: int = Field(default=1920, gt=0)

    #: The kill switch. Set to false and every outbound message stops, with no
    #: redeploy and no code change. Deliberately separate from ``DRY_RUN``:
    #: dry run is a *development* mode that still exercises the caps and the
    #: ladder, whereas this is an operational stop that advances nothing -- so
    #: turning it back on resumes where the agent left off rather than finding
    #: every invoice a rung further along.
    SENDING_ENABLED: bool = Field(
        default=True,
        description="Master switch for all outbound contact",
    )

    # ---------------------------------------------------------------------------
    # Wave 2: ERP Integration Settings
    # ---------------------------------------------------------------------------

    # Zoho Books OAuth2
    ZOHO_CLIENT_ID: str = Field(
        default="",
        description="Zoho OAuth2 client ID (from Zoho API Console)",
    )
    ZOHO_CLIENT_SECRET: str = Field(
        default="",
        description="Zoho OAuth2 client secret",
    )
    ZOHO_REDIRECT_URI: str = Field(
        default="http://localhost:8000/api/v1/integrations/zoho/connect",
        description="OAuth2 redirect URI registered in Zoho API Console",
    )

    # QuickBooks Online OAuth2 (Intuit)
    QBO_CLIENT_ID: str = Field(
        default="",
        description="QuickBooks Online Intuit OAuth2 client ID",
    )
    QBO_CLIENT_SECRET: str = Field(
        default="",
        description="QuickBooks Online Intuit OAuth2 client secret",
    )
    QBO_REDIRECT_URI: str = Field(
        default="http://localhost:8000/api/v1/integrations/quickbooks/connect",
        description="OAuth2 redirect URI registered in Intuit Developer Console",
    )
    QBO_ENVIRONMENT: str = Field(
        default="sandbox",
        description='QuickBooks environment: "sandbox" or "production"',
    )

    # ERP sync behaviour
    ERP_SYNC_LOOKBACK_DAYS: int = Field(
        default=90,
        ge=1,
        description="How many days of overdue invoices to pull on each ERP sync",
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
        # TASK_API_KEY guards the run-batch endpoint, which sends real email.
        # An empty value, the word ``change-me``, or the literal field name all
        # mean "nobody set this yet" and must refuse to boot in production.
        task_key = str(self.TASK_API_KEY or "")
        if task_key in ("", "TASK_API_KEY") or "change-me" in task_key.lower():
            unset.append("TASK_API_KEY")
        # Reply understanding needs at least one LLM path. Refusing when both
        # are unset turns "no model configured" into a failed release instead
        # of a production deploy that routes every reply to human review
        # because no one noticed the key was missing.
        if not self.GROQ_API_KEY and not self.GEMINI_API_KEY:
            unset.append("LLM keys (GROQ_API_KEY or GEMINI_API_KEY)")
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
        if url.startswith("sqlite://"):
            return "sqlite+aiosqlite://" + url[len("sqlite://") :]
        for prefix in ("postgresql+asyncpg://", "postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix) :]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()

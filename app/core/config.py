from functools import lru_cache

from pydantic import Field
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
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"

    # Database
    DATABASE_URL: str = Field(
        default="postgresql+psycopg://user:password@localhost:5432/recoup",
        description="PostgreSQL connection string for Neon/Supabase",
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
    def is_production(self) -> bool:
        return self.APP_ENV.lower() == "production"

    @property
    def database_url_async(self) -> str:
        """Return the database URL using the async driver this project installs.

        Neon and Supabase hand out bare ``postgresql://`` URLs, and older
        configs may still name asyncpg. Both are normalised to psycopg, which
        is what pyproject pins -- otherwise SQLAlchemy tries to import a driver
        that is not there.
        """
        url = self.DATABASE_URL
        for prefix in ("postgresql+asyncpg://", "postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix) :]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()

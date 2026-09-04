"""Production secrets management with provider auto-detection.

Three providers, detected by environment/config:
1. **Render Environment Groups** - detected by RENDER=true + RENDER_SERVICE_ID
2. **Doppler** - detected by DOPPLER_TOKEN
3. **AWS Secrets Manager** - detected by AWS_REGION/AWS_ACCESS_KEY_ID

Each provider implements runtime secret injection, replacing .env dependency.
Secrets are loaded on startup and optionally refreshed on a schedule.

The system gracefully degrades:
- If no provider is detected, falls back to environment variables
- If provider access fails, uses cached values or environment fallback
- Production deployments MUST set secrets via provider, not .env files

Secret rotation procedures are provider-specific but follow the same pattern:
1. Update secret in provider
2. Trigger application restart/refresh
3. Verify new secret is loaded
4. Update dependent services (schedulers, etc.)
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class SecretValue:
    """A secret value with metadata."""

    value: str
    source: str  # 'render', 'doppler', 'aws_sm', 'env', 'cache'
    last_updated: str | None = None
    version: str | None = None


class SecretProvider(ABC):
    """Abstract base for secret providers."""

    @abstractmethod
    async def load_secrets(self, secret_names: list[str]) -> dict[str, SecretValue]:
        """Load secrets by name from the provider."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this provider is configured and available."""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name for logging."""
        pass


class RenderSecretsProvider(SecretProvider):
    """Render Environment Groups secrets provider.

    Uses Render's Environment Groups API to fetch secrets at runtime.
    Requires RENDER_API_KEY and RENDER_SERVICE_ID.
    """

    def __init__(self):
        self.api_key = os.getenv("RENDER_API_KEY")
        self.service_id = os.getenv("RENDER_SERVICE_ID")

    def is_available(self) -> bool:
        return bool(os.getenv("RENDER") == "true" and self.api_key and self.service_id)

    @property
    def name(self) -> str:
        return "render"

    async def load_secrets(self, secret_names: list[str]) -> dict[str, SecretValue]:
        """Load secrets from Render Environment Groups API."""
        if not self.is_available():
            return {}

        try:
            import httpx

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
            }

            async with httpx.AsyncClient() as client:
                # Get environment variables for service
                url = f"https://api.render.com/v1/services/{self.service_id}/env-vars"
                response = await client.get(url, headers=headers)
                response.raise_for_status()

                env_vars = response.json()
                secrets = {}

                for var in env_vars:
                    name = var.get("name", "")
                    if name in secret_names:
                        secrets[name] = SecretValue(
                            value=var.get("value", ""),
                            source="render",
                            last_updated=var.get("updatedAt"),
                            version=var.get("id"),
                        )

                logger.info("Loaded secrets from Render", count=len(secrets))
                return secrets

        except Exception as exc:
            logger.warning("Failed to load secrets from Render", error=str(exc))
            return {}


class DopplerSecretsProvider(SecretProvider):
    """Doppler secrets provider.

    Uses Doppler CLI or API to fetch secrets at runtime.
    Requires DOPPLER_TOKEN.
    """

    def __init__(self):
        self.token = os.getenv("DOPPLER_TOKEN")

    def is_available(self) -> bool:
        return bool(self.token)

    @property
    def name(self) -> str:
        return "doppler"

    async def load_secrets(self, secret_names: list[str]) -> dict[str, SecretValue]:
        """Load secrets from Doppler API."""
        if not self.is_available():
            return {}

        try:
            import httpx

            headers = {
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
            }

            async with httpx.AsyncClient() as client:
                # Download config as JSON
                url = "https://api.doppler.com/v3/configs/config/secrets/download"
                params = {"format": "json"}
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()

                all_secrets = response.json()
                secrets = {}

                for name in secret_names:
                    if name in all_secrets:
                        secrets[name] = SecretValue(
                            value=str(all_secrets[name]),
                            source="doppler",
                            last_updated=None,  # Doppler doesn't provide this in download
                            version=None,
                        )

                logger.info("Loaded secrets from Doppler", count=len(secrets))
                return secrets

        except Exception as exc:
            logger.warning("Failed to load secrets from Doppler", error=str(exc))
            return {}


class AWSSecretsManagerProvider(SecretProvider):
    """AWS Secrets Manager provider.

    Uses boto3 to fetch secrets at runtime.
    Requires AWS credentials (IAM role or access keys).
    """

    def __init__(self):
        self.region = os.getenv("AWS_REGION", "us-east-1")

    def is_available(self) -> bool:
        # Check if AWS credentials are available
        return bool(
            os.getenv("AWS_ACCESS_KEY_ID")
            or os.getenv("AWS_ROLE_ARN")
            or os.path.exists(os.path.expanduser("~/.aws/credentials"))
        )

    @property
    def name(self) -> str:
        return "aws_sm"

    async def load_secrets(self, secret_names: list[str]) -> dict[str, SecretValue]:
        """Load secrets from AWS Secrets Manager."""
        if not self.is_available():
            return {}

        try:
            import boto3
            from botocore.exceptions import ClientError

            client = boto3.client("secretsmanager", region_name=self.region)
            secrets = {}

            for name in secret_names:
                try:
                    # Map config names to AWS secret names
                    secret_name = f"recoup/prod/{name}"

                    response = client.get_secret_value(SecretId=secret_name)
                    secrets[name] = SecretValue(
                        value=response["SecretString"],
                        source="aws_sm",
                        last_updated=response.get("CreatedDate", "").isoformat()
                        if response.get("CreatedDate")
                        else None,
                        version=response.get("VersionId"),
                    )

                except ClientError as exc:
                    if exc.response["Error"]["Code"] != "ResourceNotFoundException":
                        logger.warning(
                            "Failed to load secret from AWS SM", secret=name, error=str(exc)
                        )
                    # Continue with other secrets

            logger.info("Loaded secrets from AWS Secrets Manager", count=len(secrets))
            return secrets

        except Exception as exc:
            logger.warning("Failed to load secrets from AWS Secrets Manager", error=str(exc))
            return {}


class SecretManager:
    """Central secret manager with provider auto-detection and fallback."""

    def __init__(self):
        self.providers = [
            RenderSecretsProvider(),
            DopplerSecretsProvider(),
            AWSSecretsManagerProvider(),
        ]
        self.cache: dict[str, SecretValue] = {}
        self.active_provider: SecretProvider | None = None

    async def load_all_secrets(self) -> dict[str, str]:
        """Load all application secrets and return as env-compatible dict."""

        # Standard secret names expected by the application
        secret_names = [
            "DATABASE_URL",
            "TASK_API_KEY",
            "API_KEY",
            "RAZORPAY_KEY_ID",
            "RAZORPAY_KEY_SECRET",
            "RAZORPAY_WEBHOOK_SECRET",
            "RESEND_API_KEY",
            "RESEND_FROM_EMAIL",
            "RESEND_WEBHOOK_SECRET",
            "REPLY_INBOUND_DOMAIN",
            "REPLY_ADDRESS_SECRET",
            "GROQ_API_KEY",
            "GEMINI_API_KEY",
            "ZOHO_CLIENT_ID",
            "ZOHO_CLIENT_SECRET",
            "QBO_CLIENT_ID",
            "QBO_CLIENT_SECRET",
            "RATE_LIMIT_REDIS_URL",
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "OTEL_EXPORTER_OTLP_HEADERS",
        ]

        # Try providers in order until one works
        for provider in self.providers:
            if provider.is_available():
                try:
                    secrets = await provider.load_secrets(secret_names)
                    if secrets:  # Got at least some secrets
                        self.active_provider = provider
                        self.cache.update(secrets)
                        logger.info("Using secrets provider", provider=provider.name)
                        break
                except Exception as exc:
                    logger.warning("Provider failed", provider=provider.name, error=str(exc))

        # Build final env dict with fallbacks
        env_dict = {}

        for name in secret_names:
            if name in self.cache:
                env_dict[name] = self.cache[name].value
            else:
                # Fallback to environment variable
                env_value = os.getenv(name)
                if env_value:
                    env_dict[name] = env_value
                    self.cache[name] = SecretValue(value=env_value, source="env")

        logger.info(
            "Secret loading complete",
            provider=self.active_provider.name if self.active_provider else "env_fallback",
            loaded_count=len(env_dict),
        )

        return env_dict

    async def refresh_secrets(self) -> bool:
        """Refresh secrets from active provider. Returns True if successful."""
        if not self.active_provider:
            return False

        try:
            secret_names = list(self.cache.keys())
            new_secrets = await self.active_provider.load_secrets(secret_names)

            if new_secrets:
                self.cache.update(new_secrets)
                logger.info("Secrets refreshed", provider=self.active_provider.name)
                return True

        except Exception as exc:
            logger.warning("Failed to refresh secrets", error=str(exc))

        return False

    def get_secret(self, name: str) -> str | None:
        """Get a single secret value from cache."""
        if name in self.cache:
            return self.cache[name].value
        return os.getenv(name)  # Fallback


# Global instance
_secret_manager: SecretManager | None = None


async def get_secret_manager() -> SecretManager:
    """Get or create the global secret manager."""
    global _secret_manager
    if _secret_manager is None:
        _secret_manager = SecretManager()
    return _secret_manager


async def inject_secrets_into_env() -> None:
    """Load secrets from provider and inject into os.environ.

    Called during application startup to replace .env dependency.
    """
    manager = await get_secret_manager()
    secrets = await manager.load_all_secrets()

    # Update os.environ so pydantic-settings picks them up
    for name, value in secrets.items():
        if value:  # Only set non-empty values
            os.environ[name] = value

    logger.info("Secrets injected into environment", count=len(secrets))


# Rotation procedures (for runbook documentation)
ROTATION_PROCEDURES = {
    "render": {
        "steps": [
            "Update secret in Render Environment Groups UI",
            "Trigger service restart via Render Dashboard",
            "Verify new secret loaded via /api/v1/tasks/status",
            "Update GitHub Actions secrets if using scheduler workflow",
        ],
        "verify_command": "curl -sS https://$HOST/api/v1/tasks/status -H 'Authorization: Bearer $NEW_TASK_API_KEY'",
    },
    "doppler": {
        "steps": [
            "Update secret: doppler secrets set TASK_API_KEY <new> --project=recoup --config=prod",
            "Restart application container to refresh environment",
            "Update scheduler config: doppler secrets set TASK_API_KEY <new> --project=recoup --config=scheduler",
            "Verify with curl command",
        ],
        "verify_command": "curl -sS https://$HOST/api/v1/tasks/status -H 'Authorization: Bearer $NEW_TASK_API_KEY'",
    },
    "aws_sm": {
        "steps": [
            "Update secret: aws secretsmanager put-secret-value --secret-id recoup/prod/TASK_API_KEY --secret-string <new>",
            "Force ECS task deployment or restart Render/EC2 instance",
            "Update scheduler secrets using same AWS SM path",
            "Verify with curl command",
        ],
        "verify_command": "curl -sS https://$HOST/api/v1/tasks/status -H 'Authorization: Bearer $NEW_TASK_API_KEY'",
    },
}

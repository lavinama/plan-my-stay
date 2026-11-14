"""Settings from the environment. Secrets are wrapped so they never reach a log, and checked at startup."""

import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class MissingSecretError(RuntimeError):
    """Names the missing variables, never a value."""


class Secret:
    """A string that refuses to print itself. Call `reveal()` at the one place that sends it."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret(***)"

    __str__ = __repr__


def _secret(env: Mapping[str, str], name: str) -> Secret | None:
    value = (env.get(name) or "").strip()
    return Secret(value) if value else None


def _text(env: Mapping[str, str], name: str, default: str | None = None) -> str | None:
    value = (env.get(name) or "").strip()
    return value or default


@dataclass(frozen=True)
class Settings:
    google_maps_api_key: Secret | None = None  # held only by the Routing seat
    keenable_api_key: Secret | None = None
    superlinked_api_key: Secret | None = None  # SIE
    cognee_api_key: Secret | None = None
    cognee_tenant_id: Secret | None = None
    anthropic_api_key: Secret | None = None  # the Concierge's frontier model
    cognee_base_url: str | None = None
    cognee_dataset_id: str | None = None  # a UUID: a name would silently create your own dataset
    superlinked_base_url: str = "https://api.superlinked.com"
    band_config_path: str = "agent_config.yaml"
    fee_reserve_pct: float = 0.15  # placeholder: the team picks it (docs/PLAN.md, open decisions)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Settings":
        dataset_id = _text(env, "COGNEE_DATASET_ID")
        if dataset_id is not None:
            try:
                uuid.UUID(dataset_id)
            except ValueError as exc:
                raise ValueError("COGNEE_DATASET_ID must be the dataset's UUID, not its name") from exc

        reserve = float(_text(env, "FEE_RESERVE_PCT", "0.15"))
        if not 0 <= reserve <= 1:
            raise ValueError("FEE_RESERVE_PCT must be a fraction between 0 and 1")

        return cls(
            google_maps_api_key=_secret(env, "GOOGLE_MAPS_API_KEY"),
            keenable_api_key=_secret(env, "KEENABLE_API_KEY"),
            superlinked_api_key=_secret(env, "SUPERLINKED_API_KEY"),
            cognee_api_key=_secret(env, "COGNEE_API_KEY"),
            cognee_tenant_id=_secret(env, "COGNEE_TENANT_ID"),
            anthropic_api_key=_secret(env, "ANTHROPIC_API_KEY"),
            cognee_base_url=_text(env, "COGNEE_BASE_URL"),
            cognee_dataset_id=dataset_id,
            superlinked_base_url=_text(env, "SUPERLINKED_BASE_URL", "https://api.superlinked.com"),
            band_config_path=_text(env, "BAND_CONFIG_PATH", "agent_config.yaml"),
            fee_reserve_pct=reserve,
        )

    @classmethod
    def load(cls, env_file: str | Path = ".env", environ: Mapping[str, str] | None = None) -> "Settings":
        """`.env` first, then the real environment on top (it wins)."""
        merged = {**load_dotenv_file(env_file), **(os.environ if environ is None else environ)}
        return cls.from_env(merged)

    def require(self, first: str, *rest: str) -> str | tuple[str, ...]:
        """Return the value(s) of required settings, or raise naming every missing variable (fail at startup)."""
        names = (first, *rest)
        values = {name: getattr(self, name) for name in names}
        missing = [name.upper() for name, value in values.items() if value is None]
        if missing:
            raise MissingSecretError(
                f"Missing required environment variable(s): {', '.join(missing)}. "
                "Copy .env.example to .env and fill them in; never commit .env."
            )
        revealed = tuple(v.reveal() if isinstance(v, Secret) else v for v in values.values())
        return revealed[0] if not rest else revealed


def load_dotenv_file(path: str | Path) -> dict[str, str]:
    """Parse KEY=VALUE lines (comments, `export`, and quotes allowed). A missing file is an empty dict."""
    file = Path(path)
    if not file.is_file():
        return {}
    parsed: dict[str, str] = {}
    for raw_line in file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, _, value = line.partition("=")
        value = value.strip()
        if value[:1] in {'"', "'"}:
            closing = value.find(value[0], 1)
            value = value[1:closing] if closing != -1 else value[1:]
        else:
            value = value.split(" #", 1)[0].strip()
        parsed[key.strip()] = value
    return parsed

"""Environment contract.

Every variable the API process actually reads is declared here as a field.
Two categories of variable in `.env.example` are deliberately absent, and
`tests/test_config_parity.py` pins the exception list so it cannot grow by
accident:

  * `VITE_*` — read by the frontend build, never by Python.
  * `MONGODB_USERNAME` / `MONGODB_PASSWORD` — documented as available for a
    script that builds `MONGO_URI` from parts. No script does that yet, so
    nothing reads them. `MONGO_URI` is what the app actually uses.

Secrets come from the environment only (NFR-06); nothing here is defaulted to
a working credential.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # Razorpay
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    razorpay_base_url: str = "https://api.razorpay.com"

    # MongoDB
    mongo_uri: str = "mongodb://127.0.0.1:27017"
    mongo_db: str = "salvage"

    # LLM — diagnosis only. Never selects a money action (invariant 2).
    llm_provider: str = "groq"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_fallback_provider: str = ""
    llm_fallback_base_url: str = ""
    llm_fallback_api_key: str = ""
    llm_fallback_model: str = ""
    llm_template_model: str = ""
    # Skips the LLM call entirely when true — the normaliser's output passes
    # straight through as the diagnosis. This is arm C (SPEC §5.10): Salvage
    # with LLM diagnosis disabled. Never affects invariant 2 either way — the
    # normaliser already wins over anything but UNKNOWN regardless of this flag.
    disable_llm: bool = False

    # Safety controls. DRY_RUN defaults true (invariant 8) — this default is
    # load-bearing, not cosmetic. Do not change it to make a demo easier.
    dry_run: bool = True
    arm_confirm_token: str = ""
    kill_switch: bool = False
    # The live scheduler tick, in seconds. 0 disables it and no loop starts.
    # Off by default for the same reason DRY_RUN is on by default: a background
    # process that dispatches money actions should not begin because someone
    # ran the server. api/scheduler/live.py.
    scheduler_tick_seconds: int = 0
    audit_chain_key: str = ""

    # App
    app_env: str = "dev"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    log_level: str = "INFO"

    # Clocks. Stored UTC, reasoned in IST, zone always rendered (invariant 10).
    tz: str = "UTC"
    compliance_tz: str = Field(default="Asia/Kolkata")

    # Local tunnel
    public_webhook_url: str = ""
    ngrok_authtoken: str = ""


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()

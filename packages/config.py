from __future__ import annotations

from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str
    redis_url: str
    gemini_api_key: str = ""

    # /chat orchestration knobs
    chat_max_turns: int = 8
    chat_max_verify_retries: int = 2
    chat_total_timeout_s: float = 60.0
    chat_llm_timeout_s: float = 30.0
    chat_llm_retries: int = 2

    # Per-stage model selection (orchestrator). gemini-3.1-flash-lite is GA
    # and defaults to thinking_level=minimal — fastest 3.x option.
    chat_planner_model: str = "gemini-3.1-flash-lite"
    chat_joiner_model: str = "gemini-3.1-flash-lite"
    chat_composer_model: str = "gemini-3.1-flash-lite"

    # Per-request guardrails (MAST + Anthropic taxonomies).
    chat_request_token_budget: int = 50_000
    chat_max_replans: int = 1
    chat_per_task_timeout_s: float = 15.0

    shopify_base_url: str = "http://localhost:9000/shopify"
    meta_base_url: str = "http://localhost:9000/meta"
    shiprocket_base_url: str = "http://localhost:9000/shiprocket"

    shopify_shop_domain: str = ""
    shopify_access_token: str = ""
    shopify_api_version: str = "2026-01"
    shopify_webhook_secret: str = ""

    meta_access_token: str = ""
    meta_ad_account_id: str = ""
    meta_graph_base_url: str = "https://graph.facebook.com"
    meta_api_version: str = "v19.0"

    shiprocket_email: str = ""
    shiprocket_password: str = ""
    shiprocket_api_base_url: str = "https://apiv2.shiprocket.in"

    def shopify_connector_config(self, merchant: str = "m000") -> dict[str, Any]:
        if self.shopify_shop_domain and self.shopify_access_token:
            return {
                "mode": "real",
                "base_url": f"https://{self.shopify_shop_domain}",
                "shop_domain": self.shopify_shop_domain,
                "api_version": self.shopify_api_version,
                "access_token": self.shopify_access_token,
            }
        return {
            "mode": "mock",
            "base_url": self.shopify_base_url,
            "merchant": merchant,
            "shop_domain": f"{merchant}.myshopify.com",
            "api_version": self.shopify_api_version,
        }

    def meta_connector_config(self, merchant: str = "m000") -> dict[str, Any]:
        if self.meta_access_token and self.meta_ad_account_id:
            return {
                "mode": "real",
                "base_url": self.meta_graph_base_url,
                "api_version": self.meta_api_version,
                "ad_account": self.meta_ad_account_id.removeprefix("act_"),
                "access_token": self.meta_access_token,
            }
        return {
            "mode": "mock",
            "base_url": self.meta_base_url,
            "api_version": self.meta_api_version,
            "ad_account": merchant,
            "access_token": "mock-meta-token",
        }

    def shiprocket_connector_config(self, merchant: str = "m000") -> dict[str, Any]:
        if self.shiprocket_email and self.shiprocket_password:
            return {
                "mode": "real",
                "base_url": self.shiprocket_api_base_url,
                "email": self.shiprocket_email,
                "password": self.shiprocket_password,
            }
        return {
            "mode": "mock",
            "base_url": self.shiprocket_base_url,
            "email": "demo@shoppin.app",
            "password": "demo",
            "merchant": merchant,
        }


settings = Settings()  # type: ignore[call-arg]

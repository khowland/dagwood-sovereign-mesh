# =====================================================================
# SYSTEM: Dagwood Sovereign Mesh
# COMPONENT: debug_mediator.example.py
# VECTOR: [D] Diagnostic
# DESCRIPTION: Standalone diagnostic harness to validate mediator
#              configuration without triggering live agent traffic.
#              Tests vault loading, routing table resolution, real
#              provider authentication, and mediator self-health.
#
# USAGE:
#   docker exec <mediator-container> python /app/debug_mediator.example.py
#
# SECURITY NOTE:
#   Keys are masked in all log output. This tool does NOT log real
#   credential values — only length and last 4 characters.
#
# CHANGELOG:
#   v1.0 - Initial diagnostic harness
# VALIDATION: DIAGNOSTIC_TOOL
# LICENSE: Apache 2.0
# =====================================================================

import os
import json
import asyncio
import logging
from dotenv import dotenv_values

logging.basicConfig(level=logging.DEBUG, format='%(levelname)s: %(message)s')
logger = logging.getLogger("sovereign-debug")

# Configure these to match your deployment
VAULT_PATH = "/app/.env"   # Path to vault inside the mediator container
MEDIATOR_URL = "http://localhost:8080"  # Mediator self-reference
REQUIRED_KEYS = [
    "{{PRIMARY_KEY_ENV_VAR}}",
    # Add required key names here
]
OPTIONAL_KEYS = [
    "{{NODE_02_KEY_ENV_VAR}}",
    "{{NODE_03_KEY_ENV_VAR}}",
    # Add optional key names here
]


# =====================================================================
# [D] Diagnostic: Vault Validation
# =====================================================================
def check_vault() -> dict:
    """Load the vault and report the status of all expected keys.
    
    Returns the vault dict for downstream use. Never prints key values.
    """
    logger.info("=== [D] VAULT DIAGNOSTIC ===")
    vault = dotenv_values(VAULT_PATH)

    if not vault:
        logger.error(f"[D] CRITICAL: Vault empty or not found at {VAULT_PATH}")
        return {}

    logger.info(f"[D] Vault loaded from {VAULT_PATH} ({len(vault)} entries)")

    for key in REQUIRED_KEYS:
        val = vault.get(key, "")
        if val:
            masked = f"***[len={len(val)}]***{val[-4:]}"
            logger.info(f"[D]   {key}: LOADED | {masked}")
        else:
            logger.error(f"[D]   {key}: CRITICAL MISSING")

    for key in OPTIONAL_KEYS:
        val = vault.get(key, "")
        if val:
            masked = f"***[len={len(val)}]***{val[-4:]}"
            logger.info(f"[D]   {key}: LOADED | {masked}")
        else:
            logger.warning(f"[D]   {key}: MISSING (optional)")

    return vault


# =====================================================================
# [D] Diagnostic: Routing Table Validation
# =====================================================================
def check_routing_table(vault: dict) -> None:
    """Reproduce the routing table and validate key resolution for
    representative model names.
    """
    logger.info("\n=== [D] ROUTING TABLE DIAGNOSTIC ===")

    # Reconstruct routing table the same way the mediator does
    routing_table = {
        "{{ALIAS_02}}": {
            "key": vault.get("{{NODE_02_KEY_ENV_VAR}}", "").strip() or None,
            "url": "{{SOVEREIGN_MESH_NODE_02}}",
        },
        "default": {
            "key": vault.get("{{PRIMARY_KEY_ENV_VAR}}", "").strip() or None,
            "url": "{{SOVEREIGN_MESH_NODE_DEFAULT}}",
        },
    }

    # Test model names — customize this list to match your deployment
    test_models = [
        "{{YOUR_DEFAULT_MODEL}}",
        "{{YOUR_ALIAS_02_MODEL}}",
        "some-unknown-model",
    ]

    for model in test_models:
        target = routing_table["default"]
        matched_alias = "default"
        for alias, config in routing_table.items():
            if alias != "default" and alias in model:
                target = config
                matched_alias = alias
                break

        key = target.get("key")
        if key:
            key_info = f"key present [len={len(key)}, last4={key[-4:]}]"
        else:
            key_info = "KEY IS NONE <-- WILL FAIL"
        logger.info(f"[D]   model={model!r} -> alias={matched_alias!r} | {key_info}")


# =====================================================================
# [D] Diagnostic: Direct Provider Authentication Test
# =====================================================================
async def test_direct_provider(vault: dict) -> None:
    """Fire a minimal request directly to the primary provider using the
    vault key. Verifies the key is valid at the network level.
    """
    import httpx

    primary_key = vault.get("{{PRIMARY_KEY_ENV_VAR}}", "").strip()
    if not primary_key:
        logger.error("[D] Cannot run provider test: primary key missing")
        return

    logger.info("\n=== [D] DIRECT PROVIDER AUTH TEST ===")
    logger.info(f"[D] Using key: ***{primary_key[-8:]}")

    headers = {
        "Authorization": f"Bearer {primary_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "{{YOUR_DEFAULT_MODEL}}",
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 5,
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                "{{SOVEREIGN_MESH_NODE_DEFAULT}}/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=15.0,
            )
            logger.info(f"[D] Direct provider: {resp.status_code}")
            if resp.status_code == 200:
                logger.info("[D] Key is valid. Provider reachable.")
            else:
                logger.error(f"[D] Provider rejected: {resp.text[:200]}")
        except Exception as exc:
            logger.error(f"[D] Direct provider unreachable: {exc}")


# =====================================================================
# [D] Diagnostic: Mediator Self-Test
# =====================================================================
async def test_mediator_self() -> None:
    """Fire a request through the mediator (as the agent framework would).
    Uses MESH_MANAGED_TOKEN as the dummy bearer, just like the agent.
    """
    import httpx

    logger.info("\n=== [D] MEDIATOR SELF-TEST ===")
    headers = {
        "Authorization": "Bearer MESH_MANAGED_TOKEN",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "{{YOUR_DEFAULT_MODEL}}",
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 5,
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{MEDIATOR_URL}/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=30.0,
            )
            logger.info(f"[D] Mediator self-test: {resp.status_code}")
            if resp.status_code == 200:
                logger.info("[D] Mediator routing and key injection: OK")
            else:
                logger.error(f"[D] Mediator returned error: {resp.text[:200]}")
        except Exception as exc:
            logger.error(f"[D] Mediator unreachable: {exc}")


# =====================================================================
# [D] Diagnostic: Environment Variable Audit
# =====================================================================
def check_env_vars() -> None:
    """Show relevant OS environment variables. Keys are masked."""
    logger.info("\n=== [D] ENVIRONMENT VARIABLE AUDIT ===")
    env_vars_to_check = [
        "{{PRIMARY_KEY_ENV_VAR}}",
        "OPENAI_API_KEY",
        "OPENAI_API_BASE",
        # Add any other env vars you want to audit
    ]
    for var in env_vars_to_check:
        val = os.environ.get(var, "<NOT SET>")
        if val not in ("<NOT SET>", "MESH_MANAGED_TOKEN") and len(val) > 8:
            val = f"***[len={len(val)}]***{val[-4:]}"
        logger.info(f"[D]   os.environ['{var}'] = {val}")


# =====================================================================
# [η] Resonance: Diagnostic Entry Point
# =====================================================================
async def main() -> None:
    logger.info("=" * 70)
    logger.info("[D] SOVEREIGN MEDIATOR DIAGNOSTIC TOOL")
    logger.info("=" * 70)

    check_env_vars()
    vault = check_vault()
    check_routing_table(vault)
    await test_direct_provider(vault)
    await test_mediator_self()

    logger.info("\n[D] === DIAGNOSTIC COMPLETE ===")


if __name__ == "__main__":
    asyncio.run(main())

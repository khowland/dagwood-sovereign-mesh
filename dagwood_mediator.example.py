# =====================================================================
# SYSTEM: Dagwood Sovereign Mesh
# COMPONENT: dagwood_mediator.example.py
# VECTOR: [T] Transformer
# DESCRIPTION: Core logic skeleton for the zero-trust LLM gateway.
#              Replace all {{PLACEHOLDER}} values with your configuration.
#              This is the sanitized public reference implementation.
#
# ARCHITECTURE:
#   AI Agent (LLM client) --> this mediator --> real LLM providers
#   The agent sends MESH_MANAGED_TOKEN. The mediator replaces it with
#   the real vault key. The agent never holds real credentials.
#
# REQUIREMENTS:
#   pip install fastapi uvicorn httpx python-dotenv
#
# USAGE:
#   docker exec <mediator-container> python dagwood_mediator.py
#
# CHANGELOG:
#   v1.0  - Initial implementation. Basic routing + vault injection.
#   v1.1  - Added enhanced audit logging for incoming/outgoing headers.
#   v1.2  - Added provider-required telemetry headers passthrough.
#   v2.0  - /health endpoint, key null guards, startup validation log.
#   v2.1  - /v1/models passthrough. Proper response forwarding (no wrapping).
#           Settings injection pattern documented and implemented.
#
# VALIDATION: STAGE_3_PRODUCTION
# LICENSE: Apache 2.0
# =====================================================================

import os
import json
import logging
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, Response
from dotenv import dotenv_values
import httpx
import uvicorn


# =====================================================================
# [Lo] Logic: Enterprise Logging Configuration
# =====================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s'
)
logger = logging.getLogger("sovereign-mediator")


# =====================================================================
# [Ac] Actuator: FastAPI Application Bootstrap
# =====================================================================
app = FastAPI(
    title="Dagwood Sovereign Mediator",
    description=(
        "Zero-trust gateway for Dagwood Sovereign Mesh. "
        "Manages provider transitions and credential isolation."
    ),
    version="2.1.0"
)


# =====================================================================
# [T] Transformer: Audit Log Formatter
# Masks credential values before writing to log stream to comply with
# zero-trust logging policy. Last 4 chars preserved for correlation.
# =====================================================================
def log_audit(event: str, details: dict) -> None:
    """Logs a structured, masked audit event for enterprise traceability."""
    safe = {}
    for k, v in details.items():
        if isinstance(v, dict):
            safe[k] = {
                ik: (f"Bearer ***{iv[-4:]}" if ik.lower() == "authorization" else iv)
                for ik, iv in v.items()
            }
        else:
            safe[k] = v
    logger.info(f"[AUDIT] {event} | {json.dumps(safe)}")


# =====================================================================
# [St] Storage: Vault Initialization
# Load from read-only mount. Validate required keys at startup.
# System is fail-secure: missing vault = all requests fail with 401.
# =====================================================================
VAULT_PATH = "/app/.env"  # Adjust to match your container volume mount
vault = dotenv_values(VAULT_PATH)

if not vault:
    logger.critical(f"[St] CRITICAL: Vault empty or missing at {VAULT_PATH}")
    logger.critical("[St] All requests will fail. Verify volume mount.")
else:
    logger.info(f"[St] Vault loaded from {VAULT_PATH}")
    # Log key ingestion status (never log values)
    for key in ["{{PRIMARY_KEY_ENV_VAR}}", "{{OPTIONAL_KEY_ENV_VAR_1}}"]:
        status = "LOADED" if vault.get(key) else "MISSING"
        logger.info(f"[St] Key [{key}]: {status}")


# =====================================================================
# [Lo] Logic: Multi-Host Routing Table
# Maps model name substring aliases to upstream endpoints + vault keys.
# The "default" entry catches all requests not matched by another alias.
#
# TO ADD A PROVIDER:
#   1. Add its key to dagwood_stack.env and this routing table
#   2. Choose a unique alias substring that appears in the model name
#   3. Set the target URL to the provider's completions endpoint
# =====================================================================
_primary_key = str(vault.get("{{PRIMARY_KEY_ENV_VAR}}", "")).strip() or None
_node_02_key = str(vault.get("{{NODE_02_KEY_ENV_VAR}}", "")).strip() or None
_node_03_key = str(vault.get("{{NODE_03_KEY_ENV_VAR}}", "")).strip() or None

ROUTING_TABLE = {
    # Format: "alias-substring": {"key": vault_key, "url": endpoint_url}
    "{{ALIAS_02}}": {
        "key": _node_02_key,
        "url": "{{SOVEREIGN_MESH_NODE_02}}/v1/chat/completions",
    },
    "{{ALIAS_03}}": {
        "key": _node_03_key,
        "url": "{{SOVEREIGN_MESH_NODE_03}}/v1/chat/completions",
    },
    "default": {
        "key": _primary_key,
        "url": "{{SOVEREIGN_MESH_NODE_DEFAULT}}/v1/chat/completions",
    },
}

# --- [η] Startup: Emit routing table status ---
logger.info("[Lo] Routing table initialized:")
for alias, config in ROUTING_TABLE.items():
    key_ok = f"key present (last4={config['key'][-4:]})" if config["key"] else "NO KEY"
    logger.info(f"  [{alias}] -> {config['url']} | {key_ok}")


# =====================================================================
# [η] Resonance: Health Check Endpoint
# Returns vault and routing table status. Never exposes key values.
# =====================================================================
@app.get("/health")
async def health_check():
    """[η] Returns system health: vault loaded, keys present, routing ready."""
    routing_status = {
        alias: {"url": cfg["url"], "key_loaded": bool(cfg["key"])}
        for alias, cfg in ROUTING_TABLE.items()
    }
    return JSONResponse(content={
        "status": "ok" if _primary_key else "degraded",
        "vault_loaded": bool(vault),
        "primary_key_loaded": bool(_primary_key),
        "routing_table": routing_status,
    })


# =====================================================================
# [Ac] Actuator: Model List Passthrough
# LLM client libraries (e.g., litellm) may call /v1/models on startup.
# Without this endpoint, clients may fall back to direct provider calls.
# =====================================================================
@app.get("/v1/models")
async def list_models():
    """[Ac] Passthrough: forwards model list request to primary provider."""
    if not _primary_key:
        raise HTTPException(status_code=503, detail="Mediator: Primary key not loaded")
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "{{SOVEREIGN_MESH_NODE_DEFAULT}}/v1/models",
            headers={
                "Authorization": f"Bearer {_primary_key}",
                "HTTP-Referer": "http://dagwood-mediator",
                "X-Title": "Dagwood Sovereign Mesh",
            },
            timeout=15.0,
        )
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type"),
    )


# =====================================================================
# [Ac] Actuator: Chat Completions Proxy — Core Handler
#
# SECURITY PROTOCOL:
#   - Incoming Authorization header is ALWAYS IGNORED
#   - Outgoing Authorization ALWAYS comes from vault
#   - These two values never intersect
#
# DESIGN NOTE:
#   MESH_MANAGED_TOKEN is an expected dummy credential. It is a signal
#   that the client correctly configured api_base to this mediator.
#   It does NOT grant any access — the vault key does.
# =====================================================================
@app.post("/v1/chat/completions")
async def proxy_request(request: Request):
    """[Ac] Core proxy: replaces dummy token with vault key, routes upstream."""

    # --- [T] Parse and Audit Incoming Request ---
    log_audit("Incoming Request", {
        "client_host": request.client.host if request.client else "unknown",
        "headers": dict(request.headers),
    })

    try:
        raw_body = await request.body()
        body = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError as exc:
        logger.error(f"[Lo] Rejected: invalid JSON payload. Error: {exc}")
        raise HTTPException(status_code=400, detail=f"Mediator: Invalid JSON - {exc}")

    # --- [Lo] Routing Resolution ---
    requested_model = body.get("model", "default")
    logger.info(f"[Lo] Requested model: {requested_model!r}")

    target = ROUTING_TABLE["default"]
    matched_alias = "default"
    for alias, config in ROUTING_TABLE.items():
        if alias != "default" and alias in requested_model:
            target = config
            matched_alias = alias
            break

    logger.info(f"[Lo] Route: model={requested_model!r} -> {matched_alias!r} -> {target['url']!r}")

    # --- [SECURITY] Vault Key Guard ---
    if not target["key"]:
        logger.error(f"[St] VAULT FAILURE: No key for alias={matched_alias!r}")
        raise HTTPException(status_code=401, detail="Mediator: Missing Credential in Vault")

    # --- [T] Build Outgoing Headers ---
    # Authorization ALWAYS from vault. Incoming Authorization ALWAYS ignored.
    outgoing_headers = {
        "Authorization": f"Bearer {target['key']}",
        "Content-Type": "application/json",
        # Forward optional telemetry headers if provider requires them
        "HTTP-Referer": request.headers.get("http-referer", "http://dagwood-mediator"),
        "X-Title": request.headers.get("x-title", "Dagwood Sovereign Mesh"),
    }
    log_audit("Outgoing Request", {
        "url": target["url"],
        "model": requested_model,
        "matched_alias": matched_alias,
        "headers": dict(outgoing_headers),
        "payload_preview": str(body)[:300],
    })

    # --- [Ac] Upstream Dispatch ---
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                target["url"],
                json=body,
                headers=outgoing_headers,
                timeout=90.0,
            )
        except Exception as exc:
            logger.error(f"[η] Upstream unreachable: {exc}")
            raise HTTPException(status_code=502, detail=f"Mediator: Upstream Unreachable - {exc}")

    # --- [St] Audit Upstream Response ---
    if response.status_code == 200:
        logger.info(f"[Lo] Upstream success: {response.status_code}")
    else:
        log_audit("Upstream Provider Error", {
            "status_code": response.status_code,
            "provider": target["url"],
            "model": requested_model,
            "response_body": response.text[:500],
        })

    # --- [T] Forward Response ---
    # Forward the raw JSON response and status code unchanged.
    # Never wrap in {"error": ...} — LLM clients parse the raw body.
    try:
        content = response.json()
    except Exception:
        content = {"_raw": response.text}

    return JSONResponse(content=content, status_code=response.status_code)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)

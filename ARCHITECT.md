# ARCHITECT.md
## Dagwood Sovereign Mesh - Architecture Reference

---

## Design Philosophy

The single governing constraint behind this architecture is this: no AI agent process should ever hold a real API key.

Modern LLM orchestration libraries have a deeply embedded pattern of discovering credentials through environment variable conventions and using them to call provider APIs directly. This made sense when you were running a single model with a single key, but it falls apart fast when you have multiple providers, multiple billing accounts, regulatory audit requirements, and a need to rotate keys without redeploying every container that touches a model. Every container running an agent becomes its own credential exposure surface. The blast radius of a key leak is proportional to how many places that key lives.

Dagwood collapses that surface to one point. The mediator is the only component that ever sees a real credential. Everything else gets a static dummy token that is meaningless to any real provider.

---

## The Five Vector Schema

Every component in the codebase is annotated using a five-category tagging system designed for traceability and audit. The tags appear in comments throughout the source and in headers. The idea is that you should be able to look at any behavior, any log line, any endpoint, and immediately know which vector owns it.

### [St] Storage

Storage covers the persistence layer: the vault file, settings files, and any configuration that gets read from disk at startup.

The most important [St] rule in this system is that the vault is loaded once at process startup, validated immediately, and the resulting key objects are stored as module-level constants. There are no runtime vault reads. If the vault is missing or a required key is empty, the system emits a CRITICAL log and all requests fail with 401. Fail-secure, not fail-open.

```
Startup sequence:
  1. Load vault from /app/.env via dotenv_values()
  2. Check required keys, log CRITICAL for each missing one
  3. Check optional keys, log WARNING for each missing one
  4. Build routing table constants from validated keys
  5. Log routing table status (masked) to audit stream
```

### [Lo] Logic

Logic covers routing decisions, request validation, and business rules. In the mediator, this is primarily the routing table and the model name resolution that determines which upstream provider gets the request.

The routing strategy uses substring matching on model name aliases. When a request comes in, the model name is checked against each alias in the routing table in order. The first alias found as a substring of the model name wins, and that entry's key and URL are used. If nothing matches, it falls through to the default entry.

```python
# [Lo] Routing Resolution Pattern
ROUTING_TABLE = {
    "{{ALIAS_01}}": {"key": ..., "url": "{{SOVEREIGN_MESH_NODE_01}}"},
    "{{ALIAS_02}}": {"key": ..., "url": "{{SOVEREIGN_MESH_NODE_02}}"},
    "default":      {"key": ..., "url": "{{SOVEREIGN_MESH_NODE_DEFAULT}}"},
}
```

An important design note here: the routing table is built once from [St] data at startup. The keys in the table are the actual credential strings, not references that get looked up on each request. This keeps the hot path simple and prevents any vault interaction during request handling.

### [T] Transformer

Transformer covers all data shaping, which in this context means one thing more than anything else: credential replacement. The incoming Authorization header is captured for logging and then discarded. The outgoing Authorization header is always built from the vault key. These are two separate variables and they never interact.

Masking standard for logs: `Bearer ***{last4}`. The last four characters are exposed for correlation purposes (e.g., identifying which key is in rotation), but nothing more.

```
[T] Header transformation at request time:
  IN:  Authorization: Bearer MESH_MANAGED_TOKEN   <-- logged, then ignored
  OUT: Authorization: Bearer <vault_key>           <-- always from vault

  IN:  HTTP-Referer: <client value>               <-- forwarded as-is
  IN:  X-Title: <client value>                    <-- forwarded as-is
```

The response transformer is worth noting too. When the upstream returns a non-200, the raw JSON body from the provider is forwarded to the client unchanged. This matters because LLM client libraries like litellm parse the error body to determine exception type. If you wrap it in a generic `{"error": ...}` envelope, the client loses context and the exception mapping breaks.

### [Ac] Actuator

Actuator covers all external-facing interfaces: HTTP endpoint definitions, upstream HTTP dispatch, and container port configuration.

Port policy is strict. The mediator service has no host port binding in the compose configuration. It exists inside the `dagwood-mesh` Docker bridge network and is addressable only by other containers in that network. Temporarily exposing it for diagnostics is explicitly documented as a config comment, so it's a visible, intentional decision rather than an accidental one.

Endpoints:

- `POST /v1/chat/completions` - Core proxy, credential injection, upstream dispatch
- `GET /v1/models` - Model list passthrough; required because litellm calls this on startup and will fall back to direct provider calls if it returns 404
- `GET /health` - Vault and routing table status; key presence reported as boolean, no values exposed

### [η] Resonance

Resonance covers system-state signals, health, and lifecycle behavior.

The health signal is simple: `{"status": "ok"}` when the primary key is loaded, `{"status": "degraded"}` when it is not. Degraded does not mean down. It means you should check your vault mount before the next request fails.

Failure classification used in logs:

- `CRITICAL` - Vault file missing or empty at startup
- `ERROR` - Vault key missing for a requested alias at request time
- `502` - Upstream provider unreachable (network error)
- `401` from mediator - Missing credential in vault, request correctly blocked

---

## Zero-Trust Architecture Layers

The threat model has four layers, each independently enforced.

1. **Container Isolation.** The mediator is not addressable from the host network. The agent container cannot initiate a connection to any real provider URL because those are only accessible from the internet, not from the internal mesh network. The only internal service the agent can reach is the mediator.

2. **Credential Isolation.** The agent process holds `MESH_MANAGED_TOKEN` for all provider key variables. This token is meaningless to any real LLM provider. The mediator vault, mounted read-only into the mediator container only, holds the real keys. Keys are never transmitted between containers.

3. **Request Interception.** All outbound LLM calls from the agent go through the mediator. This is enforced by the `api_base` setting in the agent settings file, which is mounted read-only so the agent cannot overwrite it at runtime. Without that file in place, agent frameworks default to an empty `api_base` and call provider APIs directly with whatever credential they find in the environment. The README covers how to set this up correctly.

4. **Audit Compliance.** Every request is logged on the way in and on the way out. Keys are masked in all log output. Startup validation is logged. Upstream errors are captured with provider URL, model name, alias, and response body. You can reconstruct what happened for any request from the log stream alone.

---

## Configuration Injection Strategy

Getting the `api_base` correctly set in an agent framework is less obvious than it should be. The framework reads from a settings file if it exists, falls back to defaults if it does not. The defaults set `api_base` to an empty string. With an empty `api_base`, litellm ignores it and uses the provider's default base URL.

The solution is a pre-configured settings file stored in the host workspace and mounted read-only into the container at the precise path the framework reads from. This file sets `api_base` to the mediator URL for every model type (chat, utility, browser, embedding). It survives container restarts. It cannot be overwritten by the agent at runtime because it is mounted read-only.

```yaml
# docker-compose.yaml (pattern)
volumes:
  - ./agent_settings.json:/path/to/framework/settings.json:ro
```

The path varies by agent framework. You need to know where your framework reads its persisted settings from and mount the file there.

---

## Enterprise Commenting Standards

Every source file carries this header:

```python
# =====================================================================
# SYSTEM: <System Name>
# COMPONENT: <filename>
# VECTOR: [<tag>] <Vector Name>
# DESCRIPTION: <What this does>
# CHANGELOG:
#   v1.0 - Initial implementation
#   v2.0 - Added X, fixed Y
# VALIDATION: <STAGE_N_NAME>
# =====================================================================
```

Inline blocks use the vector tag as a section prefix:

```python
# --- [Lo] Routing Resolution ---
# --- [T] Build Outgoing Headers ---
# --- [St] Vault Initialization ---
# --- [Ac] Upstream Dispatch ---
```

This makes grep-based log analysis straightforward. You can filter log lines by vector tag and immediately see the full picture for that layer without wading through unrelated output.

---

## Validation Stage Progression

| Stage | Label | Description |
|-------|-------|-------------|
| 0 | `STAGE_0_CONCEPT` | Design only |
| 1 | `STAGE_1_REFLEX` | Basic wiring, minimal validation |
| 2 | `STAGE_2_IMPLEMENTATION` | Working implementation, audit hooks in place |
| 3 | `STAGE_3_PRODUCTION` | Full audit logging, health endpoints, changelog tracking |
| 4 | `STAGE_4_HARDENED` | Mutual TLS, rate limiting, circuit breakers |

Current state: **STAGE_3_PRODUCTION**

---

## Extension Points

Adding a provider means adding one routing table entry and one vault key. That's all it takes.

Other natural extension points:

- **Rate limiting**: Add a [Lo] sliding window counter per alias before upstream dispatch
- **Circuit breaker**: Add an [η] failure counter per alias, auto-disable the alias after a threshold
- **mTLS**: Add [T] cert injection for providers that support it
- **Response caching**: Add a [St] TTL cache layer in front of upstream dispatch for idempotent requests

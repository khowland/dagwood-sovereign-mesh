# Dagwood Sovereign Mesh

A zero-trust gateway for running AI agent workloads across multiple LLM providers without agents ever touching real API credentials.

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

---

## The Problem

If you've spent any time running LLM agent frameworks in production, you know that libraries like litellm resolve API credentials directly from environment variables and call provider endpoints without any intermediary. In a setup with multiple providers, multiple billing accounts, and any kind of audit requirement, this creates a credential surface that's basically impossible to control. Every container that runs an agent becomes a potential key exposure point. It's not a great situation.

Dagwood solves this by inserting a mediator layer that is the only component that ever holds real credentials. The agent processes get static dummy tokens. All traffic routes through the mediator, which performs key injection before forwarding upstream. That's the whole idea.

---

## Architecture

```
+---------------------------------------------------------------+
|                   Dagwood Sovereign Mesh                      |
|                                                               |
|  +------------------+        +-----------------------------+  |
|  |  Agent Node      |        |  Sovereign Mediator         |  |
|  |                  |        |                             |  |
|  |  LLM Framework   +------->+  /v1/chat/completions       |  |
|  |  (litellm etc.)  |        |  /v1/models                 |  |
|  |                  |        |  /health                    |  |
|  |  api_key =       |        |                             |  |
|  |  MESH_MANAGED_   |        |  [St] Vault --> Real Key    |  |
|  |  TOKEN           |        |  [Lo] Routing Table        |  |
|  +------------------+        +-------------+---------------+  |
|                                            |                  |
|                             +--------------v---------------+  |
|                             |   Upstream LLM Providers    |  |
|                             |                             |  |
|                             |  {{SOVEREIGN_MESH_NODE_01}} |  |
|                             |  {{SOVEREIGN_MESH_NODE_02}} |  |
|                             |  {{SOVEREIGN_MESH_NODE_03}} |  |
|                             +-----------------------------+  |
+---------------------------------------------------------------+
```

Three things are guaranteed by this design. The agent never holds a real API key. All outbound requests pass through the mediator. The mediator port is not exposed to the host network.

---

## Five Vector Schema

The codebase uses a structured annotation system called the Five Vector Schema for tagging responsibilities across components. Every block of logic carries one of these tags so you can trace any behavior back to a specific vector during debugging or audit.

| Vector | Tag | What it covers |
|--------|-----|----------------|
| Storage | `[St]` | Vault loading, credential access, persistence |
| Logic | `[Lo]` | Routing, business rules, request validation |
| Transformer | `[T]` | Data shaping, header injection, credential masking |
| Actuator | `[Ac]` | HTTP endpoints, upstream dispatch, container ports |
| Resonance | `[η]` | System state, health signals, lifecycle |

See [ARCHITECT.md](ARCHITECT.md) for the full breakdown.

---

## Security Model

### Credential Isolation

The agent container environment looks like this:

```
OPENAI_API_KEY=MESH_MANAGED_TOKEN      # dummy, rejected by real providers
OPENROUTER_API_KEY=MESH_MANAGED_TOKEN  # dummy, rejected by real providers
OPENAI_API_BASE=http://dagwood-mediator:8080/v1  # all traffic lands here
```

The mediator vault, mounted read-only at runtime, holds the real keys. The mediator ignores the incoming Authorization header entirely and builds a fresh one from the vault before sending upstream. Those two things never touch each other.

### Port Isolation

The mediator service has no host port mapping. It only exists on the internal `dagwood-mesh` Docker bridge network. The agent has no credentials that would be accepted by a real provider, so even if it tried to call one directly, the request would fail. The combination of dummy tokens and a forced `api_base` is what makes this work.

### The Settings Injection Problem

This one took some digging to find. LLM frameworks read model configuration from a settings file and fall back to defaults if that file doesn't exist. The defaults typically have an empty `api_base`, which causes litellm to bypass the mediator entirely and call provider endpoints directly with whatever credential it finds in the environment. Since the agent has `MESH_MANAGED_TOKEN` for all provider keys, those calls fail with a 401.

The fix is a pre-configured settings file mounted read-only into the exact path the framework reads from. That file sets `api_base` to the mediator URL for every model type (chat, utility, browser, embed). It survives container restarts because it lives in the host workspace.

---

## Quick Start

```bash
# Clone and enter
git clone https://github.com/{{YOUR_GITHUB_USERNAME}}/dagwood-sovereign-mesh.git
cd dagwood-sovereign-mesh

# Set up your vault (never commit the real file)
mkdir .dagwood_vault
cp dagwood_stack.env.example .dagwood_vault/dagwood_stack.env
# Edit .dagwood_vault/dagwood_stack.env and fill in your real keys

# Configure agent settings
cp agent_settings.json.example agent_zero_data/settings.json
# Adjust model names to match your providers

# Start the mesh
docker compose up -d

# Verify the mediator loaded your keys (run from inside the mesh network)
docker exec dagwood-mediator sh -c "wget -q -O- http://localhost:8080/health"
```

---

## Repository Layout

```
dagwood-sovereign-mesh/
+-- dagwood_mediator.example.py     [T] Core gateway skeleton
+-- docker-compose.example.yaml     [Ac] Orchestration template
+-- Dockerfile.mediator.example     [Ac] Container build spec
+-- dagwood_stack.env.example       [St] Vault configuration template
+-- agent_settings.json.example     [St] Settings injection template
+-- debug_mediator.example.py            Diagnostic harness
+-- README.md
+-- ARCHITECT.md
+-- LICENSE
```

---

## License

Apache 2.0. See [LICENSE](LICENSE).

# Phase 8 — Orbit remote MCP connectivity investigation

**Status:** Investigation only. No remote transport, tunnel, deployment, or authentication code has been enabled. The local stdio workflow remains the supported implementation.

## Client and account support

The intended browser assistant was treated as ChatGPT web based on the project goal. The available execution environment is Codex desktop; it does not expose the target ChatGPT plan, workspace, developer-mode setting, or OpenAI Platform tunnel permissions. Those details must be confirmed by a workspace administrator before a connection can be configured.

OpenAI's current documentation says full MCP support, including write actions, is in beta for ChatGPT Business, Enterprise, and Edu on the web. Pro currently supports MCP read/fetch access, not full write-capable MCP. ChatGPT does not connect directly to a local MCP process. OpenAI documents Secure MCP Tunnel as a way for supported OpenAI products to call private or locally hosted MCP servers.

Sources:

- [Developer mode and MCP apps in ChatGPT](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt)
- [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
- [MCP authentication for OpenAI plugins](https://developers.openai.com/plugins/build/auth)

## Current repository assumptions

- `src/orbit/mcp_server/server.py` starts FastMCP with stdio transport only.
- `README.md` documents local stdio startup and configuration.
- `src/orbit/database/engine.py` uses SQLite. `MEMORY_BRIDGE_DB_PATH` selects the local database file; the default path is relative to the process working directory.
- No HTTP listener, deployment manifest, remote authentication, project registry, or identity-to-project membership policy was found in the repository.
- Existing tools validate the supplied project string and pass it to persistence or retrieval adapters. Project scoping in repositories/retrieval prevents query leakage between requested namespaces, but the project string itself does not prove the caller is authorized for that project.

## Architecture comparison

| Option | Security and data boundary | Reliability and operations | Fit |
|---|---|---|---|
| **Secure MCP Tunnel to the local stdio server** | Keeps the MCP process and SQLite file on the local host and does not require an inbound public listener. The tunnel client makes outbound HTTPS requests to OpenAI. ChatGPT tool results still travel through OpenAI when requested, so private records returned by tools are shared with that assistant. The tunnel does not replace per-user project authorization. | Requires the host and tunnel client to stay online, a tunnel ID, a runtime API key, Platform tunnel permissions, and ChatGPT workspace access. A developer machine is a single point of failure. The stdio server can remain unchanged if the tunnel client launches it. Its working directory and `MEMORY_BRIDGE_DB_PATH` must be configured so it opens the same SQLite file as the IDE process. | Best candidate for a limited pilot after client eligibility, access policy, and data-sharing consent are confirmed. No tunnel has been created or run. |
| **Authenticated remote MCP service** | Uses a public HTTPS MCP endpoint protected by user authentication and server-side authorization. It must never expose the SQLite file itself. The service would use private/managed persistence and enforce user-to-project access on every operation. | Provides better uptime and multi-user availability, but requires deployment ownership, OAuth/identity provider configuration, database operations, backups, migrations, monitoring, rate limits, incident response, and ongoing cost. It centralizes the sensitive memory store. | Better fit for a durable shared team service, after the team approves the data boundary, hosting, and operational owner. |

## Recommendation and blockers

For an initial remote proof of concept, evaluate Secure MCP Tunnel first while keeping stdio available for the local IDE. Do not begin tunnel setup until the target workspace plan and permissions are verified, the team approves what project data may be returned to ChatGPT, and the server has an authenticated identity-to-project authorization policy. For durable team-wide availability, evaluate a hosted authenticated service separately; that is a larger architecture change and should not replace the local MVP without team approval.

The tunnel protects network exposure but does not authenticate a caller to this application or decide which project that caller may read or write. A caller-controlled `project` argument is only a requested scope, not an authorization claim. Remote authorization and project membership are designed in [authorization-design.md](authorization-design.md).

No private conversation data was sent to a remote assistant during this investigation. Tool results from a later remote invocation would be sent to the connected assistant as requested context; the project members must explicitly approve that data flow and define its scope before using real project memory remotely.

## Decision status

- Client plan/workspace eligibility: **unverified**.
- Secure tunnel access and Platform organization permissions: **unverified**.
- Tunnel versus hosted-service selection: **not approved**.
- User identity provider, project membership source, and authorization policy: **not approved**.
- Permission to return private project memory to ChatGPT: **not approved**.
- Remote implementation or deployment: **not started**.

## Follow-up: local authorization implementation

The later local authorization work adds a fail-closed `ORBIT_LOCAL_PROJECT_ACCESS` project/action allowlist and guards all four server-facing tool adapters. This narrows which project namespaces a configured stdio server process can access, but it does not authenticate individual MCP clients. A client connected to the process inherits its grants. The phase 8 remote blockers remain: no reviewed identity provider, principal-to-project membership source, remote transport/authentication, tunnel approval, or approval to send private project memory to a remote assistant. See [authorization-design.md](authorization-design.md) for the implemented local policy and threat model.

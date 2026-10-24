# Celestin project authorization design

**Status:** Local stdio authorization is implemented as an explicit, fail-closed server-process project/action allowlist. Stdio still does not authenticate individual clients. Principal-based authorization and all remote authentication/transport controls below remain a future design; no HTTP transport, tunnel, or remote access is enabled.

## Trust boundaries and threat model

| Boundary | Trusted inputs | Enforced control | Security limit |
|---|---|---|---|
| Local stdio process | The operator-controlled server launch environment and installed Celestin code | `CELESTIN_LOCAL_PROJECT_ACCESS` grants only named projects and `read`/`write` actions; legacy `ORBIT_LOCAL_PROJECT_ACCESS` is a fallback; absence of policy denies project tools | Stdio provides no authenticated identity for each MCP client. Any client that can connect inherits the process allowlist. This does not protect against an untrusted process running as the same local user or one able to alter the launch environment/configuration. |
| MCP request | Tool arguments are untrusted, including `project` and `source_message_id` | Server-facing persistence/retrieval adapters authorize before invoking a backend; repositories and retrieval continue applying project predicates | A project name selects a namespace but never grants access. Source IDs must resolve inside that same project. |
| Resurrection Layer service | Project/action selector, candidate/event data, and source references | Evidence and sync services re-authorize every operation through an injected `ProjectAuthorizer`; their trusted factory fixes the principal | These capabilities are internal-only and not MCP-exposed. The authorizer/principal injection point is only safe when controlled by trusted application code. Reconstruction accepts an explicit root and has no project-to-repository authorization resolver. |
| Future remote transport | Only a transport-verified principal produced by a reviewed authenticator | Principal-to-project/action policy must deny by default before tool dispatch and be repeated at the server service boundary | No remote authenticator, membership provider, token verifier, or transport is implemented. Tunnel reachability alone is not identity or authorization. |

The current local policy is a coarse capability for one server process, not user authentication. Do not connect a multi-user or remote client to a process whose allowlist is broader than every connected client should access.

## Current enforcement point and gap

The four MCP handlers in `src/orbit/mcp_server/server.py` validate the `project` string and call server-facing persistence/retrieval wrappers:

- `ingest_chat_message` and `save_decision` perform project-scoped writes.
- `search_chat_history` and `retrieve_context` perform project-scoped reads.
- `save_decision` resolves `source_message_id` within the requested project and treats cross-project sources as not found.

The wrappers authorize each requested project/action before calling the underlying adapters. The local policy is read from `CELESTIN_LOCAL_PROJECT_ACCESS` (falling back to `ORBIT_LOCAL_PROJECT_ACCESS`) and defaults to deny all project operations. There is still no authenticated principal or per-client membership mapping in stdio; any process able to connect to the same server receives the same configured grants. A project parameter remains a resource selector only, never proof of authorization.

The authorization boundary belongs after transport authentication (if any) and before data access. Existing MCP server-facing wrappers authorize persistence/retrieval calls. Resurrection Layer evidence and sync services also re-authorize selectors through a configured authorizer. This does not add transport authentication, and it does not make a constructible `AuthorizedProject` proof of access. Data and retrieval layers continue to enforce project predicates; no SQL or ranking logic is duplicated.

Project-state reconstruction remains an internal read-only API: its constructor accepts both a repository root and a project identifier but does not establish that they correspond or that the caller may inspect that repository. No registered MCP tool or direct-agent entry point currently exposes it. Before exposing reconstruction, the application owner must provide a trusted project-to-repository resolver that returns a canonical allowed root for an already-authorized project. That resolver must define symlink and path traversal behavior; the current reconstructor's Git-root checks do not authenticate or authorize the root.

## Proposed interface

The authorization dependency is implemented in `src/orbit/mcp_server/authorization.py`:

```python
class ProjectAuthorizer(Protocol):
    def authorize(
        self,
        principal: AuthenticatedPrincipal | None,
        project: str,
        action: ProjectAction,
    ) -> AuthorizedProject: ...
```

Current and future policy behavior:

- `AuthenticatedPrincipal`: server-verified issuer and stable subject identifier. It is constructed only by a future reviewed authenticator; never from MCP tool arguments. Current stdio supplies `None`; it does not invent a client identity.
- `ProjectAction`: `read` or `write`. Map `search_chat_history` and `retrieve_context` to `read`; map `ingest_chat_message` and `save_decision` to `write`.
- `AuthorizedProject`: a server-issued scope containing the project and action, returned only after policy approval. Server-facing wrappers pass its project to service adapters.
- `ProjectAccessDenied`: a typed authorization failure converted by the MCP boundary into a stable, non-sensitive error. Do not reveal whether an inaccessible project exists.
- `ProjectAccessPolicy`: the current implementation. With `principal=None`, it checks the process-level allowlist. With a non-null principal, it checks exact configured principal/project/action grants; unknown identities are denied. The remote identity source and production membership mapping are not configured.

The future identity provider or verifier must validate signature, issuer, audience/resource, expiry, and required scopes. Authorization must use a server-side project membership source, such as an approved identity provider group mapping or project access registry. Missing, unavailable, or ambiguous membership data must fail closed. An arbitrary bearer string, shared hard-coded token, caller-supplied subject, or project-name allowlist supplied with the request is not acceptable authentication or authorization. The local environment allowlist is strictly process configuration, not an authentication mechanism.

No membership table or project registry currently exists in the inspected database schema. Selecting and owning the membership source is a team decision. This proposal does not prescribe changes to Member 1's schema or repositories.

## Access model

1. **Authenticate each remote request.** Validate the remote client's OAuth access token or other team-approved standards-based credential before tool dispatch. Authentication failure prevents all tool execution. For local stdio, permissions are granted to the server process and are not per-client identity checks.
2. **Authorize each operation.** Check the authenticated subject's membership for the requested project and action. Do not infer permission from tool selection, ChatGPT workspace membership, a tunnel ID, or the project argument.
3. **Deny by default.** No membership, unknown principal, unknown project, missing scope, expired credential, or policy service failure denies access.
4. **Use least privilege.** Read and write permissions are separate. A read-only client may search and retrieve but cannot ingest messages or save decisions. If project-specific roles are adopted, server-side policy maps them to actions.
5. **Preserve data-layer scoping.** Continue passing the authorized canonical project to repository and retrieval services. Source-message references must resolve within that same project. Do not trust result payloads without confirming their project matches the authorized scope.
6. **Avoid project-existence disclosure.** Return the same safe denial for an inaccessible project whether it exists or not. For a source message that is absent or belongs to another project, preserve the contract's `SOURCE_MESSAGE_NOT_FOUND` behavior.
7. **Keep the stdio transport.** The local entry point remains stdio; the new process-level project allowlist is required for project tools. Remote auth configuration must be isolated to a future remote adapter/transport and must not introduce a development bypass that can be reached remotely.

The public tool contract now defines `ACCESS_DENIED` for project/action denials. Before remote implementation, the team must decide how transport-level authentication failures use the selected protocol's normal unauthorized response. Do not send token claims, identity-provider errors, or membership details to the client.

## Write-operation safeguards

- For current stdio, `ingest_chat_message` and `save_decision` require an explicit process-level project write grant. A future remote transport must additionally require a validated identity with project write permission on every invocation.
- A denied write must fail before calling persistence; tests must verify no row or FTS entry is written.
- `save_decision` continues to create a `draft`; remote access must not imply approval. Approval remains a distinct action outside the current four-tool contract.
- Client-side confirmation prompts or tool annotations may improve user awareness, but they do not replace server authorization. Decide whether both write tools require an additional explicit confirmation in the selected client.
- Add audit events for principal ID, project ID, tool/action, outcome, and request correlation ID. Do not log conversation content, decision values, credentials, or raw tokens.
- Do not add retry behavior for writes until idempotency semantics are defined; a timeout must not silently duplicate a user operation.

## Configuration required before remote implementation

### Current local stdio policy

Set `CELESTIN_LOCAL_PROJECT_ACCESS` in the server process environment to a JSON object whose keys are exact project names and whose values are arrays containing `read`, `write`, or both. Existing configurations may use `ORBIT_LOCAL_PROJECT_ACCESS` when the Celestin variable is unset. Example:

```json
{"research":["read"],"orbit":["read","write"]}
```

Unset or empty configuration denies all four project tools. Malformed JSON or unsupported actions stop server initialization instead of widening access. Configure the variable in the local MCP server entry so the client cannot select a broader grant in tool arguments. This policy authorizes the server process, not the individual stdio client.

The exact variable names are to be set after selecting the identity provider and architecture. Configuration must include:

- Remote mode/transport explicitly enabled only in the approved deployment; stdio remains the default local workflow.
- OAuth resource/audience and trusted issuer or introspection/JWKS configuration, plus required read/write scopes.
- Project membership provider and tenant/workspace mapping, with a documented default-deny policy.
- Secret-manager or environment injection for client credentials, signing/introspection secrets, tunnel runtime keys, and database credentials. Never commit secrets or print them in logs.
- If using Secure MCP Tunnel: approved tunnel ID, Platform organization association, runtime key, required `Tunnels Read + Use` permission, ChatGPT workspace access, and a fixed local stdio command/working directory. Set `CELESTIN_DB_PATH` explicitly, or use the legacy `MEMORY_BRIDGE_DB_PATH`, so the tunnel-launched process and local IDE use the same intended database.
- If using a hosted service: HTTPS termination, network policy, private persistence (not a downloadable/public SQLite file), backups, migrations, service health checks, rate limits, and operational ownership.

Do not bind the current server to an unauthenticated network address. Tunnel access alone does not satisfy the application authorization requirements above.

## Required test plan

The implemented local test suite covers explicit allowlist grants, default denial, unknown principal denial at the policy interface, read/write action separation, altered project arguments, direct server-adapter calls, and cross-project source references. This verifies process-level scoping only; it does not verify an authenticated user because stdio supplies no trustworthy per-client principal.

Implement these as transport/auth integration tests using isolated test databases and test identities; do not use real conversation data.

| Case | Expected result | Persistence/retrieval assertion |
|---|---|---|
| Missing authorization credential | Unauthorized response; no tool result | No service method called |
| Invalid signature, expired token, wrong issuer/audience | Unauthorized response; safe body | No service method called |
| Valid identity without project membership | Generic access denial | No read result and no write |
| Member of project A requests search/context for project B | Generic access denial | B records never returned; service not called or only receives authorized scope |
| Member of project A tries to ingest/save to project B | Generic access denial | No new message, decision, or FTS row in either project |
| Member with read scope attempts a write | Forbidden/access denied | No persistence call |
| Authorized member reads own project | Success | Returned rows all have authorized project ID |
| Authorized member writes own project | Success when write policy/confirmation is satisfied | Only authorized project's rows and indexes change |
| Decision references another project's message ID | `SOURCE_MESSAGE_NOT_FOUND` | No decision created; response does not disclose other-project source |
| Membership provider unavailable or ambiguous | Safe denial/service-unavailable policy; fail closed | No adapter call |
| Remote write request lacks required confirmation, if team adopts confirmation | Safe denial/cancelled operation | No persistence call |
| Tool/authorization failure contains internal token, path, or provider detail | Sanitized MCP error | Neither response nor logs contain secret or conversation text |
| Existing local stdio workflow | Existing behavior and configuration continue | Local contract suite passes |

## Decisions requiring the three-person team

### Member 1 — Database and memory core

- Confirm whether projects are provisioned entities or arbitrary namespace strings and identify the canonical project identifier.
- Identify the owner and source of project membership (existing identity/group provider, a separately owned authorization service, or a future repository-backed registry). No authorization schema is present in the current SQLite migrations.
- Confirm the persistence boundary continues to enforce project predicates for all operations and source-message lookup, and agree whether repositories receive an authorized project scope or only the canonical project ID.
- Agree on safe behavior for unknown projects and cross-project source IDs without disclosing resource existence.

### Member 2 — Retrieval

- Confirm both retrieval entry points always constrain candidates and final results to the authorized canonical project, even when upstream index metadata is inconsistent.
- Confirm the retrieval adapter can accept the server-authorized project scope/canonical identifier without changing ranking or retrieval semantics.
- Confirm how authorization failures and retrieval outages are distinguished and sanitized at the MCP boundary.

### Member 3 — MCP server and integration

- Select and own the identity token verifier and project membership source that will provide principals and grants to the implemented authorization dependency; define issuer/audience, read/write scopes, revocation, and remote authentication failure behavior.
- Decide where transport authentication terminates and ensure remote requests cannot bypass it. Keep the stdio client path unchanged.
- Decide whether `ingest_chat_message` and `save_decision` need client confirmation/annotations in addition to server-side write authorization.
- Approve remote audit fields and any additional authentication error mapping before enabling remote transport. Local `ACCESS_DENIED` handling and its tests are implemented.

### Shared/team decisions

- Verify the actual ChatGPT workspace plan, custom MCP/developer-mode access, and Platform tunnel permissions. Full write-capable ChatGPT MCP support is plan/workspace-dependent and currently documented as beta.
- Choose Secure MCP Tunnel for a limited pilot versus a hosted HTTPS service for durable team availability; name the operational owner.
- Explicitly approve whether and which project memory may be sent to ChatGPT when a remote tool call returns context. Define user opt-in, approved projects, retention expectations, and prohibited data classes.
- Confirm team/project membership rules, read/write role semantics, authorization-denial error contract, and any user confirmation policy for writes.
- Confirm production configuration, secret management, backups/retention, audit access, revocation procedure, and incident response before deployment.

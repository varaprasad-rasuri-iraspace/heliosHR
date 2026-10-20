# HeliosHR AI-Powered Onboarding Automation: Take-Home Submission

**Idea in one line:** Workday hire event → n8n → a configurable LLM proposes access → deterministic policy gate (inside an MCP server) → idempotent provisioning of Okta / Google / Slack / Jira / FreshService → tamper-evident audit. *The LLM proposes; deterministic code disposes.*

| # | Deliverable | File |
|---|---|---|
| 1 | Architecture diagram (PNG + SVG + ASCII) | `docs/01-architecture.md`, `diagrams/architecture.png`, `diagrams/architecture.svg` |
| 2 | Technical writeup (2 pages) | `docs/02-technical-writeup.pdf` (source: `docs/02-technical-writeup.md`) |
| 3 | Prototype (Option A) + implementation spec (Option B) | `prototype/`, `n8n/onboarding_workflow.json`, `docs/03-implementation-spec.md` |
| 4 | Executive summary (2 slides) | `docs/04-executive-summary.pptx` (text version: `docs/04-executive-summary.md`) |
| 5 | AI usage statement | `docs/05-ai-usage-statement.md` |

## Run the prototype (no dependencies, no API key)
```bash
cd prototype
python3 run_demo.py                          # two hires: conflict + retry, injection blocked, 403 escalated, audit tamper test
python3 -m unittest discover -s tests -v     # 22 tests
# Optional local configuration: copy the repository's .env.example to .env,
# set HELIOS_MODEL=provider/model-id and its matching API key.
# Live LLM planner:      pip install -r requirements-llm.txt && python3 run_demo.py
# MCP server:            pip install -r requirements.txt && python3 mcp_server.py
```

The Python app loads values from the repository-root `.env` without overriding variables already supplied by the host. The real `.env` is ignored by Git. The `.env.example` contains only setting names and placeholders; no API keys are included. For live model calls, use LiteLLM provider/model names such as `anthropic/<model-id>`, `openai/<model-id>`, or `gemini/<model-id>` and the matching key. Target-system connectors remain simulated, so production vendor credentials belong in Secrets Manager rather than this local file. `WORKDAY_WEBHOOK_SECRET` must be configured in the n8n host's credential/environment store separately.

## Caveats
Connectors are simulated (no live credentials, per the brief). The n8n export is a configurable import/test placeholder and has not been imported into a live n8n instance. Some endpoints were checked against current vendor documentation, while tenant-specific scopes, permissions, and webhook availability still need confirmation. Live model calls have not been executed. See the AI usage statement and implementation spec for details.

The MCP server persists run, activation, hire-context, step, and approval state in a local SQLite database (`prototype/onboarding_state.sqlite3` by default). Two distinct approver roles are required before sensitive access is granted; an already-approved grant can be retried separately. Target-system connectors remain simulated and their state is in memory; production should use real idempotent connectors plus encrypted DynamoDB/Postgres state. Streamable HTTP fails closed unless `MCP_AUTH=on` and its Okta JWKS/issuer/audience settings are present. Local stdio mode is intended only for a trusted development process.

# 5. AI Usage Statement

Claude was used to draft the project materials and generate the initial prototype code. Codex was used to review the submission against the assignment, identify gaps, and make follow-up code and documentation changes. The optional live planner is provider-configurable through LiteLLM and supports Anthropic, OpenAI, and Gemini; no live provider call was made during this review because no provider API key is configured. The default run uses the offline mock planner.

## Where AI was used

| Work | Use |
|---|---|
| Assignment analysis and deliverable plan | Claude helped structure the design, documents, and prototype scope. |
| Architecture and implementation | Claude helped draft the architecture and initial Python, MCP, n8n, and test code. |
| Diagrams and writing | Claude helped draft the diagram and technical, implementation, and executive-summary documents. |
| Review and follow-up fixes | Codex reviewed the code and documentation, implemented approval resumption and SQLite state persistence, added configurable LLM provider support and n8n placeholders, updated the AI usage statement, and checked the vendor endpoint notes. |

## Validation and limitations

- The current Python unit suite contains 22 tests and passed during this review (`python -m unittest discover -s tests -v`). These cover deterministic policy decisions, workflow behavior, provider selection/tool-call adaptation, approval resumption, rollback, and SQLite reload. No live vendor API calls were made.
- The n8n workflow export parses as JSON, but it has not been imported into a live n8n instance, so node parameter compatibility remains unconfirmed.
- The live model API path was not run because this workspace has no configured provider API key. Set `HELIOS_MODEL` to a LiteLLM `provider/model-id` value and provide the matching provider key.
- The target systems in the runnable prototype are simulated. Workday webhook availability, tenant-specific OAuth scopes and permissions, Slack/Jira product-plan requirements, and the Freshservice ticket-to-MCP approval adapter need validation in the destination environments.
- The MCP service persists its run, hire, approval, step, and activation-target records to local SQLite. The simulated target-system state remains in memory; production requires real connectors and a shared encrypted transactional store.
- The implementation spec records endpoint checks and their sources. These checks confirm documented API methods, not access to or configuration of a customer tenant.
- The technical-writeup PDF was regenerated from the current Markdown source and checked as a two-page deliverable.

## Where AI was deliberately limited

- Access authorization stays in deterministic policy code; the model proposes entitlements but cannot approve them.
- Retry classification and connector writes remain deterministic. The model drafts human-readable triage content only.
- The model never receives target-system credentials or calls target systems directly.
- Clean department baselines use rules; the model is reserved for ambiguity and human-readable drafts.

## Design decisions to explain

1. Why a one-shot structured plan is used in the prototype instead of a free-roaming agent loop.
2. Why n8n owns triggers, waits, and alerts while the MCP service owns policy and execution.
3. How the system falls back to deterministic access when the model is unavailable or returns invalid output.
4. How planner evaluations and vendor sandbox contract tests should gate production rollout.

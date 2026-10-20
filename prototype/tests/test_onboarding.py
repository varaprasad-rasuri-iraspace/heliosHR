import hashlib
import hmac
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from onboarding import intake, orchestrator, policy  # noqa: E402
from onboarding.audit import AuditLog  # noqa: E402
from onboarding.connectors import build_systems  # noqa: E402
from onboarding.planner import Planner  # noqa: E402
from onboarding.planner import PLAN_TOOL  # noqa: E402
from onboarding.storage import SQLiteStateStore  # noqa: E402

EVENTS = Path(__file__).resolve().parents[1] / "sample_events"
TODAY = date(2026, 10, 19)


def load(n):
    return json.loads((EVENTS / n).read_text())


class PolicyTests(unittest.TestCase):
    hire = {"department": "Customer Success", "worker_type": "employee"}

    def verdicts(self, hire, ids):
        return {d["id"]: d["verdict"] for d in policy.evaluate(hire, ids)}

    def test_unknown_id_denied(self):
        self.assertEqual(self.verdicts(self.hire, ["slack:channel:exec-private"])["slack:channel:exec-private"], "DENY")

    def test_tier3_denied(self):
        self.assertEqual(self.verdicts(self.hire, ["salesforce:profile:admin"])["salesforce:profile:admin"], "DENY")

    def test_cross_department_needs_approval(self):
        self.assertEqual(self.verdicts(self.hire, ["jira:project:PLAT"])["jira:project:PLAT"], "NEEDS_APPROVAL")

    def test_contractor_restricted(self):
        c = {"department": "Engineering", "worker_type": "contractor"}
        self.assertEqual(self.verdicts(c, ["slack:channel:eng-oncall"])["slack:channel:eng-oncall"], "DENY")

    def test_baseline_always_added(self):
        v = self.verdicts(self.hire, [])
        self.assertEqual(v["okta:group:everyone"], "ALLOW")

    def test_catalog_hides_tier3_from_llm(self):
        ids = {e["id"] for e in policy.catalog_for_prompt()}
        self.assertNotIn("salesforce:profile:admin", ids)


class PlannerProviderTests(unittest.TestCase):
    def test_provider_prefix_uses_matching_key_for_anthropic_openai_and_gemini(self):
        key_names = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "gemini": "GEMINI_API_KEY",
        }
        for provider, key_name in key_names.items():
            with self.subTest(provider=provider), patch.dict(os.environ, {
                "HELIOS_MODEL": f"{provider}/example-model",
                "ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": "", "GEMINI_API_KEY": "",
                key_name: "test-key",
            }):
                planner = Planner()
                self.assertTrue(planner.live)
                self.assertEqual(planner.provider, provider)
                self.assertEqual(planner.model, f"{provider}/example-model")

    def test_model_provider_requires_its_matching_key(self):
        with patch.dict(os.environ, {
            "HELIOS_MODEL": "openai/example-model",
            "ANTHROPIC_API_KEY": "anthropic-key",
            "OPENAI_API_KEY": "",
            "GEMINI_API_KEY": "",
        }):
            self.assertFalse(Planner().live)

    def test_litellm_tool_response_is_decoded(self):
        expected = {"ok": True}
        call = SimpleNamespace(function=SimpleNamespace(name="submit_access_plan", arguments=json.dumps(expected)))
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[call]))])
        completion = Mock(return_value=response)
        planner = Planner(live=False)
        planner.model = "gemini/example-model"
        planner.provider = "gemini"
        with patch.dict(sys.modules, {"litellm": SimpleNamespace(completion=completion)}):
            result = planner._call_tool(PLAN_TOOL, "system", "user", max_tokens=120)
        self.assertEqual(result, expected)
        self.assertEqual(completion.call_args.kwargs["model"], "gemini/example-model")
        self.assertEqual(completion.call_args.kwargs["tool_choice"]["function"]["name"], "submit_access_plan")


class IntakeTests(unittest.TestCase):
    def test_minimize_drops_sensitive(self):
        hire = intake.minimize(load("hire_priya.json"))
        for k in ("ssn_last4", "date_of_birth", "home_address", "base_salary"):
            self.assertNotIn(k, hire)

    def test_signature(self):
        body, secret = b'{"a":1}', "s3cret"
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        self.assertTrue(intake.verify_signature(body, "sha256=" + sig, secret))
        self.assertFalse(intake.verify_signature(body + b" ", sig, secret))

    def test_validation(self):
        h = intake.minimize(load("hire_priya.json"))
        h["manager_email"] = None
        self.assertTrue(intake.validate(h, TODAY))


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        orchestrator.RUNS.clear()
        orchestrator.ACTIVATION_TARGETS.clear()
        orchestrator.PENDING_APPROVALS.clear()
        orchestrator.configure_state_store(None)
        self.tmp = tempfile.TemporaryDirectory()
        self.audit = AuditLog(Path(self.tmp.name) / "audit.jsonl")
        self.planner = Planner(live=False)

    def tearDown(self):
        self.tmp.cleanup()

    def run_ev(self, name, systems):
        return orchestrator.run_onboarding(load(name), planner=self.planner, systems=systems,
                                           audit=self.audit, today=TODAY, sleep=lambda s: None)

    def test_priya_conflict_and_retry_complete(self):
        systems = build_systems({"slack": {"add_grants": ["429"]}})
        systems["okta"].users["priya.nair@helioshr.com"] = {"worker_id": "W-OTHER", "status": "ACTIVE", "profile": {}}
        r = self.run_ev("hire_priya.json", systems)
        self.assertEqual(r["status"], "COMPLETE")
        self.assertIn("pnair@helioshr.com", systems["okta"].users)          # fell through to 2nd candidate
        self.assertEqual(r["retried_steps"][0]["system"], "slack")           # 429 retried
        self.assertIn("slack:channel:cs-emea", r["granted"])                 # location-based channel

    def test_marcus_injection_blocked_and_partial(self):
        systems = build_systems({"jira": {"add_grants": ["403"]}})
        r = self.run_ev("hire_marcus.json", systems)
        denied = {d["id"] for d in r["denied"]}
        self.assertTrue({"salesforce:profile:admin", "jira:role:site-admin", "slack:channel:exec-private"} <= denied)
        self.assertEqual(r["pending_approval"], ["jira:project:PLAT"])
        self.assertEqual(r["status"], "PARTIAL")
        self.assertEqual(r["failures"][0]["system"], "jira")
        granted = systems["okta"].grants["marcus.lee@helioshr.com"]
        self.assertNotIn("salesforce:profile:admin", granted)

    def test_duplicate_event_is_noop(self):
        systems = build_systems()
        self.run_ev("hire_priya.json", systems)
        n_tickets = len(systems["freshservice"].tickets)
        again = self.run_ev("hire_priya.json", systems)
        self.assertTrue(again["duplicate"])
        self.assertEqual(len(systems["freshservice"].tickets), n_tickets)

    def test_audit_chain_and_tamper_detection(self):
        self.run_ev("hire_priya.json", build_systems())
        self.assertTrue(self.audit.verify()[0])
        p = self.audit.path
        p.write_text(p.read_text().replace("within policy", "within polic9", 1))
        self.assertFalse(AuditLog(p).verify()[0])

    def test_llm_outage_falls_back_to_baseline(self):
        class DownPlanner(Planner):
            def _live_propose(self, hire):
                raise ConnectionError("anthropic API unreachable")

        planner = DownPlanner(live=True)
        systems = build_systems()
        r = orchestrator.run_onboarding(load("hire_marcus.json"), planner=planner, systems=systems,
                                        audit=self.audit, today=TODAY, sleep=lambda s: None)
        self.assertEqual(r["status"], "COMPLETE")                 # degraded but works
        self.assertTrue(any("LLM unavailable" in f for f in r["flags"]))
        self.assertEqual(r["pending_approval"], [])               # no clever cross-team asks
        self.assertNotIn("salesforce:profile:admin", r["granted"])

    def test_llm_garbage_output_falls_back(self):
        class GarbagePlanner(Planner):
            def _live_propose(self, hire):
                return {"entitlements": "not-a-list"}

        r = GarbagePlanner(live=True).propose(load("hire_priya.json")["worker"])
        self.assertTrue(any("LLM unavailable" in f for f in r["flags"]))
        self.assertIn("okta:group:cs", [e["id"] for e in r["entitlements"]])

    def test_rollback_revokes_and_suspends(self):
        systems = build_systems()
        r = self.run_ev("hire_priya.json", systems)
        orchestrator.rollback(r["run_id"], systems, self.audit, r["compensations"])
        self.assertEqual(systems["okta"].users["priya.nair@helioshr.com"]["status"], "SUSPENDED")
        self.assertFalse(systems["slack"].grants["priya.nair@helioshr.com"])

    def test_two_person_approval_resumes_and_grants_access(self):
        systems = build_systems()
        event = load("hire_marcus.json")
        result = orchestrator.run_onboarding(event, planner=self.planner, systems=systems,
                                             audit=self.audit, today=TODAY, sleep=lambda s: None)
        entitlement = "jira:project:PLAT"
        self.assertEqual(result["status"], "AWAITING_APPROVAL")
        self.assertNotIn(entitlement, systems["jira"].grants.get("marcus.lee@helioshr.com", set()))

        first = orchestrator.record_access_approval(
            event["event_id"], entitlement, "manager", True, "Role requires platform access", "manager-1",
            systems=systems, audit=self.audit, sleep=lambda s: None)
        self.assertEqual(first["state"], "pending")
        with self.assertRaises(PermissionError):
            orchestrator.record_access_approval(
                event["event_id"], entitlement, "app-owner", True, "Approved", "manager-1",
                systems=systems, audit=self.audit, sleep=lambda s: None)
        final = orchestrator.record_access_approval(
            event["event_id"], entitlement, "app-owner", True, "Approved", "app-owner-1",
            systems=systems, audit=self.audit, sleep=lambda s: None)
        self.assertEqual(final["state"], "granted")
        self.assertEqual(final["run_status"], "COMPLETE")
        self.assertIn(entitlement, systems["jira"].grants["marcus.lee@helioshr.com"])

    def test_mcp_state_store_persists_run_and_approval_across_reload(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SQLiteStateStore(Path(folder) / "state.sqlite3")
            orchestrator.configure_state_store(store)
            systems = build_systems()
            event = load("hire_marcus.json")
            result = orchestrator.run_onboarding(event, planner=self.planner, systems=systems,
                                                 audit=self.audit, today=TODAY, sleep=lambda s: None)
            self.assertEqual(result["status"], "AWAITING_APPROVAL")

            orchestrator.RUNS.clear()
            orchestrator.ACTIVATION_TARGETS.clear()
            orchestrator.PENDING_APPROVALS.clear()
            orchestrator.configure_state_store(SQLiteStateStore(Path(folder) / "state.sqlite3"))
            self.assertEqual(orchestrator.RUNS[event["event_id"]]["status"], "AWAITING_APPROVAL")
            self.assertIn((event["event_id"], "jira:project:PLAT"), orchestrator.PENDING_APPROVALS)
            self.assertEqual(orchestrator.ACTIVATION_TARGETS[event["event_id"]]["start_date"], "2026-11-02")

    def test_rollback_status_is_persisted(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SQLiteStateStore(Path(folder) / "state.sqlite3")
            orchestrator.configure_state_store(store)
            systems = build_systems()
            event = load("hire_priya.json")
            result = orchestrator.run_onboarding(event, planner=self.planner, systems=systems,
                                                 audit=self.audit, today=TODAY, sleep=lambda s: None)
            orchestrator.rollback(result["run_id"], systems, self.audit, result["compensations"])
            orchestrator.RUNS.clear()
            orchestrator.configure_state_store(SQLiteStateStore(Path(folder) / "state.sqlite3"))
            self.assertEqual(orchestrator.RUNS[event["event_id"]]["status"], "ROLLED_BACK")


if __name__ == "__main__":
    unittest.main()

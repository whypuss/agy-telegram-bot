"""Tests for updated model aliases and rate-limit / risk-control circuit breaker."""

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent_runner
from ui_components import AVAILABLE_MODELS, OPENCODE_MODELS, resolve_model_alias


class TestModelAliases(unittest.TestCase):
    def test_agy_model_aliases(self):
        agy_ids = {m["id"] for m in AVAILABLE_MODELS}
        cases = {
            "hydrogen": "Gemini 4 Hydrogen (Medium)",
            "hydrogen xhigh": "Gemini 4 Hydrogen (XHigh)",
            "4 high": "Gemini 4 Hydrogen (High)",
            "argon": "Gemini 4 Argon (Medium)",
            "argon low": "Gemini 4 Argon (Low)",
            "orchestrator": "Orchestrator (Argon)",
            "opus": "Claude Opus 5.5 (High)",
            "opus low": "Claude Opus 5.5 (Low)",
            "opus med": "Claude Opus 5.5 (Medium)",
            "sonnet": "Claude Sonnet 5.5 (Medium)",
            "sonnet high": "Claude Sonnet 5.5 (High)",
            "3.8": "Gemini 3.8 Flash (Medium)",
            "gpt": "GPT-OSS 120B (Medium)",
        }
        for query, expected in cases.items():
            resolved = resolve_model_alias(query)
            self.assertEqual(resolved, expected, f"query={query!r}")
            self.assertIn(resolved, agy_ids, f"resolved={resolved!r} missing from AVAILABLE_MODELS")

    def test_opencode_model_aliases(self):
        oc_ids = {m["id"] for m in OPENCODE_MODELS}
        cases = {
            "beam": "opencode/beam-free",
            "flint": "opencode/flint-40b-free",
            "mimo": "opencode/mimo-v2.6-flash-free",
            "deepseek 4.1": "opencode/deepseek-v4.1-flash-free",
            "glm 5.3": "opencode/glm-5.3-flash-free",
        }
        for query, expected in cases.items():
            resolved = resolve_model_alias(query)
            self.assertEqual(resolved, expected, f"query={query!r}")
            self.assertIn(resolved, oc_ids, f"resolved={resolved!r} missing from OPENCODE_MODELS")


class TestRateLimitRiskControl(unittest.TestCase):
    def setUp(self):
        agent_runner._reset_agy_rate_limit()

    def tearDown(self):
        agent_runner._reset_agy_rate_limit()

    def test_rate_limit_detection(self):
        self.assertTrue(agent_runner._is_rate_limit_error("HTTP 429 Too Many Requests"))
        self.assertTrue(agent_runner._is_rate_limit_error("RESOURCE_EXHAUSTED: quota exceeded"))
        self.assertTrue(agent_runner._is_rate_limit_error("model_capacity_exhausted"))
        self.assertFalse(agent_runner._is_rate_limit_error("SyntaxError: invalid syntax"))

    def test_exponential_backoff_and_reset(self):
        d1 = agent_runner._record_agy_rate_limit()
        d2 = agent_runner._record_agy_rate_limit()
        self.assertGreaterEqual(d1, 30.0)
        self.assertGreaterEqual(d2, 60.0)
        self.assertGreater(agent_runner.get_agy_cooldown_remaining(), 50.0)

        agent_runner._reset_agy_rate_limit()
        self.assertEqual(agent_runner.get_agy_cooldown_remaining(), 0.0)

    def test_run_agy_turn_fails_fast_during_cooldown(self):
        agent_runner._record_agy_rate_limit()
        reply, conv_id, usage = asyncio.run(agent_runner._run_agy_turn("hi", user_id=999999))
        self.assertIn("風控冷卻中", reply)
        self.assertIsNone(conv_id)
        self.assertIsNone(usage)


if __name__ == "__main__":
    unittest.main(verbosity=2)

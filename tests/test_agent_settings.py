"""Tests for the agent switch and its cost limits."""

import unittest
from decimal import Decimal

from job_finder.agent.settings import DEFAULT_LIMITS, AgentLimits, agent_settings
from job_finder.matching.user_settings import EXAMPLE_SETTINGS_PATH, load_user_settings


class AgentSettingsTests(unittest.TestCase):
    def test_agent_stays_off_without_section_or_explicit_true(self):
        for section in (None, {}, {"enabled": False}, {"enabled": "true"}, {"enabled": 1}):
            with self.subTest(section=section):
                settings = agent_settings({} if section is None else {"agent": section})

                self.assertFalse(settings.enabled)
                self.assertTrue(settings.reason)
                self.assertEqual(settings.limits, AgentLimits(**DEFAULT_LIMITS))

    def test_public_example_documents_the_defaults_and_keeps_the_agent_off(self):
        settings = agent_settings(load_user_settings(EXAMPLE_SETTINGS_PATH))

        self.assertFalse(settings.enabled)
        self.assertEqual(settings.limits, AgentLimits(**DEFAULT_LIMITS))

    def test_given_limits_replace_defaults(self):
        settings = agent_settings(
            {"agent": {"enabled": True, "job_max_model_calls": 5, "daily_max_cost_eur": 0.3}}
        )

        self.assertTrue(settings.enabled)
        self.assertEqual(settings.reason, "")
        self.assertEqual(settings.limits.job_max_model_calls, 5)
        self.assertEqual(settings.limits.daily_max_cost_eur, Decimal("0.3"))
        self.assertEqual(settings.limits.monthly_max_cost_eur, Decimal("20.00"))

    def test_invalid_limits_switch_the_agent_off_and_name_the_setting(self):
        cases = {
            "daily_max_cost_eur": 100,
            "monthly_max_cost_eur": 0,
            "job_max_model_calls": 8.5,
            "job_max_tool_calls": True,
            "job_max_cost_eur": "0.05",
        }
        for name, value in cases.items():
            with self.subTest(name=name):
                settings = agent_settings({"agent": {"enabled": True, name: value}})

                self.assertFalse(settings.enabled)
                self.assertIn(f"agent.{name}", settings.reason)
                self.assertEqual(settings.limits, AgentLimits(**DEFAULT_LIMITS))

    def test_unknown_keys_and_inconsistent_limits_switch_the_agent_off(self):
        cases = (
            ({"enabled": True, "daily_max_cost": 1}, "agent.daily_max_cost"),
            ({"enabled": True, "job_max_cost_eur": 0.2, "daily_max_cost_eur": 0.1}, "<="),
            ("an", "Objekt"),
        )
        for section, message in cases:
            with self.subTest(section=section):
                settings = agent_settings({"agent": section})

                self.assertFalse(settings.enabled)
                self.assertIn(message, settings.reason)


if __name__ == "__main__":
    unittest.main()

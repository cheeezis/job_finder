"""Release prerequisites and rollback provenance, without cloud or registry access."""

import importlib.util
import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from itertools import product
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / ".github" / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


phases = load_script("check_release_config")
rollback = load_script("check_rollback_commit")


class ReleaseConfigTests(unittest.TestCase):
    def test_only_accepted_phase_combinations_pass(self):
        runtime_pairs = {
            ("legacy", "false"),
            ("legacy", "true"),
            ("prepare", "false"),
            ("prepare", "true"),
            ("split", "true"),
        }
        accepted = {
            (runtime, verified, "password", entra) for runtime, verified in runtime_pairs for entra in ("false", "true")
        }
        accepted.update(
            {
                ("split", "true", "prepare", "false"),
                ("split", "true", "prepare", "true"),
                ("split", "true", "entra", "true"),
            }
        )
        for values in product(
            ("legacy", "prepare", "split"), ("false", "true"), ("password", "prepare", "entra"), ("false", "true")
        ):
            with self.subTest(values=values):
                environ = dict(zip(phases.PHASE_VALUES, values))
                if values in accepted:
                    phases.validate(environ)
                else:
                    with self.assertRaises(ValueError):
                        phases.validate(environ)

    def test_missing_empty_or_malformed_variables_stop_without_echoing_values(self):
        valid = dict(zip(phases.PHASE_VALUES, ("legacy", "false", "password", "false")))
        for key in valid:
            for invalid in (None, "", " true ", "TRUE", "synthetic-private-value"):
                with self.subTest(key=key, invalid=invalid):
                    environ = {**valid, key: invalid}
                    if invalid is None:
                        del environ[key]
                    output = io.StringIO()
                    with patch.object(phases.os, "environ", environ), redirect_stdout(output):
                        self.assertEqual(phases.main(), 1)
                    self.assertIn(key, output.getvalue())
                    self.assertNotIn("synthetic-private-value", output.getvalue())


class RollbackCommitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.root = Path(cls.directory.name)
        cls.git("init", "-q", "--initial-branch=main")
        cls.before = cls.commit("before")
        cls.boundary = cls.commit("boundary")
        cls.after = cls.commit("after")

    @classmethod
    def git(cls, *args):
        environ = {
            **os.environ,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }
        result = subprocess.run(
            [
                "git",
                "-c",
                "user.name=Synthetic Test",
                "-c",
                "user.email=test@example.test",
                "-c",
                "commit.gpgsign=false",
                "-c",
                f"core.hooksPath={os.devnull}",
                *args,
            ],
            cwd=cls.root,
            env=environ,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return result.stdout.strip()

    @classmethod
    def commit(cls, name):
        (cls.root / "fixture.txt").write_text(name, encoding="utf-8")
        cls.git("add", "fixture.txt")
        cls.git("commit", "-q", "-m", name)
        return cls.git("rev-parse", "HEAD")

    def check(self, tags, **kwargs):
        return rollback.check(tags, repository=self.root, contract_commit=self.boundary, **kwargs)

    def test_boundary_and_descendant_commits_pass(self):
        for commit in (self.boundary, self.after):
            with self.subTest(commit=commit):
                self.assertEqual(self.check(["latest", f"sha-{commit[:12]}"]), commit)

    def test_old_commit_requires_explicit_override_and_nonempty_reason(self):
        tag = [f"sha-{self.before[:12]}"]
        for allow, reason in ((False, "owner decision"), (True, ""), (True, "  ")):
            with self.subTest(allow=allow, reason=reason), self.assertRaisesRegex(ValueError, "vor Contract"):
                self.check(tag, allow_precontract=allow, reason=reason)
        self.assertEqual(
            self.check(tag, allow_precontract=True, reason="separate schema recovery approved"), self.before
        )

    def test_unknown_or_conflicting_commits_cannot_be_overridden(self):
        for tags in (
            [],
            ["latest"],
            ["sha-invalid"],
            ["sha-000000000000"],
            [f"sha-{self.boundary[:12]}", f"sha-{self.after[:12]}"],
        ):
            with self.subTest(tags=tags), self.assertRaises(ValueError):
                self.check(tags, allow_precontract=True, reason="owner decision")

    def test_missing_contract_boundary_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "Contract-Grenze"):
            rollback.check(
                [f"sha-{self.after[:12]}"],
                repository=self.root,
                contract_commit="0" * 40,
                allow_precontract=True,
                reason="owner decision",
            )


class WorkflowSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # BaseLoader keeps GitHub's "on" key as a string (not a YAML 1.1 boolean).
        cls.jobs = yaml.load(
            (ROOT / ".github/workflows/checks.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader
        )["jobs"]
        cls.rollback = yaml.load(
            (ROOT / ".github/workflows/rollback.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader
        )

    def test_fork_and_dependabot_prs_get_terraform_checks_without_cloud_credentials(self):
        job = self.jobs["infrastructure-checks"]
        self.assertEqual(job["if"], "github.event_name == 'pull_request'")
        self.assertEqual(job["permissions"], {"contents": "read"})
        text = str(job)
        self.assertNotIn("azure/login", text)
        self.assertNotIn("secrets.", text)
        commands = [step["run"] for step in job["steps"] if "run" in step]
        self.assertIn("terraform init -backend=false -input=false", commands)
        self.assertIn("terraform validate", commands)
        self.assertIn("terraform test -no-color", commands)
        for test in (ROOT / "infrastructure/tests").glob("*.tftest.hcl"):
            self.assertIn('mock_provider "azurerm"', test.read_text(encoding="utf-8"))

    def test_both_plan_jobs_validate_explicit_phases_before_azure_login(self):
        for name in ("terraform-plan", "release-plan"):
            with self.subTest(job=name):
                job = self.jobs[name]
                for key in phases.PHASE_VALUES:
                    self.assertIn(key, job["env"])
                    self.assertNotIn("||", job["env"][key])
                steps = job["steps"]
                gate = next(i for i, step in enumerate(steps) if "check_release_config.py" in step.get("run", ""))
                login = next(i for i, step in enumerate(steps) if step.get("uses", "").startswith("azure/login@"))
                self.assertLess(gate, login)
                for step in steps:
                    self.assertFalse(set(step.get("env", {})) & phases.PHASE_VALUES.keys())

    def test_rollback_verifies_the_exact_digest_before_any_image_update(self):
        job = self.rollback["jobs"]["rollback"]
        steps = job["steps"]
        checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
        self.assertEqual(checkout["with"]["fetch-depth"], "0")
        gate = next(i for i, step in enumerate(steps) if "check_rollback_commit.py" in step.get("run", ""))
        command = steps[gate]["run"]
        self.assertIn('"jobfinder@${IMAGE##*@}"', command)
        self.assertIn("--query tags --output tsv", command)
        self.assertIn("pipefail", command)
        self.assertNotIn("${{", command)
        updates = [i for i, step in enumerate(steps) if "--image" in step.get("run", "") and "update" in step["run"]]
        self.assertTrue(updates)
        self.assertTrue(all(gate < update for update in updates))
        inputs = self.rollback["on"]["workflow_dispatch"]["inputs"]
        self.assertEqual(inputs["allow_precontract"]["default"], "false")

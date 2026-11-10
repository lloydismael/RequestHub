import os
from pathlib import Path
import shutil
import subprocess
import unittest

from workflow_test_utils import read_run_block, ROOT

BASH = os.environ.get('TEST_BASH') or shutil.which('bash')


class FastGateTests(unittest.TestCase):
    def script(self):
        try:
            return read_run_block('container-security.yml', 'Run fast regression checks')
        except ValueError as error:
            self.fail(str(error))

    def test_node_failure_stops_before_deployment_checks(self):
        script = self.script()
        result = subprocess.run([BASH, '-e', '-o', 'pipefail', '-c',
            'node() { return 42; }; bash() { echo unexpected-deployment; };\n' + script],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertNotIn('unexpected-deployment', result.stdout)

    def test_deployment_check_failure_is_not_hidden(self):
        script = self.script()
        result = subprocess.run([BASH, '-e', '-o', 'pipefail', '-c',
            'node() { return 0; }; bash() { return 43; };\n' + script],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 43, result.stderr)

    def test_actual_fast_checks_pass(self):
        result = subprocess.run([BASH, '-e', '-o', 'pipefail', '-c', self.script()],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS: success', result.stdout)
        self.assertIn('PASS: rollback-unhealthy', result.stdout)


class ContainerRuntimeTests(unittest.TestCase):
    def test_container_uses_validated_python_runtime(self):
        dockerfile = (ROOT / 'Dockerfile').read_text(encoding='utf-8')
        workflow = (ROOT / '.github/workflows/container-security.yml').read_text(encoding='utf-8')
        self.assertRegex(dockerfile.splitlines()[0], r'^FROM python:3\.12\.\d+-alpine[\d.]+@sha256:[0-9a-f]{64}$')
        self.assertIn("python-version: '3.12'", workflow)
        self.assertIn('/usr/local/lib/python3.12/site-packages/', dockerfile)

    def test_dependabot_keeps_python_patch_updates_enabled(self):
        config = (ROOT / '.github/dependabot.yml').read_text(encoding='utf-8')
        docker_config = config.split('package-ecosystem: docker', 1)[1].split('package-ecosystem:', 1)[0]
        self.assertIn('dependency-name: python', docker_config)
        self.assertIn('version-update:semver-major', docker_config)
        self.assertIn('version-update:semver-minor', docker_config)
        self.assertNotIn('version-update:semver-patch', docker_config)


if __name__ == '__main__':
    unittest.main()

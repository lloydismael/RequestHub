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


if __name__ == '__main__':
    unittest.main()

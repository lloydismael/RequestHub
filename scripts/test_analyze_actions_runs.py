import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

try:
    from analyze_actions_runs import summarize_runs
except ImportError:
    summarize_runs = None


class AnalyzeRunsTests(unittest.TestCase):
    def summarize(self, runs, jobs):
        self.assertIsNotNone(summarize_runs, 'Timing analyzer is not implemented')
        return summarize_runs(runs, jobs)

    def run_fixture(self, run_id=1, **changes):
        return {
            'id': run_id, 'name': 'Container Security', 'head_sha': 'abc',
            'head_branch': 'dev', 'event': 'push', 'status': 'completed',
            'conclusion': 'success', 'created_at': '2026-11-04T20:00:00Z',
            'updated_at': '2026-11-04T20:02:00Z', **changes,
        }

    def job_fixture(self, name='Django Validation', **changes):
        return {
            'name': name, 'conclusion': 'success',
            'started_at': '2026-11-04T20:00:05Z',
            'completed_at': '2026-11-04T20:01:45Z', **changes,
        }

    def test_parallel_jobs_are_not_added_to_elapsed_time(self):
        result = self.summarize([self.run_fixture()], {'1': [
            self.job_fixture(), self.job_fixture('Image scan'),
        ]})
        record = result['runs'][0]
        self.assertEqual(record['elapsed_seconds'], 120)
        self.assertEqual(record['time_to_first_job_seconds'], 5)
        self.assertEqual(record['job_execution_seconds'], 200)
        self.assertEqual(record['jobs'][0]['seconds'], 100)
        self.assertEqual(result['groups'][0]['median_seconds'], 120)

    def test_duplicate_push_and_pr_are_grouped_by_sha(self):
        result = self.summarize([
            self.run_fixture(), self.run_fixture(2, event='pull_request'),
            self.run_fixture(3, head_sha='different'),
        ], {})
        self.assertEqual(result['duplicate_push_pr'], [{'sha': 'abc', 'run_ids': [1, 2]}])
        self.assertEqual(len(result['groups']), 2)

    def test_missing_jobs_remain_unknown(self):
        result = self.summarize([self.run_fixture(
            conclusion='failure', updated_at='2026-11-04T20:14:45Z',
        )], {'1': []})
        record = result['runs'][0]
        self.assertEqual(record['elapsed_seconds'], 885)
        self.assertIsNone(record['time_to_first_job_seconds'])
        self.assertIsNone(record['job_execution_seconds'])
        self.assertEqual(record['evidence'], 'missing-jobs')
        self.assertEqual(result['groups'][0]['success_samples'], 0)

    def test_failed_and_cancelled_runs_are_not_success_samples(self):
        runs = [self.run_fixture(), self.run_fixture(2, conclusion='failure'),
                self.run_fixture(3, conclusion='cancelled')]
        result = self.summarize(runs, {'1': [self.job_fixture()]})
        self.assertEqual(result['groups'][0]['success_samples'], 1)
        self.assertEqual(result['groups'][0]['median_seconds'], 120)
        self.assertEqual(result['groups'][0]['conclusions'], {'success': 1, 'failure': 1, 'cancelled': 1})

    def test_p95_requires_twenty_complete_success_samples(self):
        for count, expected in [(19, None), (20, 120)]:
            runs = [self.run_fixture(n) for n in range(count)]
            jobs = {str(n): [self.job_fixture()] for n in range(count)}
            result = self.summarize(runs, jobs)
            self.assertEqual(result['groups'][0]['p95_seconds'], expected)

    def test_incomplete_jobs_do_not_produce_execution_totals(self):
        record = self.summarize([self.run_fixture()], {'1': [
            self.job_fixture(completed_at='0001-01-01T00:00:00Z'),
        ]})['runs'][0]
        self.assertIsNone(record['job_execution_seconds'])
        self.assertEqual(record['evidence'], 'incomplete-jobs')

    def test_running_and_reversed_timestamps_do_not_enter_latency_samples(self):
        for changes in [dict(status='in_progress'), dict(updated_at='2026-11-04T19:59:00Z')]:
            record = self.summarize([self.run_fixture(**changes)], {'1': [self.job_fixture()]})
            self.assertIsNone(record['runs'][0]['elapsed_seconds'])
            self.assertEqual(record['groups'][0]['success_samples'], 0)

    def test_migration_categories_are_separate_and_unknown_is_explicit(self):
        result = self.summarize([
            self.run_fixture(1, name='Production Release', migration_window='executed'),
            self.run_fixture(2, name='Production Release', migration_window='skipped'),
            self.run_fixture(3, name='Production Release'),
        ], {})
        self.assertEqual({group['migration_window'] for group in result['groups']},
                         {'executed', 'skipped', 'unknown'})

    def test_cli_reads_metadata_without_network(self):
        self.assertIsNotNone(summarize_runs, 'Timing analyzer is not implemented')
        with tempfile.TemporaryDirectory() as directory:
            runs = Path(directory, 'runs.json')
            jobs = Path(directory, 'jobs.json')
            runs.write_text(json.dumps([self.run_fixture()]), encoding='utf-8')
            jobs.write_text(json.dumps({'1': [self.job_fixture()]}), encoding='utf-8')
            completed = subprocess.run([
                sys.executable, str(Path(__file__).with_name('analyze_actions_runs.py')),
                '--runs', str(runs), '--jobs', str(jobs),
            ], capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)['runs'][0]['elapsed_seconds'], 120)


if __name__ == '__main__':
    unittest.main()

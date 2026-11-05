"""Analyze exported GitHub Actions metadata without network access or dependencies."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics


HEAVY_CHECKS = {'Django Validation', 'Python dependency audit', 'Build, scan, and publish SBOM'}


def _timestamp(value):
    if not isinstance(value, str) or not value or value.startswith('0001-'):
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return timestamp.astimezone(timezone.utc) if timestamp.tzinfo else None
    except (ValueError, TypeError):
        return None


def _seconds(start, end):
    start, end = _timestamp(start), _timestamp(end)
    return (end - start).total_seconds() if start and end and end >= start else None


def summarize_runs(runs: list[dict], jobs_by_run: dict[str, list[dict]]) -> dict:
    records = []
    grouped = defaultdict(list)
    by_sha = defaultdict(list)
    for run in runs:
        run_id = run['id']
        jobs = jobs_by_run.get(str(run_id), [])
        durations = [{
            'name': job['name'], 'conclusion': job.get('conclusion'),
            'seconds': 0 if job.get('conclusion') == 'skipped' else _seconds(job.get('started_at'), job.get('completed_at')),
        } for job in jobs]
        heavy = [job for job in jobs if job['name'] in HEAVY_CHECKS]
        heavy_count = sum(job.get('conclusion') != 'skipped' and _timestamp(job.get('started_at')) is not None for job in heavy)
        skipped_names = {job['name'] for job in heavy if job.get('conclusion') == 'skipped'}
        if run.get('name') != 'Container Security':
            validation = 'not-applicable'
        elif skipped_names == HEAVY_CHECKS:
            validation = 'routing-only'
        elif heavy_count == 3:
            validation = 'full'
        else:
            validation = 'partial' if heavy else 'unknown'
        names = {job['name'] for job in jobs}
        expected = HEAVY_CHECKS if run.get('name') == 'Container Security' else set()
        release_stage = 'not-applicable'
        if run.get('name') == 'Production Release':
            publish_name = 'Build and publish numbered release'
            deploy_name = 'Migrate and update production image'
            combined = 'Check release eligibility and configuration'
            expected = {publish_name, deploy_name, combined} if combined in names else {
                publish_name, deploy_name, 'Check release eligibility', 'Check production configuration',
            }
            started_names = {job['name'] for job in jobs if job.get('conclusion') != 'skipped'
                             and _timestamp(job.get('started_at')) is not None}
            deployed = any(job['name'] == deploy_name and job.get('conclusion') == 'success'
                           for job in jobs if job['name'] in started_names)
            if deployed:
                release_stage = 'deployed'
            elif deploy_name in started_names:
                release_stage = 'deployment-attempt'
            elif publish_name in started_names:
                release_stage = 'build-only'
            elif {publish_name, deploy_name} <= {job['name'] for job in jobs if job.get('conclusion') == 'skipped'}:
                release_stage = 'preflight-only'
            else:
                release_stage = 'unknown'
        starts = [_timestamp(job.get('started_at')) for job in jobs]
        starts = [start for start in starts if start is not None]
        elapsed = (_seconds(run.get('created_at'), run.get('updated_at'))
                   if run.get('status') == 'completed' else None)
        total = (sum(job['seconds'] for job in durations)
                 if durations and expected <= names and all(job['seconds'] is not None for job in durations) else None)
        evidence = 'complete' if total is not None else 'incomplete-jobs' if jobs else 'missing-jobs'
        record = {
            'id': run_id, 'workflow': run.get('name', 'unknown'),
            'sha': run.get('head_sha'), 'branch': run.get('head_branch'),
            'event': run.get('event'), 'conclusion': run.get('conclusion'),
            'migration_window': run.get('migration_window', 'unknown'),
            'validation': validation, 'heavy_job_count': heavy_count, 'release_stage': release_stage,
            'elapsed_seconds': elapsed,
            'time_to_first_job_seconds': _seconds(run.get('created_at'), min(starts).isoformat()) if starts else None,
            'job_execution_seconds': total, 'jobs': durations, 'evidence': evidence,
        }
        records.append(record)
        grouped[(record['workflow'], record['branch'], record['event'], record['migration_window'], validation, release_stage)].append(record)
        by_sha[(record['workflow'], record['sha'])].append(record)
    groups = []
    for (workflow, branch, event, migration, validation, release_stage), members in grouped.items():
        samples = sorted(member['elapsed_seconds'] for member in members
                         if member['conclusion'] == 'success' and member['elapsed_seconds'] is not None
                         and member['evidence'] == 'complete')
        groups.append({
            'workflow': workflow, 'branch': branch, 'event': event, 'migration_window': migration,
            'validation': validation, 'release_stage': release_stage,
            'count': len(members), 'conclusions': dict(Counter(member['conclusion'] for member in members)),
            'success_samples': len(samples),
            'median_seconds': statistics.median(samples) if samples else None,
            'p95_seconds': samples[math.ceil(len(samples) * .95) - 1] if len(samples) >= 20 else None,
        })
    duplicates = []
    for (_, sha), members in by_sha.items():
        if sha and {'push', 'pull_request'} <= {member['event'] for member in members}:
            duplicates.append({'sha': sha, 'run_ids': [member['id'] for member in members]})
    return {'runs': records, 'groups': groups, 'duplicate_push_pr': duplicates}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', type=Path, required=True, help='JSON array from the runs REST API')
    parser.add_argument('--jobs', type=Path, required=True, help='JSON object mapping run IDs to job arrays')
    args = parser.parse_args()
    try:
        runs = json.loads(args.runs.read_text(encoding='utf-8-sig'))
        jobs = json.loads(args.jobs.read_text(encoding='utf-8-sig'))
        if not isinstance(runs, list) or not isinstance(jobs, dict):
            raise ValueError('Expected a runs array and a jobs object')
        result = summarize_runs(runs, jobs)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f'Metadata analysis failed: {error}\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

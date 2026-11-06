# Application Insights Instrumentation Deployment Plan

Status: Local validation complete; staging validation and deployment pending

## Goal and scope

Prepare opt-in request and database timing for the existing Django container,
without changing production until a separate deployment approval. The existing
staging-only page timing remains unchanged.

## Azure target

- Subscription: Microsoft Azure Sponsorship (esgrequesthub).
- Resource group: ESGRequestHub; region: Southeast Asia.
- Use the existing Application Insights component and Log Analytics workspace.
- No new resources, capacity upgrades, schema changes or data migrations.

## Approach

Use explicit OpenTelemetry server spans and database timing instead of default
automatic instrumentation. The full Microsoft distribution enables multiple
instrumentations, including HTTP and AI clients; a restricted pipeline avoids
implicitly collecting their URLs, prompts or other data. Use the Azure Monitor
trace exporter with the OpenTelemetry SDK and its Application Insights sampler.
The exporter currently has a beta version; verify dependency compatibility with
the production Python 3.12 image and Django before selecting the exact pins.

The initial dependency coverage is database query timing only, not outbound HTTP,
email or AI calls. A later change can add sanitized timing for those dependencies
after a separate privacy review. Record view names rather than raw paths, request
status and duration, and database query durations without SQL or parameters.
Exclude health and readiness probes. Do not export request bodies, query strings,
headers, user identifiers, SQL, query parameters, exception text or application
logs. Disable exporter offline storage unless its privacy implications are reviewed.

Initialize the tracing provider once per Gunicorn worker, never in the master
before fork. Instrumentation is disabled by default; enabling it without a valid
connection string must produce a clear configuration error. Preserve request
behavior and original exceptions.

Use managed identity for exporter authentication. The App Service currently has
no identity configured, so enabling production telemetry also requires a
separately approved identity and least-privilege monitoring role assignment.

## Implementation and verification

1. Establish test coverage for disabled mode, enabled configuration, health probe
   exclusion, successful and failing requests, database queries, and worker
   initialization. Inspect actual exported span payloads with sentinel secrets in
   paths, query strings, headers, bodies, SQL, parameters and exceptions.
2. Add the smallest necessary implementation and compatible direct dependency
   pins to `requirements.txt` and `requirements.lock`, preserving existing pins.
   Document configuration, data collected, sampling, cost and rollback.
3. Run focused tests and dependency validation using an isolated test database;
   verify Python 3.12 container compatibility and exporter behavior. Confirm that
   no telemetry is emitted when disabled and that enabled spans contain no
   sentinel values.
4. Validate in a nonproduction environment with the chosen sampling setting and
   verify request and dependency records in the intended workspace. Measure
   latency overhead and estimated ingestion volume.
5. Request separate deployment approval and a rollback window. After approval,
   enable telemetry, verify health and request records, and monitor ingestion
   and costs. Roll back by disabling the telemetry flag and, if necessary,
   restoring the previous image.

## Proposed staging environment

- Use a separate resource group and App Service plan in Southeast Asia, rather
  than sharing the production B2 instance. Evaluate Linux Basic B1 for the
  staging plan, subject to availability and a separate cost approval.
- Use a separate PostgreSQL Flexible Server with synthetic data. Evaluate a
  burstable B1ms SKU and the production PostgreSQL major version after
  confirming regional and subscription availability.
- Use a staging Application Insights component and Log Analytics workspace,
  with a staging managed identity and narrowly scoped telemetry-ingestion role.
  Keep staging and production credentials and data separate.
- Restrict staging access, deploy the candidate image using the controlled
  migration process, then verify health, requests, database dependency timing,
  and that synthetic secrets do not appear in exported telemetry.
- Resource provisioning, any production deployment, and their costs require
  separate approval. This proposal has not been provisioned.

## Current evidence and limitations

- Application Insights is now linked to the existing workspace. The workspace
  has 30-day retention; the current request query returned zero records.
- Application Insights CLI queries reported an authentication token error, so
  workspace queries are currently the usable verification path.
- App Service Always On and the `/readyz/` health-check path are enabled.
- The production image revision differs from the current workspace revision.
- Startup delay has not been attributed to an individual startup step; do not
  disable changelog synchronization without a separately verified replacement.
- Twenty-three targeted tests passed against disposable PostgreSQL, including
  Azure exporter conversion and sentinel-secret checks. A Linux image build,
  an enabled-telemetry startup with a fake exporter, and a containerized
  privacy test passed. The dependency audit found no known vulnerabilities.
- No complete application test suite or staging telemetry-ingestion test has
  been run. Production request ingestion is not yet verified.

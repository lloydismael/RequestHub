# Plan: Multiple SQRs per RQ ID

## Confirmed requirements

- Engineers create SQRs separately and may reuse the same RQ ID without a fixed limit.
- Only the engineer currently assigned to the request may create additional SQRs; backup-engineer access is not expanded.
- Each submission has its own SQR ID, approver, status, pricing, history, delivery, and billing lifecycle.
- No bulk-entry workflow, automatic copying, or changes to other account types' creation permissions.

## Current blockers verified in source

- `hub/models.py`: `SqrSubmission.linked_request` is already a nullable foreign key, but `unique_sqrsubmission_linked_request` restricts non-null links to one SQR per request.
- `hub/views.py`: the creation dropdown and edit-modal helper exclude requests already used by an SQR.
- `SqrListView.post()` rejects an existing link before and inside its transaction, and labels any `IntegrityError` as an existing-SQR error.
- `SqrEngineerUpdateView.get_form()` excludes requests linked to other submissions, which would prevent editing siblings after this change.
- Request management uses `.first()` and displays only one linked SQR.
- SQR reference generation uses the highest yearly sequence plus one; concurrent creation requires specific verification to avoid duplicate reference codes.

## Implementation steps

### 1. Remove the database restriction

- Remove only `unique_sqrsubmission_linked_request` from the model constraints.
- Add a new forward migration using `RemoveConstraint`; do not modify historical migrations.
- Keep the foreign key, reverse relation `sqr_links`, unique SQR reference codes, existing records, and deletion behavior unchanged.
- No data migration is required.

### 2. Allow repeated creation securely

- Keep every request assigned to the logged-in engineer available in the RQ ID dropdown, including requests with existing SQRs.
- Retain the server-side assigned-engineer queryset for POST validation, including rejection of tampered request IDs.
- Remove both request-level duplicate checks and their obsolete validation message.
- Retain transactional saving, configured/custom-field persistence, calculations, and normal notification behavior.
- Do not treat unrelated integrity failures as duplicate RQ links. Handle actual save failures accurately.
- Keep applicable locking until reference-code allocation is proven safe; do not simply discard the transaction/lock because duplicate-link checks are removed.

### 3. Preserve editing behavior

- Remove used-request exclusions from the edit-modal helper and update form.
- Align available choices between modal and server-side form where appropriate.
- Preserve existing owner/admin editing permissions and historical-link preservation for reassigned requests; this does not grant backup engineers creation access.
- Refresh the request/account mapping from the final queryset so autofill works for reused RQ IDs.

### 4. Display all linked SQRs

- Replace the request-management single-SQR lookup with an explicitly ordered collection.
- Update `templates/hub/request_manager_form.html` to display every linked SQR, with a count if useful, rather than silently selecting one.
- Apply the existing user's visibility rules to the collection; do not expose siblings outside their permitted scope.
- Keep one tracker row per SQR, retaining the repeated RQ ID and unique SQR ID.
- Verify reports, imports/exports, approval communications, and project-performance records continue to operate per submission, not per request.

### 5. Add focused regression coverage

- Create two or more SQRs for one assigned RQ ID; verify separate records and unique references.
- Verify the reused RQ ID remains available after the first submission and after a validation error.
- Reject creation against an unassigned request, including backup-only assignment and forged POST data.
- Preserve non-engineer creation restrictions.
- Edit one sibling without changing another; preserve admin and historical-link editing behavior.
- Display all authorized siblings on request management; preserve zero-link and single-link rendering.
- Verify independent approval/status/history/performance and export rows for siblings.
- Verify migration preserves existing data and permits multiple non-null links afterward.
- Exercise simultaneous submissions on the production database backend. Check both same-RQ and different-RQ reference allocation; if collisions occur, add narrowly scoped, database-appropriate serialization for yearly SQR numbering.

### 6. Validate and release

- Run focused SQR model/view/form, linked-request access, migration, export, notification, and performance tests, expanding only when failures require it.
- Check migration drift and Django system checks.
- Smoke-test as an assigned engineer: create SQR A, reuse its RQ ID for SQR B, edit both, and verify separate tracker rows and links.
- Update the SQR usage documentation to explain reusable RQ IDs and independent lifecycles.
- Apply the migration before accepting repeated-link submissions; coordinate application/schema rollout.
- Once duplicate links exist, rollback cannot reapply the old constraint without first resolving those duplicates. Prefer a forward fix; never delete or merge submissions automatically.

## Acceptance criteria

An assigned engineer can create any number of separate SQRs under one RQ ID. Each receives a unique SQR ID and remains independently editable and tracked. Unauthorized creation is rejected, existing submissions remain intact, and authorized users can see every linked SQR rather than only the first.

## Status

Implemented with migration `0052_allow_multiple_sqrs_per_request`, reusable create/edit request choices, assigned-engineer POST validation, authorized linked-SQR collections, independent lifecycle coverage, and transaction-scoped PostgreSQL advisory locking for yearly SQR numbering. Usage and rollback guidance are documented in README.md.

Validated against a disposable local PostgreSQL database, including repeated submissions, permissions, independent editing/history/performance, linked-SQR display, import/export, migration data preservation, and same-/different-request concurrency. Django system checks and migration-drift checks pass. The configured live database has not been migrated or changed; apply the migration during the coordinated rollout. No live-browser submissions were made.

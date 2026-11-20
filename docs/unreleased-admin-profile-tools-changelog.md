# Unreleased: Admin profile tools

**Before:** Admin Personalization showed the Classic SQR explanation, backup forms, and recovery tables in one long section. Recovery action results appeared above the profile while the redirect scrolled to Recovery Center. A deleted-request row could prevent the profile from rendering because it referenced an unavailable deleter field.

**After:** A compact Classic SQR switch shows the last saved visibility for all users, with an inline retry message if saving fails. Recovery Center shows the latest backup and size, a direct **Download latest** link, and **Create backup**. The Hub-only scope and off-host warning stay visible. Backup history, restore preview, deleted requests, activity, and coverage details expand on demand. Search and pagination reopen deleted requests, including an empty search. Backup and recovery feedback appears in the panel linked by the redirect. Deleted-request rows show the deletion time without the unsupported deleter field.

The create dialog supports keyboard focus and has an inline form fallback. Request deletion feedback now points admins to **Profile → Personalization → Deleted requests**. Upload validation, authorization, required confirmations, scoped backup coverage, and the safety snapshot before replacement are unchanged. The other profile tabs and non-admin settings are unchanged. No schema migration or deployment configuration is required.

## Evidence and verification

| Viewport | Before | After |
| --- | --- | --- |
| Desktop | [Before](../hub/changelog_assets/pending-admin-profile-tools/desktop-before.png) | [After](../hub/changelog_assets/pending-admin-profile-tools/desktop-after.png) |
| Phone | [Before](../hub/changelog_assets/pending-admin-profile-tools/mobile-before.png) | [After](../hub/changelog_assets/pending-admin-profile-tools/mobile-after.png) |

The `admin-profile-tools` recipe renders the actual Admin Personalization templates with the same synthetic Admin and backup on both sides. The [capture metadata](../hub/changelog_assets/pending-admin-profile-tools/capture.json) records the baseline revision, working-tree source fingerprints, viewport sizes, and image checksums. Page scripts are disabled, so the Create backup control shows the inline disclosure. The crop excludes the sticky navbar on both sides. The after images are from the working tree; the [release workflow](cd-workflow-testing.md#preparing-screenshot-evidence) captures the exact release revisions before publication.

Verification: 52 focused Django and Chromium tests, 3 SQR JavaScript tests, and 34 capture tests passed. Checks cover recovery navigation, backup feedback and download, keyboard focus, the inline fallback, role restrictions, and confirmed SQR visibility after successful or failed saves.

In disposable PostgreSQL, **Notifications → Changelogs** hid the draft and denied all four PNGs to a signed-in reader. After test publication, the desktop pair appeared in the feed, the mobile pair loaded on demand, and all four images were served byte-for-byte with `no-store`. Nothing was deployed or published to a live environment.

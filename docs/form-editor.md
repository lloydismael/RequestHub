# Admin Form Editor

Open **Management → Forms** using an Admin account. Switching to **Users** or **Accounts** hides the Form Editor; only the selected management tab's content is shown. Direct editor URLs also enforce the Admin role. Authentication and credential behavior are not configurable.

## Managing form people

1. Open **Management → Forms**. The two cards at the top select **New Request** or **New SQR Submission**; each card summarizes what its dropdowns currently show.
2. For a person dropdown, choose **All eligible accounts** to include everyone who is eligible now and later, or **Only the people I choose**. The second option starts with everyone eligible: remove anyone you do not want to show, or use **Add person** to show someone who is missing. This only selects existing eligible accounts and never creates or changes a login.
3. For SQR **Account Managers**, use **Add an Account Manager** to enter a name and choose its department. **Edit** renames a name or moves it to another department, and **Remove** deletes it from the dropdown. Names need no login account or source-code change. The same name can appear in different departments, but only once per department.
4. Select **Save and apply** to publish immediately. Forms opened afterward use the new list; existing records keep their saved names. **Discard changes** restores the last saved list.

New Request derives its Account Manager from the person submitting the request; it has no separate Account Manager list. Manage its available engineers here instead. User-backed selectors only offer existing eligible accounts; create or update login accounts through User Management.

Unsaved changes require confirmation before switching forms or leaving. Concurrent changes require reloading rather than overwriting another administrator. If an advanced-editor draft exists, publish it or restore defaults in that editor before saving people changes. Saving people preserves other published form customizations.

## Advanced editing and publication

Expand **Advanced form customization** to access the full registered-form catalog.

1. Use the category filters (**Request**, **SQR**, **Management**, **Others**) or the search box in **Advanced form editors** to find a registered form, including New Request or New SQR Submission.
2. Search the field list and select one field to edit its label and help text. Open Advanced settings for placeholders, sections, widths, and conditions. Use Move up/down for keyboard-friendly ordering.
3. Add custom text, long-text, number, email, URL, date, checkbox, single-select, or multi-select fields. Edit dropdown options in separate value/label rows; values are stable storage keys.
4. For supported people dropdowns, leave All eligible selected or search and check the people to display. New Request supports preferred engineers; Request Admin also supports requestors and backup engineers. SQR Submission supports approvers and Account Manager names. Use the primary Forms workspace to add names and assign departments; the advanced editor can narrow its baseline member list. Delivery and Performance person fields support the same controls when those forms are used. These lists do not rename or modify user accounts.
5. Core field type, requiredness, visibility, and business validation remain locked. Other built-in dropdowns are protected.
6. Preview, then Save draft. Drafts do not affect users.
7. Publish the saved draft to apply it application-wide.

Allowed-person lists only narrow existing role and actor restrictions; they never grant access. Required selectors need at least one configured value. An empty optional selector offers no new selections. Existing records retain their current selection when still permitted by domain rules; saved custom dropdown values remain available as retired options. Published IDs that later become ineligible can be removed explicitly, but do not reappear for new records. Department-dependent SQR members and shared edit modals use the same filtered lists. If every configured person later becomes ineligible, update the list or restore All eligible.

Restore Defaults and history rollback create new published versions without deleting old versions or saved answers. Concurrent draft edits return a conflict rather than overwriting another administrator's changes. Published custom field types cannot be changed: archive the old field and add a new key instead.

Forms pin a signed schema version for 24 hours. Expired/invalid tokens require reloading. A form opened before publication can finish using its original version. New required custom fields do not retroactively block records that lack an answer. Hidden answers are preserved and forged hidden inputs are ignored.

## Current coverage and boundaries

The catalog includes Request/SQR creation, editing, stage forms, activity/status forms, account/user management, profile, filters, and import configuration. Existing permissions, dependent account/engineer selectors, deployment-calendar behavior, SQR uniqueness, calculations, and workflow rules remain authoritative.

Activity-log schema previews use an unsaved engineer with an empty Related Request list; live forms retain their assigned/backup-engineer request filtering.

Published presentation applies when a registered Django form is used. Legacy inline SQR tracker updates that bypass Django forms are not converted into configurable forms in this release. Catalog entries for unused legacy stage forms do not create new workflow screens. Core fields in specialized/table layouts retain layout constraints; half-width primarily applies to grouped custom fields. Role-specific schemas, uploads, advanced conditional formulas, custom-answer exports, and historical answer display/audit beyond retained versioned storage remain future integration work.

Custom answers are stored with the corresponding business record and published version, independently of typed model columns. Create/edit flows that use registered forms preserve answers when revisiting records; shared SQR edit modals load each record's custom inputs before opening.

## Deployment

Run the normal Django migration and static build before starting updated workers:

```powershell
py manage.py migrate
py manage.py collectstatic --noinput
```

Migration `0048_form_editor` adds editor definitions, immutable published versions, configuration audit, and custom-answer storage. No pre-existing business columns are rewritten.

## Validation

Run the focused editor tests with the project's Python interpreter:

```powershell
py manage.py test hub.test_form_editor_schema hub.test_form_editor_runtime hub.test_form_editor_views hub.test_form_people
```

The implementation also has regression coverage for Request, SQR performance, and Management. A local run found unrelated pending model/migration drift for SQR performance health choices; no unrelated migration was generated.

from datetime import date
from pathlib import Path

from django.db import migrations

ASSET_DIR = Path(__file__).resolve().parent.parent / "changelog_assets" / "v55.1"
PREVIOUS_VERSION = "v55.0"
NEW_VERSION = "v55.1"

# Listed newest last: the feed shows the highest primary key first for the same release date.
ENTRIES = [
    {
        "title": "Sign-in page shows the correct year",
        "summary": "The footer of the sign-in page now shows the current year.",
        "before_text": "The sign-in page footer read \u201cDeveloped by ESG 2027\u201d.",
        "after_text": "The sign-in page footer reads \u201cDeveloped by ESG 2026\u201d.",
        "before_image": "sign-in-footer-before.jpg",
        "after_image": "sign-in-footer-after.jpg",
    },
    {
        "title": "SQR approval emails open with the body already filled in",
        "summary": (
            "When a PM-ESG or Admin sets an SQR to Approved and chooses the ready draft option, "
            "the Outlook email now opens with the quotation text already in the body, so no paste step is needed. "
            "When the Microsoft 365 connection is configured, the complete formatted draft is created straight away."
        ),
        "before_text": (
            "Outlook opened with only the recipients and subject. The message body was empty, "
            "so the approver had to click inside it and press Ctrl+V to paste the copied quotation.\n"
            "If the browser blocked clipboard access, the preview page opened instead and the body had to be copied manually.\n"
            "With Microsoft 365 configured, the app first downloaded the preview and copied the email "
            "to the clipboard before creating the Outlook draft."
        ),
        "after_text": (
            "Outlook opens with the recipients, subject and the plain-text quotation body already filled in "
            "(project details, man-hours, totals, validity and terms). Press Ctrl+A then Ctrl+V in the body "
            "only if you want the formatted layout with the logo.\n"
            "If clipboard access is blocked, the body is still filled in. The preview page with manual copy and .eml "
            "download opens only when the email is too long for a mail link (over 8,000 characters) and the clipboard copy is also blocked.\n"
            "With Microsoft 365 configured, the draft is requested as soon as the approval is saved, without the preview "
            "download or clipboard copy, and opens in Outlook on the web."
        ),
        "before_image": "sqr-approval-email-before.jpg",
        "after_image": "sqr-approval-email-after.jpg",
    },
]


def stored_name(filename):
    return f"changelogs/{NEW_VERSION}-{filename}"


def add_release_entries(apps, schema_editor):
    Changelog = apps.get_model("hub", "Changelog")
    StoredFile = apps.get_model("accounts", "StoredFile")
    for entry in ENTRIES:
        if Changelog.objects.filter(
            previous_version=PREVIOUS_VERSION, new_version=NEW_VERSION, title=entry["title"],
        ).exists():
            continue
        images = {}
        for field in ("before_image", "after_image"):
            filename = entry[field]
            data = (ASSET_DIR / filename).read_bytes()
            StoredFile.objects.update_or_create(
                name=stored_name(filename),
                defaults={"data": data, "content_type": "image/jpeg", "original_name": filename, "size": len(data)},
            )
            images[field] = stored_name(filename)
        Changelog.objects.create(
            title=entry["title"],
            previous_version=PREVIOUS_VERSION,
            new_version=NEW_VERSION,
            release_date=date(2026, 11, 4),
            summary=entry["summary"],
            before_text=entry["before_text"],
            after_text=entry["after_text"],
            is_published=True,
            **images,
        )


def remove_release_entries(apps, schema_editor):
    Changelog = apps.get_model("hub", "Changelog")
    StoredFile = apps.get_model("accounts", "StoredFile")
    Changelog.objects.filter(
        previous_version=PREVIOUS_VERSION, new_version=NEW_VERSION, title__in=[entry["title"] for entry in ENTRIES],
    ).delete()
    StoredFile.objects.filter(
        name__in=[stored_name(entry[field]) for entry in ENTRIES for field in ("before_image", "after_image")],
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0009_database_profile_photos"),
        ("hub", "0054_changelog"),
    ]

    operations = [
        migrations.RunPython(add_release_entries, remove_release_entries),
    ]

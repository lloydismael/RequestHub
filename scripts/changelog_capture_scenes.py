"""Render only allowlisted synthetic scenes from a disposable Git snapshot."""

import argparse
import hashlib
import importlib.util
import json
import re
import socket
import sys
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path, PurePosixPath
from unittest.mock import patch

PRIVATE_PARTS = {".git", ".azure", ".ssh"}
PRIVATE_SUFFIXES = {".pem", ".key", ".pfx", ".p12"}
FINGERPRINT_LENGTH = 16


def _document(snapshot, scene):
    path = snapshot.joinpath(*scene["path"].split("/")).resolve()
    if not path.is_relative_to(snapshot):
        raise ValueError("Documentation path escaped the snapshot.")
    if not path.is_file():
        return "<h1>Documentation absent at this revision</h1>"
    lines = path.read_text(encoding="utf-8").splitlines()
    heading = scene["heading"]
    selected = []
    depth = None
    for line in lines:
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match:
            if depth is not None and len(match[1]) <= depth:
                break
            if depth is None and match[2] == heading:
                depth = len(match[1])
        if depth is not None:
            selected.append(line)
    if depth is None:
        return "<h1>Selected documentation section absent at this revision</h1>"
    if any(line.startswith(("```", "~~~", "    ", "\t")) for line in selected):
        raise ValueError("Code blocks cannot be published as automatic documentation evidence.")
    return "\n".join(
        f"<h2>{escape(line.lstrip('#').strip())}</h2>" if line.startswith("#")
        else f"<p>{escape(line)}</p>" for line in selected if line.strip()
    )


def _is_private(name):
    if not isinstance(name, str) or not name or "\\" in name:
        return True
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {".", ".."} or ":" in part for part in name.split("/")):
        return True
    if any(part.startswith(".env") or part in PRIVATE_PARTS for part in path.parts):
        return True
    return path.suffix.lower() in PRIVATE_SUFFIXES


def _repository_change(snapshot, scene):
    paths = scene.get("paths")
    if not isinstance(paths, list) or not paths:
        raise ValueError("A repository change report requires the changed repository paths.")
    for name in paths:
        if _is_private(name):
            raise ValueError(f"Private or unsafe path cannot be reported: {name}")
    rows = []
    omitted = scene.get("omitted_files")
    omitted = omitted if type(omitted) is int and omitted > 0 else 0
    for name in paths:
        target = snapshot.joinpath(*PurePosixPath(name).parts)
        if target.is_file():
            payload = target.read_bytes()
            status = "Present"
            detail = f"{len(payload)} bytes · sha256:{hashlib.sha256(payload).hexdigest()[:FINGERPRINT_LENGTH]}"
        else:
            status = "Absent"
            detail = "No file content at this revision"
        rows.append((name, status, detail))
    revision = scene.get("revision")
    revision = revision if isinstance(revision, str) and revision else "unknown"
    body = [
        "<h1>Repository change report</h1>",
        "<p>Verified file presence, sizes and content fingerprints from the exact Git snapshot. "
        "This is a repository change report; not a UI screenshot. Source code and private configuration are not displayed.</p>",
        f"<p>Revision: <code>{escape(revision)}</code></p>",
    ]
    if omitted:
        body.append(f"<p>Private or unsafe files excluded: {omitted}</p>")
    body.append(
        "<table><tr><th>Path</th><th>Status</th><th>Fingerprint</th></tr>"
        + "".join(
            f"<tr><td>{escape(name)}</td><td>{status}</td><td>{escape(detail)}</td></tr>"
            for name, status, detail in rows
        )
        + "</table>"
    )
    return "".join(body)


def _report(snapshot, scene):
    if scene.get("fixture") == "repository_change":
        return _repository_change(snapshot, scene)
    path = snapshot / "hub" / "release_changelog.py"
    if not path.is_file():
        return "<h1>Release changelog contract</h1><p>This feature is absent at this revision.</p>"
    spec = importlib.util.spec_from_file_location("historical_release_changelog", path)
    if spec is None or spec.loader is None:
        raise ValueError("Historical report module could not be loaded.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = module.validate_manifest({
        "version": "fixture", "revision": "3" * 40, "generated_at": "2026-11-06T00:00:00+00:00",
        "evidence_policy": "all_changes",
        "commits": [{"sha": "1" * 40, "parent_sha": "2" * 40, "type": "maintenance",
                     "title": "Synthetic documentation change", "requires_comparison": False}],
    })
    rows = [
        ("Feature subject", module.classify("feat: synthetic export")[0]),
        ("Maintenance subject", module.classify("docs: synthetic guide")[0]),
        ("Evidence policy", manifest.get("evidence_policy", "Legacy optional evidence")),
        ("Maintenance requires screenshots", str(manifest["commits"][0].get("requires_comparison", False))),
    ]
    return "<h1>Release changelog contract</h1><p>Deterministic synthetic behavior report; no live data.</p><table>" + "".join(
        f"<tr><th>{escape(label)}</th><td>{escape(value)}</td></tr>" for label, value in rows
    ) + "</table>"


def _seasonal_root(root):
    match = re.search(r' class="([^"]*)"', root)
    if not match:
        return root[:-1] + ' class="login-christmas">'
    if "login-christmas" not in match[1].split():
        return root[:match.end(1)] + " login-christmas" + root[match.end(1):]
    return root


def _ui(snapshot, fixture):
    sys.path.insert(0, str(snapshot))
    from django.conf import settings
    settings.configure(
        BASE_DIR=snapshot,
        SECRET_KEY="disposable-capture-fixture-not-for-deployment",
        DEBUG=False, ALLOWED_HOSTS=["changelog.capture.invalid"], ROOT_URLCONF="request_hub.urls",
        INSTALLED_APPS=["django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions",
                        "django.contrib.messages", "django.contrib.staticfiles", "accounts.apps.AccountsConfig", "hub"],
        AUTH_USER_MODEL="accounts.User", DATABASES={"default": {"ENGINE": "django.db.backends.dummy"}},
        TEMPLATES=[{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [str(snapshot / "templates")],
                    "APP_DIRS": True, "OPTIONS": {"context_processors": []}}],
        STATIC_URL="/static/", MEDIA_URL="/media/", USE_TZ=True, TIME_ZONE="Asia/Manila",
        CHRISTMAS_LOGIN_THEME_ENABLED=True,
        STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                  "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
    )
    import django
    django.setup()
    from django.contrib.auth.models import AnonymousUser
    from django.template import engines
    from django.template.loader import render_to_string
    from django.test import RequestFactory
    request = RequestFactory().get("/", HTTP_HOST="changelog.capture.invalid")
    request.user = AnonymousUser()
    request.META["CSRF_COOKIE"] = "0" * 32
    context = {"request": request, "assistant_widget": {"native_enabled": False}}
    if fixture in {"login", "seasonal_login"}:
        from accounts.views import RoleLoginView
        with patch("django.utils.timezone.now", return_value=datetime(2026, 12, 31, 4, tzinfo=timezone.utc)):
            response = RoleLoginView.as_view()(request)
            if fixture == "seasonal_login":
                response.context_data["christmas_login_default"] = True
            document = response.render().content.decode("utf-8")
        if fixture == "seasonal_login":
            # Older templates applied this same preference via localStorage and inline JS.
            document = re.sub(r"<html\b[^>]*>", lambda match: _seasonal_root(match[0]), document, count=1)
        return document
    if fixture == "profile":
        from accounts.forms import ProfileForm, ThemeForm
        from accounts.models import User

        user = User(username="demo-viewer", first_name="Demo", last_name="Viewer", role="engineer")
        request.user = user
        with patch("hub.form_editor.get_definition", return_value=None):
            document = render_to_string("accounts/profile_form.html", {
                **context, "user": user, "form": ProfileForm(instance=user), "theme_form": ThemeForm(instance=user),
            }, request=request)
        return document.replace('class="profile-panel" id="profile-panel-avatar"',
                                'class="profile-panel profile-panel--active" id="profile-panel-avatar"', 1)
    if fixture == "sqr_tracker":
        context["capture_component"] = render_to_string("hub/partials/sqr_prototype.html", {"sqr_active_view": "prototype"})
        template = engines["django"].from_string(
            "{% extends 'base.html' %}{% load static %}"
            "{% block extra_head %}<link rel='stylesheet' href='{% static \"css/sqr-tracker-prototype.css\" %}'>{% endblock %}"
            "{% block content %}{{ capture_component|safe }}{% endblock %}"
        )
        return re.sub(r'(<section id="sqr-proto"[^>]*?) hidden(?=\s|>)', r"\1", template.render(context, request=request), count=1)
    if fixture == "sqr_performance":
        # The dashboard is a lazy shell: its filters and headings are server-rendered, while the data
        # tables are filled by JavaScript, which capture disables. Render the real partial and reveal
        # both hidden containers so the before/after diff shows the server-rendered controls.
        context["capture_component"] = render_to_string(
            "hub/partials/sqr_performance.html",
            {"default_my_assigned_filter": False, "sqr_current_user_id": ""},
        )
        template = engines["django"].from_string(
            "{% extends 'base.html' %}{% load static %}"
            "{% block extra_head %}<link rel='stylesheet' href='{% static \"css/liquid-glass-sqr.css\" %}'>{% endblock %}"
            "{% block content %}{{ capture_component|safe }}{% endblock %}"
        )
        document = template.render(context, request=request)
        return document.replace(
            'class="sqr-pm sqr-view-panel" data-sqr-view-panel="performance" hidden',
            'class="sqr-pm sqr-view-panel" data-sqr-view-panel="performance"',
            1,
        ).replace('id="sqr-pm-content" hidden', 'id="sqr-pm-content"', 1)
    from hub.models import Changelog
    entry = Changelog(
        pk=1, title="Synthetic release fixture", previous_version="v0", new_version="v1",
        release_date=date(2026, 11, 6), updated_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
        summary="The same unsaved release fixture is rendered at both source revisions.",
        release_sha="1" * 40,
        changes={"commits": [
            {"sha": "2" * 40, "type": "improved", "title": "Screen layout", "comparisons": [{
                "title": "Screen layout", "before_text": "Previous synthetic layout.", "after_text": "Updated synthetic layout.",
            }]},
            {"sha": "3" * 40, "type": "maintenance", "title": "Release test coverage", "comparisons": [{
                "title": "Repository change report", "before_text": "Previous test coverage.", "after_text": "Updated test coverage.",
                "capture_note": "Automatic report capture, recipe repository-change. Exact Git snapshots.",
            }]},
        ]},
    )
    if fixture == "management_monitoring":
        from django.core.paginator import Paginator
        from accounts.models import User
        from hub.changelog_forms import ChangelogForm

        request.user = User(pk=1, username="demo-admin", first_name="Demo", last_name="Admin", role=User.Roles.ADMIN)
        monitoring = (snapshot / "templates" / "hub" / "partials" / "monitoring.html").is_file()
        context.update(active_tab="monitoring" if monitoring else "changelogs", total_users=5, total_accounts=3)
        if not monitoring:
            context.update(changelog_page=Paginator([entry], 10).page(1), changelog_form=ChangelogForm())
        return render_to_string("hub/management.html", context, request=request)
    if fixture == "changelog_feed":
        return render_to_string("hub/notifications.html", {
            **context, "active_tab": "changelogs", "changelog_count": 1, "unread_count": 0,
            "changelogs": [entry],
        }, request=request)
    if fixture in {"changelog_editor", "changelog_history"}:
        from django.core.paginator import Paginator
        from hub.changelog_forms import ChangelogForm

        if fixture == "changelog_editor":
            entry.changes["commits"].append({"sha": "4" * 40, "type": "fixed", "title": "Synthetic form update"})
            for change in entry.changes["commits"]:
                change["requires_comparison"] = True
            history = {"changelog_entry": entry, "changelog_form": ChangelogForm(instance=entry)}
        else:
            history = {"changelog_page": Paginator([entry], 10).page(1), "changelog_form": ChangelogForm()}
        context["capture_component"] = render_to_string("hub/partials/changelog_management.html", history, request=request)
    else:
        context["capture_component"] = render_to_string("hub/partials/changelog_entry.html", {"entry": entry})
    template = engines["django"].from_string(
        "{% extends 'base.html' %}{% load static %}"
        "{% block extra_head %}<link rel='stylesheet' href='{% static \"hub/changelogs.css\" %}'>{% endblock %}"
        "{% block content %}{{ capture_component|safe }}{% endblock %}"
    )
    return template.render(context, request=request)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True)
    args = parser.parse_args()
    snapshot = Path(args.snapshot).resolve()
    scene = json.load(sys.stdin)

    def no_network(*args, **kwargs):
        raise PermissionError("Network access is forbidden in capture fixtures.")

    socket.socket.connect = no_network
    socket.create_connection = no_network
    if scene["kind"] == "ui":
        document = _ui(snapshot, scene["fixture"])
    else:
        content = _document(snapshot, scene) if scene["kind"] == "document" else _report(snapshot, scene)
        document = (
            "<!doctype html><html lang='en'><meta charset='utf-8'><title>Synthetic change evidence</title>"
            "<style>body{font:18px/1.6 system-ui,sans-serif;color:#17233b;background:#f5f7fb;padding:40px;}"
            "h1,h2{line-height:1.25}table{border-collapse:collapse;width:100%}th,td{padding:16px;text-align:left;"
            "border-bottom:1px solid #d4dbea}p{max-width:70ch}</style><body>" + content + "</body></html>"
        )
    sys.stdout.write(document)


if __name__ == "__main__":
    main()

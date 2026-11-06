"""Render only allowlisted synthetic scenes from a disposable Git snapshot."""

import argparse
import importlib.util
import json
import re
import socket
import sys
from html import escape
from pathlib import Path


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


def _report(snapshot):
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


def _ui(snapshot, fixture):
    sys.path.insert(0, str(snapshot))
    from django.conf import settings
    settings.configure(
        BASE_DIR=snapshot,
        SECRET_KEY="disposable-capture-fixture-not-for-deployment",
        DEBUG=False, ALLOWED_HOSTS=["changelog.capture.invalid"], ROOT_URLCONF="request_hub.urls",
        INSTALLED_APPS=["django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions",
                        "django.contrib.messages", "django.contrib.staticfiles", "accounts", "hub"],
        AUTH_USER_MODEL="accounts.User", DATABASES={"default": {"ENGINE": "django.db.backends.dummy"}},
        TEMPLATES=[{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [str(snapshot / "templates")],
                    "APP_DIRS": True, "OPTIONS": {"context_processors": []}}],
        STATIC_URL="/static/", MEDIA_URL="/media/", USE_TZ=True, TIME_ZONE="Asia/Manila",
        STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                  "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
    )
    import django
    django.setup()
    from django.contrib.auth.models import AnonymousUser
    from django.template import engines
    from django.template.loader import render_to_string
    from django.test import RequestFactory
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    request.META["CSRF_COOKIE"] = "0" * 32
    context = {"request": request, "assistant_widget": {"native_enabled": False}}
    if fixture == "login":
        from accounts.forms import RoleAuthenticationForm
        context.update(form=RoleAuthenticationForm(request), christmas_login_enabled=True)
        return render_to_string("accounts/login.html", context, request=request)
    from hub.models import Changelog
    entry = Changelog(
        title="Synthetic release fixture", previous_version="v0", new_version="v1",
        summary="The same unsaved release fixture is rendered at both source revisions.",
        release_sha="1" * 40,
        changes={"commits": [{"sha": "2" * 40, "type": "improved", "title": "Synthetic comparison",
                             "before_text": "Previous synthetic state.", "after_text": "Updated synthetic state."}]},
    )
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
        content = _document(snapshot, scene) if scene["kind"] == "document" else _report(snapshot)
        document = (
            "<!doctype html><html lang='en'><meta charset='utf-8'><title>Synthetic change evidence</title>"
            "<style>body{font:18px/1.6 system-ui,sans-serif;color:#17233b;background:#f5f7fb;padding:40px;}"
            "h1,h2{line-height:1.25}table{border-collapse:collapse;width:100%}th,td{padding:16px;text-align:left;"
            "border-bottom:1px solid #d4dbea}p{max-width:70ch}</style><body>" + content + "</body></html>"
        )
    sys.stdout.write(document)


if __name__ == "__main__":
    main()

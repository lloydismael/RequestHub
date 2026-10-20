import os
from unittest.mock import patch

from django.db import DatabaseError
from django.test import SimpleTestCase, override_settings


@override_settings(SECURE_SSL_REDIRECT=False)
class ReleaseHealthTests(SimpleTestCase):
    def test_release_reports_revision_without_caching(self):
        with patch.dict(os.environ, APP_REVISION="a" * 40):
            response = self.client.get("/release/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["revision"], "a" * 40)
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_readiness_checks_database(self):
        with patch("request_hub.urls.connection") as database:
            response = self.client.get("/readyz/")
        database.cursor.return_value.__enter__.return_value.execute.assert_called_once_with("SELECT 1")
        self.assertEqual(response.json(), {"ready": True})

    def test_readiness_failure_is_generic(self):
        with patch("request_hub.urls.connection") as database:
            database.cursor.side_effect = DatabaseError("private connection details")
            response = self.client.get("/readyz/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"ready": False})
        self.assertNotContains(response, "private connection details", status_code=503)

    def test_endpoints_reject_post(self):
        for endpoint in ("/healthz/", "/readyz/", "/release/"):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.post(endpoint).status_code, 405)
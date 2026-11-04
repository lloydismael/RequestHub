import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings


class LiveRefreshScriptTests(SimpleTestCase):
    def test_live_refresh_regressions(self):
        import shutil
        import subprocess
        from django.conf import settings
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js is required for browser-script regression tests')
        result = subprocess.run([node, '--test',
            str(settings.BASE_DIR / 'scripts_check' / 'test_dashboard_refresh.js'),
            str(settings.BASE_DIR / 'scripts_check' / 'test_sqr_refresh.js')], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class PageAssetTests(TestCase):
    def test_shared_theme_assets_remain_on_login(self):
        response = self.client.get('/accounts/login/')
        self.assertContains(response, 'css/account-themes')
        self.assertContains(response, 'css/liquid-glass')
        self.assertNotContains(response, 'Server-Timing')

    def test_build_collection_produces_immutable_compressed_assets(self):
        with TemporaryDirectory() as directory:
            with override_settings(STATIC_ROOT=directory, STORAGES={
                'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
            }):
                call_command('collectstatic', interactive=False, verbosity=0)
            manifest = json.loads((Path(directory) / 'staticfiles.json').read_text())
            hashed = manifest['paths']['css/app.css']
            self.assertNotEqual(hashed, 'css/app.css')
            self.assertTrue((Path(directory) / hashed).exists())
            self.assertTrue((Path(directory) / (hashed + '.gz')).exists())

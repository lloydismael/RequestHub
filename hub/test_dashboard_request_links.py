from html.parser import HTMLParser
from types import SimpleNamespace

from django.template.loader import render_to_string
from django.test import SimpleTestCase


class RequestIdLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.first_cell = False
        self.cell_seen = False
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'td' and not self.cell_seen:
            self.first_cell = True
            self.cell_seen = True
        elif tag == 'a' and self.first_cell:
            self.links.append(dict(attrs))

    def handle_endtag(self, tag):
        if tag == 'td':
            self.first_cell = False


class DashboardRequestLinksTests(SimpleTestCase):
    def test_requestor_reference_links_to_manage_for_initial_and_live_rows(self):
        item = SimpleNamespace(
            pk=1023, reference_code='REQ-01023', description='test deployment',
            account=SimpleNamespace(name='Test Account'), status='ongoing',
        )
        for is_pm_ess in (False, True):
            for template in ('hub/dashboard.html', 'hub/partials/dashboard_admin_rows.html'):
                with self.subTest(is_pm_ess=is_pm_ess, template=template):
                    html = render_to_string(template, {
                        'requests': [item], 'role': 'requestor',
                        'is_requestor_ui': True, 'is_pm_ess': is_pm_ess,
                        'metrics': {'ongoing': 1, 'completed': 0},
                    })
                    parser = RequestIdLinks()
                    parser.feed(html)
                    self.assertEqual(len(parser.links), 1)
                    self.assertEqual(parser.links[0]['href'], '/requests/1023/manage/collab/')
                    self.assertIn('>REQ-01023</a>', html)

from html.parser import HTMLParser

from django.contrib.auth.models import AnonymousUser
from django.core.paginator import Paginator
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase


class PaginationElements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attributes):
        self.tags.append((tag, dict(attributes)))


class PaginationLayoutTests(SimpleTestCase):
    def context(self, number=1):
        request = RequestFactory().get('/dashboard/')
        request.user = AnonymousUser()
        return {
            'request': request, 'user': request.user, 'page_obj': Paginator(range(60), 50).page(number),
            'page_size': 50, 'requests': [], 'role': 'admin', 'active_sqr_tab': 'proposal',
            'pagination_previous': '?page=1&page_size=50&q=needle' if number == 2 else '',
            'pagination_next': '?page=2&page_size=50&q=needle' if number == 1 else '',
            'pagination_id': 'test-pagination',
        }

    def test_dashboard_pagination_follows_requests_table(self):
        html = render_to_string('hub/dashboard.html', self.context())
        table_end = html.index('</table>', html.index('id="dashboard-live-tbody"'))
        self.assertGreater(html.index('id="dashboard-live-pagination"'), table_end)

    def test_sqr_pagination_follows_tracker_table(self):
        html = render_to_string('hub/sqr.html', self.context())
        table_end = html.index('</table>', html.index('class="sqr-tracker table-hover"'))
        self.assertGreater(html.index('id="sqr-pagination"'), table_end)

    def test_compact_controls_preserve_links_and_disabled_boundaries(self):
        for number, label in ((1, 'Next page'), (2, 'Previous page')):
            html = render_to_string('hub/partials/pagination.html', self.context(number))
            parser = PaginationElements()
            parser.feed(html)
            self.assertFalse(any(tag in ('form', 'select') for tag, attrs in parser.tags))
            links = [attrs for tag, attrs in parser.tags if tag == 'a']
            self.assertEqual(len(links), 1)
            self.assertEqual(links[0]['aria-label'], label)
            self.assertIn('q=needle', links[0]['href'])
            self.assertEqual(len([attrs for tag, attrs in parser.tags if tag == 'button' and 'disabled' in attrs]), 1)

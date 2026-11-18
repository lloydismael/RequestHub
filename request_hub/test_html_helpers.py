"""Script extraction and omission for trusted local HTML test fixtures, not sanitization."""

from html.parser import HTMLParser


class _ScriptParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.html = html
        self.scripts = []
        self.spans = []
        self._current_script = None
        self._line_starts = [0]
        for line in html.split("\n")[:-1]:
            self._line_starts.append(self._line_starts[-1] + len(line) + 1)
        self.feed(html)
        self.close()

    def _offset(self):
        line, column = self.getpos()
        return self._line_starts[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            start = self._offset()
            self._current_script = (start, start + len(self.get_starttag_text()), dict(attrs))

    def handle_endtag(self, tag):
        if tag == "script" and self._current_script is not None:
            start, body_start, attrs = self._current_script
            body_end = self._offset()
            self.scripts.append((attrs, self.html[body_start:body_end]))
            self.spans.append((start, self.html.index(">", body_end) + 1, attrs))
            self._current_script = None


def extract_scripts(html):
    """Return each script's attributes and source in document order."""
    return _ScriptParser(html).scripts


def omit_script_elements(html, *, keep_json_data=False):
    """Omit fixture scripts except explicitly requested inert JSON data elements."""
    fragments = []
    previous_end = 0
    for start, end, attrs in _ScriptParser(html).spans:
        if keep_json_data and (attrs.get("type") or "").strip().lower() == "application/json" and "src" not in attrs:
            continue
        fragments.append(html[previous_end:start])
        previous_end = end
    fragments.append(html[previous_end:])
    return "".join(fragments)

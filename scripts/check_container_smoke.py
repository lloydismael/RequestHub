"""Probe a disposable local container, including image-packaged static assets."""

import argparse
from html.parser import HTMLParser
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen


class StaticAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        value = values.get("href" if tag == "link" else "src" if tag == "script" else "")
        if value and value.startswith("/static/") and not urlsplit(value).netloc:
            self.paths.append(value)


def check(base, revision):
    origin = urlsplit(base)
    if origin.scheme != "http" or origin.hostname not in ("127.0.0.1", "localhost") or origin.path:
        raise ValueError("Smoke checks require a disposable local HTTP origin")

    def fetch(path):
        request = Request(urljoin(base, path), headers={"X-Forwarded-Proto": "https"})
        try:
            with urlopen(request, timeout=5) as response:
                if response.status != 200:
                    raise ValueError("Smoke endpoint did not return 200")
                return response.read()
        except (HTTPError, URLError, OSError) as error:
            if isinstance(error, HTTPError):
                error.close()
            raise ValueError(f"Smoke endpoint unavailable: {path}") from error

    if fetch("/healthz/") != b"ok":
        raise ValueError("Container liveness failed")
    if json.loads(fetch("/release/"))["revision"] != revision:
        raise ValueError("Container revision does not match the scanned image")
    if json.loads(fetch("/readyz/")).get("ready") is not True:
        raise ValueError("Disposable database is not ready")
    assets = StaticAssets()
    assets.feed(fetch("/accounts/login/").decode("utf-8"))
    if not assets.paths:
        raise ValueError("Login page has no image-packaged static assets")
    for path in dict.fromkeys(assets.paths):
        if not fetch(path):
            raise ValueError("Image-packaged static asset is empty")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)
    deadline = time.monotonic() + args.timeout
    while True:
        try:
            check(args.url, args.revision)
            print("PASS: container revision, liveness, database readiness, login, and packaged static assets")
            return 0
        except (ValueError, KeyError) as error:
            if time.monotonic() >= deadline:
                print(str(error))
                return 1
            time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())

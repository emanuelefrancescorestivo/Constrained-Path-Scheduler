"""
What a first visit to Today downloads, before and after compression (DECISIONS.md D106).

The demo's @alex (cps.showcase, made through `service`) opens Today in the app itself:
the page, then every file it names (stylesheets, scripts, the two fonts it preloads),
the scripts each script imports, and the fonts the stylesheet names, followed until
there are none left. Each file is weighed as served, and compressed with gzip at
level 6, which stands in for what Caddy's `encode zstd gzip` sends (zstd usually
does a little better; Python 3.13's standard library has no zstd). Then the next
page, Progress, with every file already kept: how many of its files the browser
must ask about again, which is the round trips D106's versioned names remove.

    python benchmarks/page_weight.py

A local measure of bytes, not of time: no network, no browser. A browser also
fetches the calendar's data and an icon or two, which are not counted here.
"""

from __future__ import annotations

import gzip
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fastapi.testclient import TestClient

from cps import console
from cps.showcase import seed
from cps.web import Config, create_app

NAMED = re.compile(r'<(?:script[^>]*\bsrc|link[^>]*\bhref)="(/static/[^"]+)"')
IMPORTS = re.compile(r'^\s*import\b[^"\']*["\'](\.{1,2}/[^"\']+)["\']', re.M)
FONTS = re.compile(r'url\("?([^")]+\.woff2)"?\)')


def files_of(web: TestClient, html: str, base: str) -> dict[str, bytes]:
    """Every file a page asks for, followed through imports and the stylesheet."""
    found: dict[str, bytes] = {}
    queue = [urljoin(base, path) for path in NAMED.findall(html)]
    while queue:
        url = queue.pop()
        path = url.split("://", 1)[-1].split("/", 1)[-1]
        path = "/" + path
        if path in found or "/icons/" in path:
            continue
        answer = web.get(path)
        if answer.status_code != 200:
            continue
        found[path] = answer.content
        if path.endswith(".js"):
            queue += [urljoin(url, spec) for spec in IMPORTS.findall(answer.text)]
        elif path.endswith(".css"):
            # Only the Latin faces: the extended ones load for other alphabets only.
            queue += [urljoin(url, spec) for spec in FONTS.findall(answer.text) if "-ext" not in spec]
    return found


def main() -> int:
    console.ensure_utf8_output()
    with tempfile.TemporaryDirectory() as folder:
        where = Path(folder)
        alex = seed(where).you
        config = Config(store=where, background=False, sweep_every=None, remind_every=None)
        with TestClient(create_app(config), base_url="http://testserver") as web:
            today = web.get(f"/p/{alex}")
            files = files_of(web, today.text, f"http://testserver/p/{alex}")
            rows = [
                ("/p/<alex> (the page)", today.content),
                *sorted(files.items(), key=lambda kv: -len(kv[1])),
            ]
            raw = sum(len(body) for _, body in rows)
            packed = sum(len(gzip.compress(body, 6)) for _, body in rows)
            print(f"A first visit to Today: {len(rows)} answers, {raw / 1024:.0f} KB, ", end="")
            print(f"{packed / 1024:.0f} KB with gzip")
            for name, body in rows:
                print(
                    f"  {len(body) / 1024:6.1f} KB  {len(gzip.compress(body, 6)) / 1024:6.1f} KB gzip  {name}"
                )
            progress = web.get(f"/p/{alex}/progress")
            again = files_of(web, progress.text, f"http://testserver/p/{alex}/progress")
            kept = [path for path in again if path in files]
            asked = [
                path for path in kept if "immutable" not in web.get(path).headers.get("cache-control", "")
            ]
            print(
                f"Progress next: {len(again)} files, {len(kept)} already kept, "
                f"{len(asked)} of them asked about again ({len(progress.content) / 1024:.0f} KB of page)"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

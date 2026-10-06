"""Download the published color books that build_catalog.py reads.

Each brand is fetched from the brand's own site and saved under data/source,
so the catalog can be rebuilt without going back online.

    python scripts/fetch_sources.py            # every brand below
    python scripts/fetch_sources.py glidden    # one brand
"""

from __future__ import annotations

import html
import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "data" / "source"
AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129 Safari/537.36"


def get(url: str, tries: int = 4) -> str:
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": AGENT})
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read().decode("utf-8", errors="replace")
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(url)


def save(filename: str, payload) -> None:
    SOURCE.mkdir(parents=True, exist_ok=True)
    (SOURCE / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("wrote", filename, len(payload))


def sherwin_williams() -> None:
    rows = json.loads(get("https://api.sherwin-williams.com/prism/v1/colors/sherwin"))
    save("sherwin-williams-prism.json", rows)


def behr() -> None:
    text = get("https://www.behr.com/mainService/services/colornx/all.v2.js")
    table = json.loads(text[text.index("[") : text.rindex("]") + 1])
    header = table[0]
    save("behr-colornx.json", [dict(zip(header, row)) for row in table[1:]])


def valspar() -> None:
    page = get("https://www.valspar.com/en/colors/browse-colors")
    rows = []
    for attrs in re.findall(r'<div class="grid--wall__item[^"]*"([^>]*)>', page):
        item = {key: html.unescape(value) for key, value in re.findall(r'data-([\w-]+)="([^"]*)"', attrs)}
        if item.get("color-id") and item.get("hex"):
            rows.append(
                {
                    "code": item["color-id"],
                    "name": item.get("color-name", ""),
                    "hex": item["hex"],
                    "family": item.get("color-family", ""),
                    "tags": item.get("tag", ""),
                }
            )
    save("valspar-site.json", rows)


def _glidden_color(url: str) -> dict | None:
    page = get(url)
    name = re.search(r'<h1 class="heading-style-h2">(.*?)</h1>', page)
    code = re.search(r'<h1 class="heading-style-h2">.*?</h1><div class="flex-hoz"><div class="heading-style-h6">(.*?)</div>', page)
    rgb = re.search(r'class="rgb-box" data-rgb="(\d+),\s*(\d+),\s*(\d+)"', page)
    if not (name and code and rgb):
        return None
    return {
        "code": html.unescape(code.group(1)).strip(),
        "name": html.unescape(name.group(1)).strip(),
        "rgb": [int(part) for part in rgb.groups()],
        "url": url,
    }


def glidden() -> None:
    sitemap = get("https://www.glidden.com/sitemap.xml")
    urls = [url for url in re.findall(r"<loc>(.*?)</loc>", sitemap) if "/colors/" in url]
    rows, missed = [], []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for index, (url, row) in enumerate(zip(urls, pool.map(_safe(_glidden_color), urls)), 1):
            (rows.append(row) if row else missed.append(url))
            if index % 250 == 0:
                print(f"glidden {index}/{len(urls)}")
    if missed:
        print("glidden pages without a color:", len(missed), missed[:5])
    save("glidden-site.json", rows)


def _safe(fetch):
    def run(url: str):
        try:
            return fetch(url)
        except Exception as error:
            print("failed", url, error)
            return None

    return run


BRANDS = {
    "sherwin-williams": sherwin_williams,
    "behr": behr,
    "valspar": valspar,
    "glidden": glidden,
}


def main() -> None:
    for name in sys.argv[1:] or list(BRANDS):
        BRANDS[name]()


if __name__ == "__main__":
    main()

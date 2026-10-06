"""Build data/brands/*.json from the published color books in data/source.

Behr comes from Behr's own color index. Benjamin Moore comes from their color
pages. Sherwin-Williams is the ColorSnap book. The other brands are the
published color-book files. Hex values are copied, not invented.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "data" / "source"
OUT = ROOT / "data" / "brands"

NOTE = (
    "Published color-book values. Behr is Behr's own color index, including archived colors. "
    "Benjamin Moore is taken from their color pages. Sherwin-Williams is the ColorSnap book, "
    "checked against their published hex values. PPG, Valspar, Dunn-Edwards, Farrow & Ball, Kilz, "
    "and Dutch Boy are their published books. A newer limited color may not be listed yet."
)


def clean_name(name: str) -> str:
    name = name.replace("\u00ae", "").replace("\u2122", "").replace("\ufffd", "")
    return re.sub(r"\s+", " ", name).strip()


def clean_hex(value: str) -> str | None:
    text = str(value).strip()
    rgb = re.fullmatch(r"rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)", text, re.IGNORECASE)
    if rgb:
        numbers = [int(part) for part in rgb.groups()]
        if any(number > 255 for number in numbers):
            return None
        return "#{:02X}{:02X}{:02X}".format(*numbers)
    text = text.lstrip("#").strip()
    if re.fullmatch(r"[0-9A-Fa-f]{6}", text):
        return f"#{text.upper()}"
    return None


def add(colors: list[dict], seen: set[tuple[str, str]], brand: str, code: str, name: str, hex_value: str, archived: bool = False) -> None:
    name = clean_name(name)
    code = clean_name(str(code))
    hex_value = clean_hex(hex_value) or ""
    if not name or not code or not hex_value:
        return
    key = (brand, code.upper())
    if key in seen:
        return
    seen.add(key)
    row = {"brand": brand, "code": code, "name": name, "hex": hex_value}
    if archived:
        row["archived"] = True
    colors.append(row)


def main() -> None:
    colors: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for row in json.loads((SOURCE / "sherwin-williams.json").read_text(encoding="utf-8")):
        add(colors, seen, "sherwin-williams", f"SW{int(row['label']):04d}", row["name"], row["hex"])

    for row in json.loads((SOURCE / "behr-colormapper.json").read_text(encoding="utf-8"))["colors"]:
        add(colors, seen, "behr", row["code"], row["name"], row["hex"], bool(row.get("archived")))

    for row in json.loads((SOURCE / "benjamin-moore-wesbos.json").read_text(encoding="utf-8")):
        add(colors, seen, "benjamin-moore", row["number"], row["name"], row["hex"])

    for filename, brand in (
        ("ppg.json", "ppg"),
        ("valspar.json", "valspar"),
        ("dunn-edwards.json", "dunn-edwards"),
        ("farrow-ball.json", "farrow-ball"),
        ("kilz.json", "kilz"),
        ("dutch.json", "dutch-boy"),
    ):
        for row in json.loads((SOURCE / filename).read_text(encoding="utf-8")):
            add(colors, seen, brand, row["label"], row["name"], row["hex"])

    names = {
        "sherwin-williams": "Sherwin-Williams",
        "behr": "Behr",
        "benjamin-moore": "Benjamin Moore",
        "ppg": "PPG",
        "valspar": "Valspar",
        "dunn-edwards": "Dunn-Edwards",
        "farrow-ball": "Farrow & Ball",
        "kilz": "Kilz",
        "dutch-boy": "Dutch Boy",
    }
    order = list(names)
    brands = []
    for brand in order:
        count = sum(1 for color in colors if color["brand"] == brand)
        brands.append({"id": brand, "name": names[brand], "count": count})
        print(f"{names[brand]}: {count}")

    colors.sort(key=lambda color: (order.index(color["brand"]), color["name"].lower(), color["code"]))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.json").write_text(
        json.dumps({"note": NOTE, "brands": brands}, indent=2) + "\n",
        encoding="utf-8",
    )
    for brand in order:
        rows = [color for color in colors if color["brand"] == brand]
        lines = [json.dumps(row, separators=(",", ":")) for row in rows]
        (OUT / f"{brand}.json").write_text("[\n" + ",\n".join(lines) + "\n]\n", encoding="utf-8")
    print("wrote", OUT, "colors", len(colors))


if __name__ == "__main__":
    main()

"""Draw the RoomRoller app icons (house outline and door, from the favicon) as PNGs."""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "static" / "icons"
PAPER = (244, 240, 234)
ORANGE = (196, 92, 38)


def icon(size: int, padding: float) -> Image.Image:
    scale = 4  # draw large, then shrink for smooth edges
    big = size * scale
    image = Image.new("RGB", (big, big), PAPER)
    draw = ImageDraw.Draw(image)
    inner = big * (1 - 2 * padding)
    unit = inner / 32
    ox = oy = big * padding

    def p(x: float, y: float) -> tuple[float, float]:
        return ox + x * unit, oy + (y + 2) * unit  # the house spans y 6-22; +2 centers it

    width = max(2, round(2.2 * unit))
    draw.polygon([p(6, 22), p(6, 12), p(16, 6), p(26, 12), p(26, 22)], outline=ORANGE, width=width)
    draw.rectangle([p(13, 16), p(19, 22)], fill=ORANGE)
    return image.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    icon(180, 0.08).save(OUT / "apple-touch-icon.png")
    icon(192, 0.08).save(OUT / "icon-192.png")
    icon(512, 0.08).save(OUT / "icon-512.png")
    # Android crops "maskable" icons to a circle or squircle, so keep the drawing in the middle 60%.
    icon(512, 0.2).save(OUT / "icon-maskable-512.png")
    print("wrote", sorted(path.name for path in OUT.glob("*.png")))


if __name__ == "__main__":
    main()

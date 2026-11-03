"""Draw assets/fox.ico, the icon for Fox.exe and its shortcut. Same shapes as fox/gui/static/fox.svg."""

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 1024
INK, ORANGE, SHADE, CREAM = "#1f1b18", "#f2711c", "#b84a0e", "#fff3e6"
SHAPES = [
    (ORANGE, [(14, 14), (36, 34), (64, 34), (86, 14), (84, 54), (50, 88), (16, 54)]),
    (SHADE, [(20, 23), (33, 35), (22, 42)]),
    (SHADE, [(80, 23), (67, 35), (78, 42)]),
    (CREAM, [(16, 54), (38, 52), (50, 88)]),
    (CREAM, [(84, 54), (62, 52), (50, 88)]),
    (INK, [(44, 77), (56, 77), (50, 88)]),
    (INK, [(28, 42), (41, 49), (30, 50)]),
    (INK, [(72, 42), (59, 49), (70, 50)]),
]


def main() -> None:
    scale = SIZE / 100
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=int(22 * scale), fill=INK)
    for color, points in SHAPES:
        draw.polygon([(x * scale, y * scale) for x, y in points], fill=color)

    output = Path(__file__).resolve().parents[1] / "assets" / "fox.ico"
    output.parent.mkdir(exist_ok=True)
    image.resize((256, 256), Image.LANCZOS).save(output, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()

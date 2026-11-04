# Draws desktop/build/icon.png, the app icon every package uses: the Miso Chat logo, the same face the
# web pages use (web/static/icon.svg), at 1024 pixels for the Windows, macOS and Linux packages.
#
#     python desktop/tools/make_icon.py
#
# electron-builder turns the one PNG into .ico on Windows and .icns on macOS, so there is no other
# icon file to keep in step. Pillow is already a server requirement.

import os

from PIL import Image, ImageDraw

SIZE = 1024
RADIUS = 224          # the 112 of the logo, doubled
START = (181, 230, 225)   # #b5e6e1
END = (143, 211, 204)     # #8fd3cc
INK = (11, 16, 22, 255)   # #0b1016
FACE = (244, 248, 250, 255)  # #f4f8fa
CHEEK = (134, 184, 217, 153)  # #86b8d9 at 60%


def background():
    # The logo's diagonal wash, made small and stretched, then trimmed to its rounded square.
    small = Image.new("RGB", (64, 64))
    px = small.load()
    for y in range(64):
        for x in range(64):
            t = (x + y) / 126.0
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(START, END))
    wash = small.resize((SIZE, SIZE), Image.BICUBIC)
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=RADIUS, fill=255)
    out = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    out.paste(wash, (0, 0), mask)
    return out


def draw_logo():
    img = background()
    draw = ImageDraw.Draw(img)
    # Ears.
    for cx in (272, 752):
        draw.ellipse([cx - 96, 300 - 96, cx + 96, 300 + 96], fill=INK)
    # Face.
    draw.ellipse([512 - 300, 592 - 256, 512 + 300, 592 + 256], fill=FACE)
    # Eyes and their highlights.
    for cx in (392, 632):
        draw.ellipse([cx - 80, 564 - 68, cx + 80, 564 + 68], fill=INK)
    for cx in (412, 652):
        draw.ellipse([cx - 18, 548 - 18, cx + 18, 548 + 18], fill=FACE)
    # Cheeks, then the nose and mouth.
    cheeks = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    cheek_draw = ImageDraw.Draw(cheeks)
    for cx in (320, 704):
        cheek_draw.ellipse([cx - 40, 672 - 40, cx + 40, 672 + 40], fill=CHEEK)
    img.alpha_composite(cheeks)
    draw = ImageDraw.Draw(img)
    draw.ellipse([512 - 44, 668 - 30, 512 + 44, 668 + 30], fill=INK)
    return img


def build():
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "build", "icon.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    draw_logo().save(out, "PNG")
    return out


if __name__ == "__main__":
    print(build())

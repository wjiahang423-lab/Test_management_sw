"""Generate brand assets: Zioneer logo and EOL app icon.

Output (all white background, blue text):
  assets/logo_zioneer.png   page logo, text "Zioneer"
  assets/icon_eol.png       app icon bitmap, text "EOL"
  assets/icon_eol.ico       Windows icon bundle (used by build_app.py)

Re-run with:  python3 tools/generate_assets.py
Adjust text / colors / font below, then rebuild the app.
"""
import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(ROOT, "assets")

BLUE = (31, 90, 168, 255)        # #1F5AA8
WHITE = (255, 255, 255, 255)

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_BOLD_CJK = "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc"


def _font(size, cjk=False):
    path = FONT_BOLD_CJK if cjk else FONT_BOLD
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def draw_text_image(size, text, font_size, cjk=False):
    w, h = size
    img = Image.new("RGBA", (w, h), WHITE)
    draw = ImageDraw.Draw(img)
    font = _font(font_size, cjk)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (w - tw) / 2 - bbox[0]
    y = (h - th) / 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=BLUE)
    return img


def main():
    os.makedirs(ASSETS_DIR, exist_ok=True)

    logo = draw_text_image((480, 150), "Zioneer", 88)
    logo.save(os.path.join(ASSETS_DIR, "logo_zioneer.png"))
    print("saved:", os.path.join(ASSETS_DIR, "logo_zioneer.png"))

    icon = draw_text_image((256, 256), "EOL", 96)
    icon.save(os.path.join(ASSETS_DIR, "icon_eol.png"))
    print("saved:", os.path.join(ASSETS_DIR, "icon_eol.png"))

    ico_path = os.path.join(ASSETS_DIR, "icon_eol.ico")
    icon.save(ico_path, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    print("saved:", ico_path)


if __name__ == "__main__":
    main()

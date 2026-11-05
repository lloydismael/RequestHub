from django.contrib.staticfiles import finders
from django.test import SimpleTestCase
from PIL import Image

from accounts.themes import contrast_ratio, custom_theme_style, custom_theme_vars, normalize_hex


class ChristmasFrameAssetTests(SimpleTestCase):
    def test_border_artwork_is_transparent_nine_slice_image(self):
        path = finders.find("images/christmas-frame.webp")
        self.assertIsNotNone(path)
        with Image.open(path) as image:
            self.assertEqual(image.size, (1152, 768))
            alpha = image.convert("RGBA").getchannel("A")
        self.assertEqual(alpha.getpixel((576, 384)), 0)
        self.assertGreater(alpha.getpixel((5, 5)), 200)
        self.assertIsNone(alpha.crop((300, 0, 842, 215)).point(lambda value: 255 if value > 8 else 0).getbbox())
        with open(finders.find("css/christmas-theme.css"), encoding="utf-8") as stylesheet:
            self.assertIn("christmas-frame.webp') 215 310 180 300", stylesheet.read())

    def test_santa_flyover_artwork_is_valid_svg_used_by_stylesheet(self):
        from xml.etree import ElementTree

        path = finders.find("images/christmas-santa-sleigh.svg")
        self.assertIsNotNone(path)
        root = ElementTree.parse(path).getroot()
        self.assertEqual(root.get("viewBox"), "0 0 640 210")
        self.assertEqual(len(root.findall(".//{http://www.w3.org/2000/svg}use")), 3)
        with open(finders.find("css/christmas-theme.css"), encoding="utf-8") as stylesheet:
            self.assertIn("url('../images/christmas-santa-sleigh.svg')", stylesheet.read())


class CustomThemeTests(SimpleTestCase):
    def test_normalize_hex_accepts_only_six_digit_hex(self):
        self.assertEqual(normalize_hex(" #FACC15 "), "#facc15")
        for value in ["", None, "red", "#fff", "#12345g", "url(x)", "#1234567"]:
            with self.subTest(value=value):
                self.assertEqual(normalize_hex(value), "")

    def test_invalid_palette_yields_no_style(self):
        self.assertIsNone(custom_theme_vars("#2563eb", "", "#06b6d4"))
        self.assertEqual(custom_theme_style("#2563eb", "bad", "#06b6d4"), "")

    def test_accents_are_readable_on_light_and_dark_surfaces(self):
        for primary in ["#ffffff", "#facc15", "#000000", "#2563eb", "#059669"]:
            with self.subTest(primary=primary):
                theme = custom_theme_vars(primary, "#7c3aed", "#06b6d4")
                self.assertGreaterEqual(contrast_ratio(theme["--account-accent"], "#ffffff"), 4.5)
                self.assertGreaterEqual(contrast_ratio(theme["--account-dark-accent"], "#141b2c"), 7.0)

    def test_style_is_stable_for_known_palette(self):
        self.assertEqual(
            custom_theme_style("#FACC15", "#22d3ee", "#a3e635"),
            "--account-c1: #facc15; --account-c2: #22d3ee; --account-c3: #a3e635; "
            "--account-accent: #8a700c; --account-dark-accent: #facc15; --account-rgb: 138, 112, 12;",
        )

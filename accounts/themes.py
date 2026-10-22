"""Account theme helpers: custom palette validation and readable accent derivation."""
import re

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

DEFAULT_CUSTOM_COLORS = ("#2563eb", "#7c3aed", "#06b6d4")

LIGHT_SURFACE = "#ffffff"
DARK_SURFACE = "#141b2c"
MIN_CONTRAST = 4.5
MIN_CONTRAST_DARK = 7.0


def normalize_hex(value):
    value = (value or "").strip()
    return value.lower() if HEX_COLOR_RE.match(value) else ""


def _to_rgb(hex_color):
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def _to_hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, int(c + 0.5))) for c in rgb))


def _luminance(rgb):
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(first, second):
    lighter, darker = sorted((_luminance(_to_rgb(first)), _luminance(_to_rgb(second))), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _shift_until_readable(color, surface, target, minimum):
    """Blend `color` toward `target` until it reads on `surface` at the given contrast."""
    rgb = _to_rgb(color)
    goal = _to_rgb(target)
    for step in range(21):
        amount = step / 20
        candidate = _to_hex(tuple(c + (g - c) * amount for c, g in zip(rgb, goal)))
        if contrast_ratio(candidate, surface) >= minimum:
            return candidate
    return target


def custom_theme_vars(primary, secondary, tertiary):
    """CSS variables for a custom palette, or None when any colour is invalid."""
    colors = [normalize_hex(c) for c in (primary, secondary, tertiary)]
    if not all(colors):
        return None
    primary, secondary, tertiary = colors
    accent = _shift_until_readable(primary, LIGHT_SURFACE, "#000000", MIN_CONTRAST)
    dark_accent = _shift_until_readable(primary, DARK_SURFACE, "#ffffff", MIN_CONTRAST_DARK)
    return {
        "--account-c1": primary,
        "--account-c2": secondary,
        "--account-c3": tertiary,
        "--account-accent": accent,
        "--account-dark-accent": dark_accent,
        "--account-rgb": ", ".join(str(c) for c in _to_rgb(accent)),
    }


def custom_theme_style(primary, secondary, tertiary):
    theme_vars = custom_theme_vars(primary, secondary, tertiary)
    if not theme_vars:
        return ""
    return "; ".join(f"{name}: {value}" for name, value in theme_vars.items()) + ";"

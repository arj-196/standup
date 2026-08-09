"""The Watch's chrome palette: every color is a role, never a decoration.

Source of truth: the "Watch Redesign v2 — Color" design spec, as amended by
ADR 0004 § the removed-row field. Three channels, never fewer — the gutter says
*what changed*, the text says *what it is*, and on removed rows the surface
says *what changed* a second time. Every role also carries a glyph or attribute
(its NO_COLOR carrier), so no distinction lives in color alone.

Three tiers of fallback, straight from the spec's palette table: truecolor
hexes, explicit 256-color indices, and named 16-color approximations. Under
NO_COLOR only the attributes (bold / dim / italic) remain. The syntax palette
(monokai, foreground-only) is a separate, licensed system and lives in
watchui, not here.

The light values are kept and still resolve, but nothing reaches them: the
`--light` flag was withdrawn from `standup watch`. The chrome was not the
problem — the blocker is that the syntax palette has no light variant, so
monokai's near-white plain foreground disappeared into the light surface and
took the code bodies with it, which is worse than no light mode at all. These
values are the spec's, they are correct, and they are what a future light
mode would be built from once a light-page syntax theme is chosen. Reaching
them is deliberately not possible until then.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from rich.color import Color
from rich.style import Style


@dataclass(frozen=True)
class Role:
    """One palette role: dark/light truecolor, 256 and 16-color fallbacks,
    plus the attributes that survive NO_COLOR."""
    dark: str
    light: str
    c256_dark: int
    c256_light: int
    c16: str | None = None       # rich color name, or None (no 16-color hue)
    bold: bool = False
    dim: bool = False
    italic: bool = False


# Chrome roles (spec §1). Rank by saturation: on any row the most actionable
# token is the most saturated — handle over @sha, fact over claim.
ROLES: dict[str, Role] = {
    "surface":   Role("#101418", "#FAFBFC", 233, 255),
    "raised":    Role("#1A2028", "#EEF1F5", 235, 254),
    "primary":   Role("#D6DBE3", "#22272E", 253, 235, "white"),
    "muted":     Role("#8A93A2", "#57606C", 246, 241, "bright_black"),
    "faint":     Role("#636D7C", "#848E9C", 242, 246, "bright_black", dim=True),
    "address":   Role("#56D4E8", "#086F7E", 80, 30, "cyan"),
    "reference": Role("#7D8590", "#767E88", 245, 245, "bright_black"),
    "claim":     Role("#C7A96B", "#7A6224", 179, 94, "yellow", italic=True),
    "chapter":   Role("#B49DF7", "#5B3FBF", 141, 61, "magenta"),
    "added":     Role("#6FCF7C", "#1F7A33", 114, 28, "green"),
    "removed":   Role("#F07178", "#B3362E", 210, 131, "red"),
    "file":      Role("#DCE3EC", "#1C2127", 254, 234, "white"),
    "git":       Role("#E5B84D", "#8A5F00", 221, 94, "bright_yellow"),
    "live":      Role("#6FCF7C", "#1F7A33", 114, 28, "green"),  # alias of added
    "selection": Role("#2C3A4D", "#D4E2F2", 237, 253),
    # file extensions: a desaturated alias in the address family (spec census);
    # the light value is derived — the spec defines only the dark alias
    "ext":       Role("#6FA8B8", "#3E7280", 109, 24, "cyan"),
    # the removed-row wash (ADR 0004 § the removed-row field): a red tint one
    # step off `surface`, not a shade of `removed` — monokai's foregrounds are
    # bright and need a dark substrate, so the dark value darkens and only the
    # light value lightens. Deliberately quieter than `selection`, which
    # outranks it. The 256 value is the one place the whisper can't be
    # honoured: the color cube's darkest red is #5f0000, dark in luminance but
    # saturated, so a 256-color terminal shows a louder wash than a truecolor
    # one.
    "removed_bg": Role("#291A1E", "#FCEBEB", 52, 224),
}

# Identity palette: per-session hues, aid-only — the carrier is the lane digit,
# and sessions past the third get no hue at all (digit only).
SESSION_HUES: list[Role] = [
    Role("#6CA6E8", "#2F5FA8", 110, 25, None),   # session 1 · blue
    Role("#E08BC8", "#A0468F", 175, 133, None),  # session 2 · pink
    Role("#66C7A8", "#1F7A62", 79, 29, None),    # session 3 · teal
]


def _detect_depth() -> str:
    """truecolor | 256 | 16 | none — from the same signals rich reads."""
    if os.environ.get("NO_COLOR"):
        return "none"
    from rich.console import Console
    system = Console().color_system
    if system is None:
        return "none"
    return {"truecolor": "truecolor", "256": "256"}.get(system, "16")


class Theme:
    """Resolves roles to rich Styles for one (variant, depth) pair.

    `light` is unreachable from the CLI by design (see the module docstring);
    every caller gets the dark variant."""

    def __init__(self, light: bool = False, depth: str | None = None):
        self.light = light
        self.depth = depth or _detect_depth()

    # -- resolution -----------------------------------------------------------

    def _color(self, role: Role) -> Color | None:
        if self.depth == "truecolor":
            return Color.parse(role.light if self.light else role.dark)
        if self.depth == "256":
            return Color.from_ansi(role.c256_light if self.light else role.c256_dark)
        if self.depth == "16" and role.c16:
            return Color.parse(role.c16)
        return None    # NO_COLOR (or a role with no 16-color hue): carrier only

    def style(self, name: str, bold: bool = False, italic: bool = False) -> Style:
        role = ROLES[name]
        # the dim attribute is the low-depth stand-in for the faint tier: at
        # truecolor/256 the hex alone carries it (dim would double-fade)
        dim = role.dim and self.depth in ("16", "none")
        return Style(color=self._color(role),
                     bold=bold or role.bold or None,
                     dim=dim or None,
                     italic=italic or role.italic or None)

    def background(self, name: str) -> Style | None:
        """A role as a background wash, or None where backgrounds don't exist
        (16 colors and NO_COLOR — there the glyph is the only carrier). Sets
        bgcolor alone, so it composes with the foreground-only syntax spans in
        either order."""
        if not self.paints_backgrounds:
            return None
        return Style(bgcolor=self._color(ROLES[name]))

    def hex(self, name: str) -> str:
        """The truecolor value for CSS — bands and selection degrade to
        attributes below truecolor/256 (see css_colors)."""
        role = ROLES[name]
        return role.light if self.light else role.dark

    def session(self, num: int, bold: bool = False) -> Style:
        """The identity hue of session lane `num` (1-based); past the palette
        the digit alone is the identity."""
        if 1 <= num <= len(SESSION_HUES) and self.depth in ("truecolor", "256"):
            return Style(color=self._color(SESSION_HUES[num - 1]), bold=bold or None)
        return Style(bold=bold or None)

    # -- CSS hooks -------------------------------------------------------------

    @property
    def paints_backgrounds(self) -> bool:
        """Bands and the selection wash exist only at truecolor/256; at 16
        colors and under NO_COLOR the selection falls back to reverse video
        and the bands to plain rows (the chip glyphs carry the state)."""
        return self.depth in ("truecolor", "256")

    def css_color(self, name: str) -> str:
        role = ROLES[name]
        if self.depth == "256":
            n = role.c256_light if self.light else role.c256_dark
            return Color.from_ansi(n).get_truecolor().hex
        return self.hex(name)

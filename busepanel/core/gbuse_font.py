"""Original gBUSE font set (fonts 0–3 and 15 + code table) as used in BUSE panel data.

It is the same font set found in gBUSE data of several operators and in the BS120 panel itself,
stored in gbuse_font.yaml next to this module. Font 0 = thin, 3 = normal (bold).
"""
from __future__ import annotations

import os

from .fonts import FontSet

PATH = os.path.join(os.path.dirname(__file__), "gbuse_font.yaml")


def gbuse_fontset() -> FontSet:
    from .project import load_yaml
    return FontSet.from_yaml(load_yaml(PATH))


FONT_SOURCES = {"gbuse": "originální font gBUSE", "builtin": "vestavěný font (BUSE Studio)"}


def fontset(source: str = "gbuse") -> FontSet:
    """'gbuse' (original gBUSE fonts) or 'builtin' (own design)."""
    if source == "builtin":
        from .builtin_font import builtin_fontset
        return builtin_fontset()
    if source == "gbuse":
        return gbuse_fontset()
    raise ValueError(f"neznámý font {source!r} (k dispozici: {', '.join(FONT_SOURCES)})")

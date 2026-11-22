"""Pictogram font for panels (transport modes, warning, transfer …).

Icons live in their own font (ICON_FONT) at codes 0xC1… (outside the character code table,
so they never clash with letters). In texts they are written as {icon:name}; the encoder emits
E0+ICON_FONT, code, E0+<previous font>.
"""
from __future__ import annotations

ICON_FONT = 14
FIRST_CODE = 0xC1

ICONS = {
    "bus": [
        ".########.",
        "#..#..#..#",
        "#..#..#..#",
        "##########",
        "#........#",
        "##########",
        ".##....##.",
        "..........",
    ],
    "tramvaj": [
        "...#.#....",
        "....#.....",
        ".########.",
        "#.##.##.##",
        "#.##.##.##",
        "##########",
        "##########",
        ".#......#.",
    ],
    "trolejbus": [
        ".#.....#..",
        "..#...#...",
        ".########.",
        "#.##.##..#",
        "##########",
        "#........#",
        "##########",
        ".##....##.",
    ],
    "vlak": [
        "..######..",
        ".#......#.",
        ".#.####.#.",
        ".#.####.#.",
        ".########.",
        ".##....##.",
        "..######..",
        ".#......#.",
    ],
    "metro": [
        ".######.",
        "##....##",
        "#.#..#.#",
        "#..##..#",
        "#......#",
        "#......#",
        "##....##",
        ".######.",
    ],
    "pozor": [
        "....#....",
        "...#.#...",
        "...#.#...",
        "..#.#.#..",
        "..#.#.#..",
        ".#.....#.",
        ".#..#..#.",
        "#########",
    ],
    "vozik": [
        "..##....",
        "..##....",
        "...#....",
        "...####.",
        ".#.#..#.",
        "#..#..#.",
        "#...##..",
        ".###....",
    ],
    "prestup": [
        "....#...",
        "######..",
        "....#...",
        "........",
        "...#....",
        "..######",
        "...#....",
        "........",
    ],
    "sipka": [
        "....#...",
        "....##..",
        "#######.",
        "########",
        "#######.",
        "....##..",
        "....#...",
        "........",
    ],
    "letadlo": [
        "....#....",
        "....##...",
        "#...###..",
        "#########",
        "#...###..",
        "....##...",
        "....#....",
        ".........",
    ],
}

ICON_CODES = {name: FIRST_CODE + i for i, name in enumerate(ICONS)}
CODE_ICONS = {v: k for k, v in ICON_CODES.items()}


def add_icon_font(fontset, font_id: int = ICON_FONT, replace: bool = True):
    """Add (or replace) the pictogram font in a FontSet."""
    from .fonts import Font, Glyph
    if fontset.font(font_id) is not None:
        if not replace:
            return fontset.font(font_id)
        fontset.fonts = [f for f in fontset.fonts if f.id != font_id]
    f = Font(font_id)
    for name, rows in ICONS.items():
        code = ICON_CODES[name]
        f.glyphs[code] = Glyph.from_rows(code, rows)
    # insert before the special font 15 to keep ids ascending
    fontset.fonts.append(f)
    fontset.fonts.sort(key=lambda x: x.id)
    return f


def icon_bytes(name: str, restore_font: int | None) -> bytes:
    if name not in ICON_CODES:
        raise KeyError(f"neznámá ikona {name!r} (k dispozici: {', '.join(ICONS)})")
    out = bytes([0xE0 + ICON_FONT, ICON_CODES[name]])
    if restore_font is not None:
        out += bytes([0xE0 + restore_font])
    return out

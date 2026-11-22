"""Pictograms for panels (transport modes, warning, transfer …).

BS120 firmware (EPROM 0x14E7) splits text bytes into: 0x20–0xAF characters, 0xB0–0xBF spacing,
0xC0–0xDF commands that are silently DROPPED, 0xE0–0xEF font, 0xF0–0xFE characters (0xFF ends a font).
Glyphs are looked up in the current font of the uploaded database (sorted search, 0x1322).
So the icons go into every text font at free character codes (CODES, verified on the panel)
and {icon:name} emits just the code. Original gBUSE pictograms 0xAF, 0xF1–0xF4 are kept;
0xF5 (slash) and 0xF6–0xFC (gBUSE test patterns) are overwritten.

Where the PID standard defines a pictogram for interior LED panels (SK bus, příloha 2, tabulka znaků,
sloupec „vnitřní panely“), the bitmap is taken from it: metro, vlak, letadlo, šipka, zvonek (znameni),
přívoz, výluka, ČD and the corners of the stylised metro letter ([B] = metro_l B metro_r).
"""
from __future__ import annotations

ICON_FONT = 14          # old separate pictogram font – the BS120 does not use it; removed from databases
CODES = [0xAA, 0xAB, 0xAC, 0xAD, 0xAE, 0xF0, 0xFA, 0xFB, 0xFC, 0xFD, 0xFE,     # in the order of ICONS
         0xF5, 0xF6, 0xF7, 0xF8, 0xF9]

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
    "vlak": [          # PID: kód 0x7E ve fontu vnitřních panelů
        "...........",
        "####..####.",
        ".##.#.#..#.",
        "#######..#.",
        "###########",
        "###########",
        "###########",
        ".##.##.##..",
    ],
    "metro": [          # PID: 0x3C
        "....#...#....",
        "###.##.##.###",
        ".##.#.#.#.##.",
        "..#.#...#.#..",
        ".............",
        "....#####....",
        ".....###.....",
        "......#......",
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
    "sipka": [          # PID: 0x3E
        ".......",
        "..#....",
        "..##...",
        "#####..",
        "######.",
        "#####..",
        "..##...",
        "..#....",
    ],
    "letadlo": [          # PID: 0x5C
        ".............",
        ".....###...##",
        "....###...##.",
        ".##########..",
        "##########...",
        "....###......",
        ".....###.....",
        "......###....",
    ],
    "znameni": [          # PID: 0xAA – zvonek, zastávka na znamení
        ".......",
        "...#...",
        "..###..",
        "..###..",
        "..###..",
        ".#####.",
        "#######",
        "...#...",
    ],
    "privoz": [          # PID: 0x8A – kotva, přestup na přívoz
        "...#...",
        "...#...",
        "..###..",
        "...#...",
        "#..#..#",
        "#..#..#",
        ".#.#.#.",
        "..###..",
    ],
    "vyluka": [          # PID: 0x24 – mimořádnost / změna trasy
        "######...####",
        "#####.....###",
        "###.#..#..###",
        "##..#..#..###",
        "#......#....#",
        "#.....###...#",
        "##..#########",
        "###.#########",
    ],
    "cd": [          # PID: 0x25 – logo ČD
        "..####.#####.",
        ".......######",
        ".#####.##..##",
        "######.##..##",
        "##.....##..##",
        "##.....##..##",
        "#############",
        ".###########.",
    ],
    "metro_l": [          # PID: 0x5B – levý roh stylizovaného písmene metra
        "...",
        "...",
        "###",
        ".##",
        "..#",
        "...",
        "...",
        "...",
    ],
    "metro_r": [          # PID: 0x5D – pravý roh (metro B = [B])
        "...",
        "...",
        "###",
        "##.",
        "#..",
        "...",
        "...",
        "...",
    ],
}

assert len(ICONS) <= len(CODES), "pro další ikonu chybí volný kód"
ICON_CODES = {name: CODES[i] for i, name in enumerate(ICONS)}
CODE_ICONS = {v: k for k, v in ICON_CODES.items()}


def add_icon_font(fontset, font_id: int = ICON_FONT, replace: bool = True):
    """Put the pictograms into every text font (ids below ICON_FONT) and drop the old separate
    pictogram font. Glyphs are kept sorted by code."""
    from .fonts import Glyph
    fontset.fonts = [f for f in fontset.fonts if f.id != font_id]
    for f in fontset.fonts:
        if f.id >= ICON_FONT:
            continue
        glyphs = dict(f.glyphs)
        for name, rows in ICONS.items():
            code = ICON_CODES[name]
            if replace or code not in glyphs:
                glyphs[code] = Glyph.from_rows(code, rows)
        f.glyphs = dict(sorted(glyphs.items()))
    return fontset


def icon_bytes(name: str, restore_font: int | None = None, fonts=None) -> bytes:
    """The icon is a character of the current text font -> just its code."""
    if name not in ICON_CODES:
        raise KeyError(f"neznámá ikona {name!r} (k dispozici: {', '.join(ICONS)})")
    return bytes([ICON_CODES[name]])


"""gBUSE0 – data and behaviour of the exterior DOT-LED panels (BS210, BS110 with FW 2.x).

Sources (docs/PROTOKOL.md 4.6 and 10): the original gBUSE0 manual (mnemo language, firmware levels)
and sample gBUSE0 HEX files (front / side / rear panels).

  database.py – reads a gBUSE0 HEX: header (fields, presets), fonts, LIN / CIL / CIL4 / DRU records
  program.py  – the panel's interpreter of one record (windows, fonts, LF/VT cycles, b/w blocking)
"""

"""Program-wide settings (stored in the working folder as nastaveni.yaml).

tight_spacing – allow letter spacing 0 ("tight" font). Off by default: texts without gaps between the
letters are hard to read, so a text that does not fit takes a smaller font instead.
"""
from __future__ import annotations

import os

import yaml

FILE = "nastaveni.yaml"
DEFAULTS = {"tight_spacing": False}
_current: dict = dict(DEFAULTS)


def get() -> dict:
    return dict(_current)


def load(workdir: str | None) -> dict:
    _current.clear()
    _current.update(DEFAULTS)
    p = os.path.join(workdir, FILE) if workdir else None
    if p and os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        _current.update({k: data[k] for k in DEFAULTS if k in data})
    return get()


def update(workdir: str | None, values: dict) -> dict:
    _current.update({k: type(DEFAULTS[k])(v) for k, v in values.items() if k in DEFAULTS})
    if workdir:
        with open(os.path.join(workdir, FILE), "w", encoding="utf-8") as f:
            yaml.safe_dump(get(), f, allow_unicode=True, sort_keys=False)
    return get()


def min_spacing() -> int:
    return 0 if _current.get("tight_spacing") else 1


def spacing(value):
    """Clamp a letter spacing from a text format to the program setting (None stays None)."""
    return None if value is None else max(int(value), min_spacing())

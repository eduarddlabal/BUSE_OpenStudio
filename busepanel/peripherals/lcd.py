"""LCD panel (add-on busepanel/extras/lcd) as a peripheral: no bus, the web page reads the state itself."""
from __future__ import annotations

from .base import Peripheral


class LcdSlot(Peripheral):
    kind = "lcd"
    has_bus = False

    def __init__(self, name, role, output, params=None):
        super().__init__(name, role, None, None, params)
        self._out = output

    @property
    def output(self):
        return self._out

    def frame(self, now=None):
        return None

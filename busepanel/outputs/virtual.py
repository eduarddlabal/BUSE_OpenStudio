"""Virtual BS120 – emulates the panel firmware closely enough for previews and simulation.

Behaviour (firmware analysis + tests on the real panel, PROTOKOL.md 9.x):
* after power-on: mode "normal" = line field + destination
* l<3 digits>, z<3 digits>, zC<4>, v<text>, zI/zM/zN<p><text> only *store* values
* x<4 digits>: 99xx = mode switch (x9999 normal, x9996 text, x9997 line+text, …),
  any other number = stop-only mode (then the real panel processes only further x)
* free text v is shown only in modes text / line_text (verified)
"""
from __future__ import annotations

import re
import time

from ..core.render import preview_item
from .base import Output, tel_text

MODE_SWITCH = {"9999": "normal", "9998": "unknown", "9997": "line_text", "9996": "text", "9995": "dest_only",
               "9994": "line_zI", "9993": "line_zC", "9992": "zC", "9991": "xA", "9990": "zM",
               "9989": "xB", "9988": "zD", "9987": "zN", "9986": "text0700"}
MODE_TITLE = {"normal": "linka + cíl", "text": "volný text (v)", "line_text": "linka + text (v)", "dest_only": "jen cíl",
              "line_zI": "linka + zI", "zM": "text zM", "zN": "text zN", "zC": "cíl 4místný", "stop": "zastávka",
              "line_zC": "linka + zC", "xA": "mimořádná inf.", "xB": "zastávka xB"}


class VirtualPanel(Output):
    kind = "virtual"

    def __init__(self, name: str, project=None, profile=None):
        super().__init__(name)
        from ..panels.profile import profile as get_profile
        self.project = project
        self.profile = profile or get_profile("BS120")
        self.reset()

    def reset(self):
        self.mode = "normal"
        self.values = {"l": None, "z": None, "zC": None, "x": None, "v": b"", "zI": b"", "zM": b"", "zN": b"", "xA": None}
        self.log: list[str] = []
        self.changed = time.time()

    # ---- input --------------------------------------------------------
    def send(self, *telegrams):
        for t in telegrams:
            self.feed(t if isinstance(t, bytes) else t.encode("cp1250"))
            self.sent += 1
        self.changed = time.time()

    def feed(self, b: bytes):
        self.log = (self.log + [time.strftime("%H:%M:%S ") + tel_text(b, self.project.fonts if self.project else None)])[-40:]
        s = b.decode("latin1")
        m = re.fullmatch(r"x(\d{4})", s)
        if m:
            key = m.group(1)
            if key in MODE_SWITCH:
                self.mode = MODE_SWITCH[key]
            elif key != self.values["x"] or self.mode != "stop":
                self.values["x"], self.mode = key, "stop"
            return
        for cmd, pat in (("zC", r"zC(\d{4})"), ("xA", r"xA(\d{4})"), ("z", r"z(\d{3})"), ("l", r"l(\d{3})")):
            m = re.fullmatch(pat, s)
            if m:
                v = m.group(1)
                if cmd == "l":
                    v = "0" + v[1:]      # firmware uses only the last two digits (measured)
                self.values[cmd] = v
                return
        for cmd in ("zI", "zM", "zN"):
            if b.startswith(cmd.encode()) and len(b) >= 3:
                self.values[cmd] = b[3:]
                return
        if b.startswith(b"v"):
            self.values["v"] = b[1:]

    # ---- output -------------------------------------------------------
    def _record(self, section: str, key):
        if not self.project or key is None:
            return None
        v = (self.project.data.get(section) or {}).get(key)
        if v is None:
            return None
        return self.project.encode_value(v)[0]

    def frame(self) -> dict:
        """Preview-format description: {"line": item|None, "dest": item|None, "mode": …}."""
        g = self.profile.geometry
        fonts = self.project.fonts if self.project else None
        defaults = self.project.defaults() if self.project else (3, 1)
        out = {"name": self.name, "mode": self.mode, "mode_title": MODE_TITLE.get(self.mode, self.mode),
               "geometry": g.__dict__, "line": None, "dest": None, "log": self.log[-8:], "changed": self.changed}
        if fonts is None:
            return out

        def item(b, area, lo=None, hi=None):
            return preview_item(b, fonts, g, defaults, area, lo, hi) if b else None

        two = g.rows > 8
        # two-line panel (PID layout): upper row = line number + heading next to it, lower row = text over
        # the whole width [ODHAD – how the real panel splits "heading LF text"]

        def split(b):
            """two-line panels: 'heading LF text' -> (heading, text)"""
            if b and g.rows > 8 and b"\n" in b:
                h, t = b.split(b"\n", 1)
                return h + b"\r", t
            return None, b

        line = self._record("lines", self.values["l"])
        text = self.values["v"] + b"\r" if self.values["v"] else None
        m = self.mode
        if m == "normal":
            out["line"], out["dest"] = item(line, "line"), item(self._record("destinations", self.values["z"]), "dest")
        elif m == "dest_only":
            out["dest"] = item(self._record("destinations", self.values["z"]), "full")
        elif m == "text":
            head, text = split(text)
            out["heading"], out["dest"] = item(head, "full"), item(text, "full")
        elif m == "line_text":
            head, text = split(text)
            out["line"] = item(line, "line")
            if two:
                out["heading"] = item(head, "full", g.dest_left)
                out["dest"] = item(text, "full")
            else:
                out["heading"] = item(head, "full")
                out["dest"] = item(text, "dest")
        elif m == "line_zI":
            zi = self.values["zI"] + b"\r" if self.values["zI"] else None
            out["line"], out["dest"] = item(line, "line"), item(zi, "dest")
        elif m in ("zM", "zN"):
            tx = self.values[m] + b"\r" if self.values[m] else None
            out["dest"] = item(tx, "full")
        elif m == "stop":
            out["dest"] = item(self._record("stops", self.values["x"]), "full")
        elif m in ("zC", "line_zC"):
            if m == "line_zC":
                out["line"] = item(line, "line")
            out["dest"] = item(self._record("destinations4", self.values["zC"]), "dest")
        return out

    def status(self) -> dict:
        return {**super().status(), "mode": self.mode}

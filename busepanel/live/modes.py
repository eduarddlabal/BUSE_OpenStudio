"""Live use of a panel: clock, home departure board, custom texts.

The BS120 shows only texts stored in its FLASH; they are selected by number over IBIS.
So the live modes *pre-generate* the texts into the project (strategy A) and then a runner
sends the right numbers. Telegram templates are configurable because different firmwares
use different telegrams (l/z/zC/x).

Channels:
  "dest"     destinations "000"–"999", telegram  z{key}
  "dest4"    destinations4 "0000"–"9999", telegram zC{key}   (needs command zC enabled)
  "stops"    stops "0000"–"9999", telegram x{key}            (needs command x)
  "split"    clock only: hours as line l0HH, minutes as destination z0MM
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import time

CHANNELS = {
    "dest": ("destinations", 3, "z{key}"),
    "dest4": ("destinations4", 4, "zC{key}"),
    "stops": ("stops", 4, "x{key}"),
}
REQUIRED_CMD = {"dest": "z", "dest4": "zC", "stops": "x"}
# firmware: x9999 switches the BS120 back to line + destination (from the stop-only mode)
NORMAL_MODE = "x9999"


def _ensure_cmd(project, cmd):
    cfg = project.data.setdefault("config", {})
    cmds = list(cfg.get("commands") or [])
    for c in (cmd, "l", "z", "aV"):
        if c not in cmds:
            cmds.append(c)
    cfg["commands"] = cmds


def _style(text, style):
    item = {"text": text}
    item.update({k: v for k, v in (style or {}).items() if v not in (None, False, "")})
    return item if len(item) > 1 else text



SWITCHES = {"x9999", "x9998", "x9997", "x9996", "x9995", "x9994", "x9993", "x9992", "x9991", "x9990",
            "x9989", "x9988", "x9987", "x9986"}


def order_for_panel(tel: list, current_mode: str | None) -> tuple[list, str | None]:
    """Reorder telegrams for the real BS120 (verified behaviour):
    * after a mode switch the panel shows the previously stored text, a *different* new v then redraws
      -> send the switch first and the text after it;
    * re-sending the switch of the current mode breaks updates -> omit it when the mode is unchanged.
    Returns (telegrams, new_mode)."""
    switch = next((t for t in tel if isinstance(t, str) and t in SWITCHES), None)
    rest = [t for t in tel if t is not switch]
    if switch is None:
        return rest, current_mode
    if switch == current_mode:
        return rest, current_mode
    pre = [t for t in rest if isinstance(t, str)]           # l / z numbers first
    texts = [t for t in rest if not isinstance(t, str)]     # v … after the switch
    return pre + [switch] + texts, switch


# ------------------------------------------------------------------ free text (verified on BS120)
# v<text> stores a text in the panel; it is shown after the mode switch x9996 (text only)
# or x9997 (line + text). zI<p><text> + x9994 (line + zI) and zM<p><text> + x9990 come from
# the firmware analysis and are not verified yet.
TEXT_MODES = {
    "v": ("x9996", "v"),            # text only            – verified
    "v+line": ("x9997", "v"),       # line number + text
    "zI": ("x9994", "zI0"),         # line + zI text
    "zM": ("x9990", "zM0"),         # extraordinary information text
}


def text_payload(fonts, text: str, font: int | None = None, spacing: int | None = None) -> tuple[bytes, list]:
    """Text -> panel codes (via the project's code table), optional font/spacing prefix."""
    from ..core.text import encode_markup
    codes, problems = encode_markup(text, fonts, default_font=font if font is not None else 3, cr=False)
    prefix = bytes([0xE0 + font] if font is not None else []) + bytes([0xB0 + spacing] if spacing is not None else [])
    return prefix + codes, problems


def text_telegrams(fonts, text: str, kind: str = "v", font=None, spacing=None) -> list:
    switch, cmd = TEXT_MODES[kind]
    payload, _ = text_payload(fonts, text, font, spacing)
    return [cmd.encode() + payload, switch]


def departure_text(d, now: dt.datetime, fmt: str) -> str:
    mins = d["time"] - (now.hour * 60 + now.minute)
    t = f"{d['time'] // 60:02d}:{d['time'] % 60:02d}"
    za = "teď" if mins <= 0 else (f"za {mins} min" if mins < 60 else t)
    return fmt.format(time=t, dest=d["dest"], line=d["line"], min=mins, za=za)


def departures_text_runner(panel, fonts, schedule, fmt="{line}  {dest}  {za}", font=None, spacing=None, log=None,
                           empty="Dnes už nic nejede", line_field=False):
    """Free-text departure board (verified mechanism v + x9996).

    line_field=True shows the line number in the panel's own line field instead (l<key> + v + x9997);
    that only works when the panel database contains the line (LIN record).
    """
    kind = "v+line" if line_field else "v"

    def compute(now):
        i, d = next_departure(schedule, now)
        if i is None:
            return (["l000"] if line_field else []) + text_telegrams(fonts, empty, kind, font, spacing)
        text = departure_text(d, now, fmt)
        pre = [f"l{line_key(d['line']) or '000'}"] if line_field else []
        return pre + text_telegrams(fonts, text, kind, font, spacing)
    return Runner(panel, compute, 5.0, log)


def text_clock_runner(panel, fonts, fmt="%H:%M", kind="v", font=None, spacing=None, log=None):
    def compute(now):
        return text_telegrams(fonts, now.strftime(fmt), kind, font, spacing)
    return Runner(panel, compute, 1.0, log)


# ------------------------------------------------------------------ clock
def clock_texts(fmt="%H:%M"):
    for m in range(24 * 60):
        t = dt.datetime(2000, 1, 1, m // 60, m % 60)
        yield m // 60, m % 60, t.strftime(fmt)


def add_clock(project, channel="stops", fmt="%H:%M", style=None, offset=0):
    """Add clock texts. `offset` = first key number for 'dest'/'stops' channels."""
    style = style or {"font": 3, "spacing": 2}
    if channel == "split":
        lines = project.data.setdefault("lines", {})
        dests = project.data.setdefault("destinations", {})
        for h in range(24):
            lines[f"{h:03d}"] = _style(f"{h:02d}", style)
        for m in range(60):
            dests[f"{m:03d}"] = _style(f":{m:02d}", style)
        _ensure_cmd(project, "z")
        return {"channel": "split"}
    sec, klen, _tpl = CHANNELS[channel]
    if channel == "dest":
        raise ValueError("1440 minut se do 3místných cílů (000–999) nevejde – použijte dest4, stops nebo split")
    recs = project.data.setdefault(sec, {})
    for h, m, txt in clock_texts(fmt):
        recs[f"{offset + h * 100 + m:0{klen}d}"] = _style(txt, style)
    project.data[sec] = dict(sorted(recs.items()))
    _ensure_cmd(project, REQUIRED_CMD[channel])
    return {"channel": channel}


def clock_telegrams(now: dt.datetime, channel="stops", offset=0):
    if channel == "split":
        return [f"l{now.hour:03d}", f"z{now.minute:03d}"]
    _sec, klen, tpl = CHANNELS[channel]
    return [tpl.format(key=f"{offset + now.hour * 100 + now.minute:0{klen}d}")]


# ------------------------------------------------------------------ departures
def read_schedule(data: str):
    """CSV: time;line;destination[;days]  (days e.g. 'Po-Pá', 'So,Ne' – optional)"""
    rows = []
    try:
        dialect = csv.Sniffer().sniff(data[:2000], delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
    for r in csv.DictReader(io.StringIO(data), dialect=dialect):
        r = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
        t = r.get("time") or r.get("cas") or r.get("čas") or r.get("odjezd")
        if not t:
            continue
        hh, mm = (int(x) for x in t.split(":")[:2])
        rows.append({"time": hh * 60 + mm, "line": r.get("line") or r.get("linka") or "",
                     "dest": r.get("destination") or r.get("cil") or r.get("cíl") or "",
                     "days": r.get("days") or r.get("dny") or ""})
    return sorted(rows, key=lambda x: x["time"])


DAYS = ["po", "út", "st", "čt", "pá", "so", "ne"]


def runs_today(days: str, date: dt.date) -> bool:
    if not days:
        return True
    wd = date.weekday()
    for part in days.lower().replace(" ", "").split(","):
        part = part.replace("ut", "út").replace("ct", "čt").replace("pa", "pá")
        if "-" in part:
            a, b = part.split("-")
            if a in DAYS and b in DAYS and DAYS.index(a) <= wd <= DAYS.index(b):
                return True
        elif part in DAYS and DAYS.index(part) == wd:
            return True
    return False


def add_departures(project, schedule, channel="dest", fmt="{time} {dest}", style=None, offset=500):
    """One destination per departure (text incl. time), line numbers as lines."""
    style = style or {"font": 3, "spacing": 1}
    sec, klen, _ = CHANNELS[channel]
    recs = project.data.setdefault(sec, {})
    lines = project.data.setdefault("lines", {})
    keys = []
    for i, d in enumerate(schedule):
        key = f"{offset + i:0{klen}d}"
        t = f"{d['time'] // 60:02d}:{d['time'] % 60:02d}"
        recs[key] = _style(fmt.format(time=t, dest=d["dest"], line=d["line"]), style)
        keys.append(key)
        if d["line"].isdigit() and len(d["line"]) <= 3:
            lines.setdefault(f"{int(d['line']) * 10 if int(d['line']) < 100 else int(d['line']):03d}",
                             _style(d["line"], {"font": 3, "spacing": 2}))
    project.data[sec] = dict(sorted(recs.items()))
    _ensure_cmd(project, REQUIRED_CMD[channel])
    return keys


def next_departure(schedule, now: dt.datetime):
    m = now.hour * 60 + now.minute
    for i, d in enumerate(schedule):
        if d["time"] >= m and runs_today(d["days"], now.date()):
            return i, d
    return None, None


def line_key(line: str) -> str | None:
    if line.isdigit() and len(line) <= 3:
        n = int(line)
        return f"{n * 10 if n < 100 else n:03d}"
    return None


# ------------------------------------------------------------------ runner
class Runner:
    """Calls `compute(now)` every `period` s and sends telegrams when they change."""

    def __init__(self, panel, compute, period=1.0, log=None):
        self.panel, self.compute, self.period = panel, compute, period
        self.log = log or (lambda m: None)
        self.last = None
        self.running = False

    def step(self, now=None):
        tel = self.compute(now or dt.datetime.now())
        if tel and tel != self.last:
            out, self.mode = order_for_panel(tel, getattr(self, "mode", None))
            self.panel.send(*out)
            self.log(" ".join(t if isinstance(t, str) else t.decode("cp1250", "replace") for t in tel))
            self.last = tel
        return tel

    def run(self, stop=None):
        self.running = True
        while self.running and not (stop and stop()):
            try:
                self.step()
            except Exception as e:      # noqa: BLE001 – keep running, report
                self.log(f"chyba: {e}")
            time.sleep(self.period)

    def stop(self):
        self.running = False


def clock_runner(panel, channel="stops", offset=0, log=None):
    return Runner(panel, lambda now: clock_telegrams(now, channel, offset), 1.0, log)


def departures_runner(panel, schedule, keys, channel="dest", log=None):
    tpl = CHANNELS[channel][2]

    def compute(now):
        i, d = next_departure(schedule, now)
        if i is None:
            return ["l000", tpl.format(key="0" * CHANNELS[channel][1])]
        lk = line_key(d["line"]) or "000"
        return [NORMAL_MODE, f"l{lk}", tpl.format(key=keys[i])]
    return Runner(panel, compute, 5.0, log)


def playlist_runner(panel, keys, interval=10.0, channel="dest", log=None):
    tpl = CHANNELS[channel][2]

    def compute(now):
        i = int(now.timestamp() // interval) % len(keys)
        return ([NORMAL_MODE] if channel == "dest" else []) + [tpl.format(key=keys[i])]
    return Runner(panel, compute, 1.0, log)

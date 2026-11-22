"""Panel outputs – where telegrams go (serial port, ESP over WebSocket, virtual emulator)."""
from __future__ import annotations


def tel_text(t, fonts=None) -> str:
    """Readable form of a telegram; bytes in panel codes are decoded with the project's code table."""
    if isinstance(t, str):
        return t
    if fonts is not None:
        from ..core.text import decode_markup
        try:
            return decode_markup(t, fonts).replace("{nocr}", "")
        except Exception:      # noqa: BLE001
            pass
    return t.decode("cp1250", "replace")


class Output:
    kind = "base"

    def __init__(self, name: str):
        self.name = name
        self.last_error: str | None = None
        self.sent = 0
        self.fonts = None          # set by the owner -> readable logs

    def send(self, *telegrams):
        raise NotImplementedError

    def status(self) -> dict:
        return {"name": self.name, "kind": self.kind, "sent": self.sent, "error": self.last_error}

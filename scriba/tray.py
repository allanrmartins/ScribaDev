"""Ícone de bandeja do ScribaDev (pystray)."""

from __future__ import annotations

import threading

import pystray
from PIL import Image, ImageDraw

from . import autostart, util


def _icon_image(recording: bool) -> Image.Image:
    """Ícone do app (pergaminho + raio). Cai para um círculo desenhado se o asset sumir."""
    png = util.ICON_REC_PNG if recording else util.ICON_PNG
    try:
        return Image.open(png).convert("RGBA")
    except Exception:
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse([4, 4, 60, 60], fill=(30, 30, 40, 255), outline=(90, 90, 110, 255), width=2)
        d.ellipse([22, 22, 42, 42], fill=(229, 69, 69, 255) if recording else (240, 240, 245, 255))
        return img


class Tray:
    def __init__(self, app):
        self.app = app
        self.icon = pystray.Icon(
            "scriba",
            _icon_image(False),
            "ScribaDev — aguardando call do Teams",
            menu=pystray.Menu(
                pystray.MenuItem(lambda item: self.app.status_text(), None, enabled=False),
                pystray.Menu.SEPARATOR,
                # default=True: é o que roda no duplo clique do ícone
                pystray.MenuItem("Abrir ScribaDev", self._open_main, default=True),
                pystray.MenuItem("Notas", self._open_notes_win),
                pystray.MenuItem("Configurações", self._open_settings),
                pystray.MenuItem(
                    lambda item: "Parar gravação" if self.app.is_recording() else "Gravar agora",
                    self._toggle_recording,
                ),
                pystray.MenuItem("Abrir pasta de reuniões", self._open_meetings),
                pystray.MenuItem("Abrir pasta de notas", self._open_notes),
                pystray.MenuItem("Processar pendentes", self._process_pending),
                pystray.MenuItem(
                    "Iniciar com o Windows",
                    self._toggle_autostart,
                    checked=lambda item: autostart.is_enabled(),
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Sair", self._quit),
            ),
        )

    # ações rodam na thread do pystray — sempre delegar para o app em thread própria
    def _bg(self, fn, *args):
        threading.Thread(target=fn, args=args, daemon=True).start()

    def _open_main(self, icon, item):
        self.app.show_main()

    def _open_settings(self, icon, item):
        self.app.show_settings()

    def _open_notes_win(self, icon, item):
        self.app.show_notes()

    def _toggle_recording(self, icon, item):
        if self.app.is_recording():
            self._bg(self.app.stop_recording, False)
        else:
            self._bg(self.app.start_recording, "manual")

    def _open_meetings(self, icon, item):
        util.open_path(self.app.cfg.output.resolved_recordings_dir())

    def _open_notes(self, icon, item):
        d = self.app.cfg.output.resolved_export_dir()
        d.mkdir(parents=True, exist_ok=True)
        util.open_path(d)

    def _process_pending(self, icon, item):
        self._bg(self.app.scan_pending)

    def _toggle_autostart(self, icon, item):
        self._bg(autostart.set_autostart, not autostart.is_enabled())

    def _quit(self, icon, item):
        self.app.request_quit()

    def start(self):
        self.icon.run_detached()

    def stop(self):
        try:
            self.icon.stop()
        except Exception:
            pass

    def set_recording(self, recording: bool, title: str):
        try:
            self.icon.icon = _icon_image(recording)
            self.icon.title = title
        except Exception:
            pass

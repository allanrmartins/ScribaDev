"""Ícone de bandeja do ScribaDev (pystray)."""

from __future__ import annotations

import threading

import pystray
from PIL import Image, ImageDraw

from . import autostart, util


def _icon_image(recording: bool, dim: bool = False) -> Image.Image:
    """Ícone do app (pergaminho + raio). Cai para um círculo desenhado se o asset sumir.

    `dim` (só com recording): variante APAGADA do ícone REC — alternada com a normal a
    cada tick faz a bandeja PULSAR durante a gravação (#14)."""
    png = util.ICON_REC_PNG if recording else util.ICON_PNG
    try:
        img = Image.open(png).convert("RGBA")
    except Exception:
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse([4, 4, 60, 60], fill=(30, 30, 40, 255), outline=(90, 90, 110, 255), width=2)
        d.ellipse([22, 22, 42, 42], fill=(229, 69, 69, 255) if recording else (240, 240, 245, 255))
    if recording and dim:  # pulso "apagado": reduz o alpha p/ o ícone esmaecer e voltar
        img.putalpha(img.getchannel("A").point(lambda a: int(a * 0.4)))
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
                pystray.MenuItem("Log", self._open_log_win),
                pystray.MenuItem("Configurações", self._open_settings),
                pystray.MenuItem("Gravar agora", self._start_recording,
                                 visible=lambda item: not self.app.is_recording()),
                pystray.MenuItem("Nova call (dividir gravação)", self._split_recording,
                                 visible=lambda item: self.app.is_recording()),
                pystray.MenuItem("Parar gravação", self._stop_recording,
                                 visible=lambda item: self.app.is_recording()),
                pystray.MenuItem("Descartar gravação", self._discard_recording,
                                 visible=lambda item: self.app.is_recording()),
                pystray.MenuItem("Participantes", self._speakers_menu(),
                                 visible=lambda item: self.app.is_recording()),
                pystray.MenuItem("Abrir pasta de reuniões", self._open_meetings),
                pystray.MenuItem("Abrir pasta de notas", self._open_notes),
                pystray.MenuItem("Processar pendentes", self._process_pending),
                pystray.MenuItem("Buscar atualizações", self._check_updates),
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

    def _open_log_win(self, icon, item):
        self.app.show_log()

    def _start_recording(self, icon, item):
        self._bg(self.app.start_recording, "manual")

    def _split_recording(self, icon, item):
        self._bg(self.app._split_now)  # #38: fecha esta call e começa outra

    def _stop_recording(self, icon, item):
        self._bg(self.app.stop_recording, False, True)  # keep=True: intenção explícita

    def _discard_recording(self, icon, item):
        # DESTRUTIVO: confirma na thread do Tk (a bandeja roda noutra thread) antes de perder o áudio.
        self.app.ui(self._confirm_discard)

    def _confirm_discard(self):
        from tkinter import messagebox

        kw = {"icon": "warning", "default": "no"}
        if getattr(self.app, "root", None) is not None:
            kw["parent"] = self.app.root
        try:
            ok = messagebox.askyesno(
                "Descartar gravação?",
                "A gravação em andamento será descartada e o áudio, perdido.\n\nDescartar mesmo assim?",
                **kw,
            )
        except Exception:
            ok = True  # sem UI disponível: não trava o descarte (comportamento antigo)
        if ok:
            self._bg(self.app.stop_recording, True)  # discard=True

    def _speakers_menu(self) -> "pystray.Menu":
        """Submenu 'Participantes' (1–8): define o nº na gravação ativa (#13/#14).
        Ação (icon,item) e checked (item) fecham sobre n — pystray só aceita
        callables com argcount <= 2."""
        def mk(n: int):
            def act(icon, item):
                self._bg(self.app._set_speakers_live, n)

            def chk(item):
                return self.app.current_speakers() == n

            return pystray.MenuItem(str(n), act, checked=chk, radio=True)

        return pystray.Menu(*[mk(n) for n in range(1, 9)])

    def _open_meetings(self, icon, item):
        util.open_path(self.app.cfg.output.resolved_recordings_dir())

    def _open_notes(self, icon, item):
        d = self.app.cfg.output.resolved_export_dir()
        d.mkdir(parents=True, exist_ok=True)
        util.open_path(d)

    def _process_pending(self, icon, item):
        self._bg(self.app.scan_pending)

    def _check_updates(self, icon, item):
        self._bg(self.app.check_updates_now)

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

    def set_recording(self, recording: bool, title: str, dim: bool = False):
        try:
            self.icon.icon = _icon_image(recording, dim=dim)
            self.icon.title = title
        except Exception:
            pass

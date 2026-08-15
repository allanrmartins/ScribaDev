"""Notificações do app: toasts nativos no Windows (com botão "Abrir notas");
no-op logado no POSIX até os marcos de notificação nativa do épico #104 (#101)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from . import util

log = logging.getLogger("scriba.notify")

AUMID = util.APP_AUMID
_DISPLAY = "ScribaDev"


def _register_aumid() -> None:
    """Registra o AUMID em HKCU (sem admin) — dá nome e ícone próprios aos toasts
    e permite que cliques a partir da Central de Ações cheguem ao app."""
    # lazy: winreg só existe no Windows; o import no topo quebrava o módulo em
    # Linux/macOS (#96) — este caminho só roda dentro do Notifier (Windows-only)
    import winreg

    key_path = rf"SOFTWARE\Classes\AppUserModelId\{AUMID}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as k:
        winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, _DISPLAY)
        if util.ICON_PNG.exists():
            winreg.SetValueEx(k, "IconUri", 0, winreg.REG_SZ, str(util.ICON_PNG))


class _WindowsNotifier:
    def __init__(self):
        from . import util

        util.preload_msvc_runtime()  # antes do winrt carregar o runtime velho dele
        from windows_toasts import InteractableWindowsToaster

        _register_aumid()
        self.toaster = InteractableWindowsToaster(_DISPLAY, notifierAUMID=AUMID)

    def _toast(self, lines: list[str], open_path: Path | None = None, button: str = "Abrir"):
        from windows_toasts import Toast, ToastButton

        t = Toast()
        t.text_fields = lines
        if open_path is not None:
            t.AddAction(ToastButton(button, "open"))

            def _on_activated(_args):
                from . import util

                try:
                    util.open_path(open_path)
                except Exception:
                    pass

            t.on_activated = _on_activated
        self.toaster.show_toast(t)

    def info(self, title: str, body: str = "") -> None:
        self._toast([title, body] if body else [title])

    def notes_ready(self, md_path: Path) -> None:
        self._toast(["Notas prontas", md_path.name], open_path=md_path, button="Abrir notas")

    def test(self) -> None:
        from . import util

        self._toast(["ScribaDev", "Notificação de teste — clique para abrir a pasta do app"], open_path=util.APP_DIR)


class _MacNotifier:
    """macOS (#104, M6): notificação nativa via `osascript` (zero dependências).

    Limitações aceitas até existir um bundle .app (M8 → UNUserNotificationCenter):
    sem botão "Abrir notas" (o AppleScript não tem ação de clique) — o caminho vai
    p/ o log; a notificação sai atribuída ao "Editor de Scripts" e o usuário pode
    precisar habilitá-la em Ajustes → Notificações na primeira vez (o doctor avisa).
    """

    @staticmethod
    def _esc(s: str) -> str:
        # injeção de AppleScript: escapar \ e " antes de interpolar no literal
        return str(s).replace("\\", "\\\\").replace('"', '\\"')

    def _display(self, title: str, body: str = "") -> None:
        import subprocess

        script = f'display notification "{self._esc(body)}" with title "{self._esc(title)}"'
        try:
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
        except Exception:
            log.exception("notificação (osascript) falhou")

    def info(self, title: str, body: str = "") -> None:
        self._display(title, body)

    def notes_ready(self, md_path: Path) -> None:
        self._display("Notas prontas", md_path.name)
        log.info("notas prontas: %s", md_path)

    def test(self) -> None:
        self._display("ScribaDev", "Notificação de teste")


class _NoopNotifier:
    """POSIX (Marco 1 do #104): notificações nativas ainda não existem — loga e segue.

    Mesma interface do notifier do Windows; o marco Linux troca por notify-send."""

    def info(self, title: str, body: str = "") -> None:
        log.info("notificação (no-op neste SO): %s — %s", title, body)

    def notes_ready(self, md_path: Path) -> None:
        log.info("notificação (no-op neste SO): notas prontas — %s", md_path)

    def test(self) -> None:
        log.info("notificação de teste: toasts nativos não suportados neste SO ainda")


# a classe pública escolhe o backend pelo SO (mesmo padrão da camada plat; #101)
if sys.platform == "win32":
    Notifier = _WindowsNotifier
elif sys.platform == "darwin":
    Notifier = _MacNotifier
else:
    Notifier = _NoopNotifier

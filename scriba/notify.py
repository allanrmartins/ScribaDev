"""Notificações toast do Windows (com botão "Abrir notas")."""

from __future__ import annotations

from pathlib import Path

from . import util

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


class Notifier:
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

"""Orquestração do ScribaDev: detector → gravador → pipeline → notificações/UI.

Modelo de threads:
- main thread: Tk root oculto (pílula) drenando uma fila de comandos de UI;
- pystray: run_detached;
- detector: thread vigiando o registro;
- worker: thread processando pastas gravadas (transcrição + notas), em fila —
  uma call nova pode ser gravada enquanto a anterior ainda transcreve.
"""

from __future__ import annotations

import ctypes
import logging
import logging.handlers
import os
import queue
import sys
import threading
import time
import tkinter as tk

from . import util
from .config import load
from .detector import Detector
from .recorder import Recording

log = logging.getLogger("scriba")

_mutex_handle = None  # mantém o handle vivo durante o processo


def _single_instance() -> bool:
    global _mutex_handle
    _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\ScribaDevSingleInstance")
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


# Evento nomeado que a 2ª instância usa para pedir "mostre a janela" à 1ª —
# sem isso, clicar no atalho com o app já na bandeja não fazia nada visível.
_SHOW_EVENT_NAME = "Local\\ScribaDevShowWindow"


def _signal_show_window() -> bool:
    """Acorda a instância que já está rodando (True se conseguiu sinalizar)."""
    EVENT_MODIFY_STATE = 0x0002
    k32 = ctypes.windll.kernel32
    handle = k32.OpenEventW(EVENT_MODIFY_STATE, False, _SHOW_EVENT_NAME)
    if not handle:
        return False
    k32.SetEvent(handle)
    k32.CloseHandle(handle)
    return True


def _setup_logging() -> None:
    util.ensure_app_dirs()
    root = logging.getLogger()
    if root.handlers:
        return
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%d/%m/%Y %H:%M:%S"
    )
    # rotação DIÁRIA (à meia-noite): cada arquivo = 1 dia, então o .log não cresce sem
    # fim; mantém ~14 dias (scriba.log de hoje + scriba.log.AAAA-MM-DD dos anteriores)
    fh = logging.handlers.TimedRotatingFileHandler(
        util.LOGS_DIR / "scriba.log", when="midnight", backupCount=14, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr is not None:  # ausente no scribadev-tray.exe (gui)
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
    root.setLevel(logging.INFO)


class ScribaApp:
    def __init__(self, start_hidden: bool = True):
        # start_hidden=True: inicia só na bandeja (autostart). False: abre a janela
        # na frente (lançamento manual pelo atalho), como qualquer app.
        self.start_hidden = start_hidden
        self.cfg = load()
        self.stop_event = threading.Event()
        self.ui_queue: queue.Queue = queue.Queue()
        self.jobs: queue.Queue = queue.Queue()
        self.rec: Recording | None = None
        self.rec_source = "auto"
        self.rec_lock = threading.Lock()
        self.call_active = False  # call do Teams em andamento (a pílula segue a call)
        self.root: tk.Tk | None = None
        self.pill = None
        self.tray = None
        self.settings = None
        self.notes_win = None
        self.wizard_win = None
        self.log_win = None
        self.main_win = None
        self.update_news = None  # versão nova detectada (#19); a capa exibe o aviso
        self.hotkey = None
        self.detector: Detector | None = None
        self.call_started_at: float | None = None
        try:
            from .notify import Notifier

            self.notifier = Notifier()
        except Exception:
            log.exception("toasts indisponíveis")
            self.notifier = None

    # ------------------------------------------------------------- helpers --

    def _toast(self, title: str, body: str = "") -> None:
        if self.notifier:
            try:
                self.notifier.info(title, body)
            except Exception:
                log.exception("toast falhou")

    def ui(self, fn) -> None:
        """Agenda um callable para rodar na thread do Tk."""
        self.ui_queue.put(fn)

    def is_recording(self) -> bool:
        with self.rec_lock:
            return self.rec is not None

    def detection_label(self) -> str:
        """"Teams, Zoom, Meet, Webex" — o que está sendo monitorado (desktop e web)."""
        from .detector import browser_patterns_from, friendly_name, patterns_from, title_patterns_from

        names = [friendly_name(p) for p in patterns_from(self.cfg.detection)]
        if browser_patterns_from(self.cfg.detection):
            web = title_patterns_from(self.cfg.detection)
            names += [friendly_name(p) for p in web] or ["navegador"]
        return ", ".join(dict.fromkeys(names))

    def current_call_app(self) -> str | None:
        return self.detector.current_app if self.detector else None

    def status_text(self) -> str:
        if self.is_recording():
            s = int(self.recording_duration())
            t = f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"
            return f"ScribaDev — gravando {t} ({self.current_call_app() or 'manual'})"
        return f"ScribaDev — monitorando {self.detection_label()}"

    def current_speakers(self) -> int | None:
        """Nº de participantes já definido na gravação ATIVA (pílula/bandeja, #13/#14)."""
        import json

        with self.rec_lock:
            rec = self.rec
        if rec is None:
            return None
        try:
            return json.loads((rec.folder / "meta.json").read_text(encoding="utf-8")).get("num_speakers")
        except Exception:
            return None

    def _tick_tray(self) -> None:
        """Atualiza o tooltip da bandeja com o tempo de gravação ao vivo e PULSA o ícone
        REC (#14). A bandeja é o indicador confiável (não some como a pílula)."""
        if self.tray and self.is_recording():
            self._tray_pulse = not getattr(self, "_tray_pulse", False)
            self.tray.set_recording(True, self.status_text(), dim=self._tray_pulse)
            if self.root is not None and not self.stop_event.is_set():
                self.root.after(700, self._tick_tray)  # ritmo de pulso (~como a pílula)

    # --------------------------------------------------------------- call --

    def _on_call_started(self, probe: bool = True) -> None:
        self.call_active = True
        self.call_started_at = time.monotonic()
        if self.cfg.detection.auto_record:
            self.start_recording("auto", probe=probe)
        else:
            self.ui(self._show_pill_idle)

    def _on_call_ended(self) -> None:
        self.call_active = False
        self.call_started_at = None
        if self.is_recording():
            self.stop_recording()
        else:
            self.ui(self._hide_pill)

    def call_duration(self) -> float:
        return time.monotonic() - self.call_started_at if self.call_started_at else 0.0

    def recording_duration(self) -> float:
        with self.rec_lock:
            return self.rec.duration_seconds() if self.rec else 0.0

    # ----------------------------------------------------------- gravação --

    def start_recording(self, source: str = "auto", probe: bool = True) -> None:
        # pré-checagem em subprocesso: se o PortAudio estiver num estado que
        # aborta (assert de troca de dispositivo), morre a sonda — não o app.
        # probe=False no split de calls consecutivas (#34): os dispositivos
        # funcionavam há < poll_seconds e a sonda pode custar segundos que viram
        # buraco no início da call 2. Se Recording() falhar, o try/except cobre.
        if probe and util.run_audio_probe() is None:
            log.error("dispositivos de áudio indisponíveis — gravação não iniciada (%s)", source)
            self._toast("ScribaDev: áudio indisponível", "Não consegui acessar microfone/loopback agora.")
            return
        with self.rec_lock:
            if self.rec is not None:
                return
            try:
                rec = Recording(self.cfg)
            except Exception as e:
                log.exception("falha ao iniciar gravação")
                self._toast("ScribaDev: erro ao iniciar gravação", str(e))
                return
            self.rec = rec
            self.rec_source = source
        log.info("gravação iniciada (%s): %s", source, rec.folder.name)
        self._toast("Gravando reunião", "O áudio está sendo capturado localmente.")
        if self.tray:
            self.tray.set_recording(True, self.status_text())
            self.ui(self._tick_tray)  # #14: tooltip da bandeja com o tempo ao vivo
        self.ui(self._show_pill)
        # O modelo só é carregado quando a gravação terminar (em stop_recording):
        # nada de segurar a VRAM da GPU durante a call inteira.

    def stop_recording(self, discard: bool = False, keep: bool = False) -> None:
        """Encerra a gravação atual.

        discard: joga fora (botão × da pílula).
        keep: mantém mesmo abaixo da duração mínima (botão ■ — intenção explícita).
        """
        with self.rec_lock:
            rec, source = self.rec, self.rec_source
            self.rec = None
        if rec is None:
            return
        if self.tray:
            self.tray.set_recording(False, self.status_text())

        duration = rec.duration_seconds()
        if discard:
            rec.stop(status="discarded")
            log.info("gravação descartada: %s", rec.folder.name)
            self._toast("Gravação descartada", rec.folder.name)
            self.ui(self._show_pill_idle if self.call_active else self._hide_pill)
            return
        min_secs = self.cfg.detection.min_call_seconds
        if source == "auto" and not keep and duration < min_secs:
            rec.stop(status="too_short")
            log.info("gravação muito curta (%.0fs), ignorada: %s", duration, rec.folder.name)
            self._toast(
                f"Gravação de {duration:.0f}s ignorada",
                f"Mínimo configurado: {min_secs:.0f}s. Ajuste em Configurações (duplo clique no ícone).",
            )
            self.ui(self._show_pill_idle if self.call_active else self._hide_pill)
            return
        rec.stop(status="recorded")
        log.info("gravação encerrada (%.0fs): %s", duration, rec.folder.name)
        self._toast("Transcrevendo...", f"{rec.folder.name} ({max(1, int(duration // 60))} min)")
        # com a call ainda ativa, a pílula espera um novo ⏺; senão, ela fica
        # visível mostrando o progresso do processamento
        if self.call_active:
            self.ui(self._show_pill_idle)
        else:
            self.ui(lambda: self._pill_processing("recorded"))
        # enfileira na fila serial (o worker processa uma de cada vez): uma call
        # nova pode ser gravada enquanto a anterior transcreve, sem dois Whisper
        # disputando a GPU — era o que travava em calls seguidas. Com diarização +
        # ask_speakers, _enqueue_meeting pergunta antes o nº de participantes.
        self._enqueue_meeting(rec.folder)

    def _enqueue_meeting(self, folder) -> None:
        """Põe a reunião na fila de processamento.

        Com diarização + ask_speakers ligados, abre antes a janela de nº de
        participantes (na thread de UI, não-bloqueante): o job só entra na fila
        quando o usuário responde, escolhe automático ou o timeout esgota — então
        só ESTA reunião espera, e nunca para sempre.
        """
        dz = self.cfg.diarization
        # se o nº de participantes já foi definido na pílula durante a call (#13),
        # dispensa a janela do fim e enfileira direto.
        if dz.enabled and dz.ask_speakers and not self._meta_has_speakers(folder):
            self.ui(lambda: self._ask_speakers_then_enqueue(folder))
        else:
            self.jobs.put(folder)

    def _meta_has_speakers(self, folder) -> bool:
        """True se o meta da gravação já tem num_speakers (definido na pílula, #13)."""
        import json
        from pathlib import Path

        try:
            meta = json.loads((Path(folder) / "meta.json").read_text(encoding="utf-8"))
            return bool(meta.get("num_speakers"))
        except Exception:
            return False

    def _ask_speakers_then_enqueue(self, folder) -> None:
        """(thread de UI) Abre a janela e enfileira a reunião quando ela resolver."""
        from .speakers_ui import ask_num_speakers

        def done(n: int | None) -> None:
            if n is not None:
                self._save_num_speakers(folder, n)
                util.update_state(last_num_speakers=int(n))
            self.jobs.put(folder)

        try:
            last = util.read_state().get("last_num_speakers")
            ask_num_speakers(
                self.root, done,
                last_value=last if isinstance(last, int) else None,
                timeout_seconds=int(self.cfg.diarization.ask_speakers_timeout or 0),
            )
        except Exception:
            log.exception("janela de nº de participantes falhou — enfileirando direto")
            self.jobs.put(folder)

    def _save_num_speakers(self, folder, n: int) -> None:
        """Grava num_speakers no meta.json (o subprocesso de processamento o lê)."""
        import json
        from pathlib import Path

        meta_path = Path(folder) / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["num_speakers"] = int(n)
            util.atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))
            log.info("nº de participantes informado (%d): %s", n, getattr(folder, "name", folder))
        except Exception:
            log.exception("não consegui gravar num_speakers no meta de %s", getattr(folder, "name", folder))

    def _set_speakers_live(self, n: int) -> None:
        """Pílula definiu o nº de participantes durante a call (#13): grava no meta da
        gravação ATIVA. O fim da call lê isso e dispensa a janela de perguntar."""
        with self.rec_lock:
            rec = self.rec
        if rec is not None:
            self._save_num_speakers(rec.folder, n)
            util.update_state(last_num_speakers=int(n))

    # ------------------------------------------------------------- janelas --

    def show_main(self) -> None:
        """Mostra a janela principal (chamável de qualquer thread)."""
        self.ui(lambda: self.main_win.show() if self.main_win else None)

    def show_settings(self) -> None:
        """Abre a janela de Configurações (chamável de qualquer thread)."""
        self.ui(self._open_settings_ui)

    def _open_settings_ui(self) -> None:
        if self.settings is None:
            from .settings_ui import SettingsWindow

            self.settings = SettingsWindow(self.root, self)
        self.settings.show()

    def show_notes(self) -> None:
        """Abre a janela de Notas (chamável de qualquer thread)."""
        self.ui(self._open_notes_ui)

    def _open_notes_ui(self) -> None:
        if self.notes_win is None:
            from .notes_ui import NotesWindow

            self.notes_win = NotesWindow(self.root, self)
        self.notes_win.show()

    def show_log(self) -> None:
        """Abre a janela de Log/diagnóstico (chamável de qualquer thread)."""
        self.ui(self._open_log_ui)

    def _open_log_ui(self) -> None:
        if self.log_win is None:
            from .log_ui import LogWindow

            self.log_win = LogWindow(self.root, self)
        self.log_win.show()

    # -- exceções não tratadas (#22) -------------------------------------------

    def _on_unhandled(self, exc_info, where: str) -> None:
        """Handler único dos 3 excepthooks: SEMPRE loga e, se a UI já existe, mostra um
        diálogo amigável (sem console, uma exceção sumia calada). Crash cedo demais
        (root ainda None) ou já encerrando → fica só no log."""
        log.error("exceção não tratada (%s)", where, exc_info=exc_info)
        if self.root is None or self.stop_event.is_set():
            return
        import traceback as _tb

        detail = f"[{where}]\n\n" + "".join(_tb.format_exception(*exc_info))
        try:
            self.ui(lambda: self._show_crash(detail))
        except Exception:
            pass  # nunca deixar o handler de erro levantar outro erro

    def _show_crash(self, detail: str) -> None:
        try:
            from .log_ui import show_crash_dialog

            show_crash_dialog(self, detail)
        except Exception:
            log.exception("falha ao exibir o diálogo de crash")

    # -- atualização in-app (#19) ----------------------------------------------

    def _check_updates_boot(self) -> None:
        """Checagem de versão a CADA inicialização do app (#19)."""
        threading.Thread(target=self._update_check_worker, args=(False,), daemon=True, name="updchk").start()

    def check_updates_now(self) -> None:
        """Checagem manual (bandeja/capa): roda sempre e avisa mesmo se já atualizado."""
        self._toast("ScribaDev", "Procurando atualizações…")
        threading.Thread(target=self._update_check_worker, args=(True,), daemon=True, name="updchk").start()

    def _update_check_worker(self, announce: bool) -> None:
        from datetime import date

        ver = None
        try:
            from . import updates

            ver = updates.update_available()
        except Exception:
            log.exception("checagem de atualização falhou")
        try:
            util.update_state(last_update_check=date.today().isoformat())
        except Exception:
            pass
        self.ui(lambda: self._on_update_result(ver, announce))

    def _on_update_result(self, ver, announce: bool) -> None:
        self.update_news = ver
        if ver:
            self._toast("ScribaDev — atualização", f"Nova versão v{ver} disponível.")
            if self.main_win:
                self.main_win.show_update(ver)
        elif announce:
            self._toast("ScribaDev", "Você já está na versão mais recente.")

    def apply_update(self, on_done) -> None:
        """Aplica a atualização (git pull) em thread; chama on_done(ok, msg) na UI."""
        def work():
            from . import updates

            ok, msg = updates.apply_git_update()
            self.ui(lambda: on_done(ok, msg))

        threading.Thread(target=work, daemon=True, name="updapply").start()

    def relaunch(self) -> None:
        """Reinicia o app: agenda um novo lançamento para DEPOIS que este processo sair
        (libera o mutex de instância única), avisa o usuário com UMA mensagem final
        (sucesso ou falha — nunca as duas em cascata) e FORÇA a saída se o encerramento
        gracioso travar. Sem o 'forçar', um cleanup preso (tray/thread) segura o mutex,
        a nova instância aborta no single-instance e o app 'não reinicia sozinho'.
        Auto-restart do update (#19)."""
        import subprocess
        from pathlib import Path
        from tkinter import messagebox

        pyw = Path(sys.executable)
        if pyw.name.lower() == "python.exe":
            pyw = pyw.with_name("pythonw.exe")  # sem janela de console
        ps = (f"Wait-Process -Id {os.getpid()} -Timeout 60 -ErrorAction SilentlyContinue; "
              f"Start-Process '{pyw}' -ArgumentList '-m','scriba.cli','run'")
        try:
            subprocess.Popen(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0),
            )
            scheduled = True
        except Exception:
            log.exception("falha ao agendar o relançamento")
            scheduled = False

        # UMA única mensagem com o resultado final — nunca uma 2ª em cascata.
        try:
            if scheduled:
                messagebox.showinfo(
                    "ScribaDev atualizado",
                    "A atualização foi aplicada com sucesso.\n\n"
                    "O app vai fechar e reabrir sozinho em alguns segundos.",
                )
            else:
                messagebox.showwarning(
                    "ScribaDev atualizado",
                    "A atualização foi aplicada, mas não consegui reiniciar automaticamente.\n\n"
                    "Feche e abra o ScribaDev para usar a nova versão.",
                )
        except Exception:
            pass

        if not scheduled:
            return

        # encerra gracioso e, se a saída travar, força em 3 s — libera o mutex para o
        # relançador (Wait-Process) subir a nova instância. daemon=True não atrasa a
        # saída natural quando ela ocorre antes do timeout.
        self.request_quit()
        watchdog = threading.Timer(3.0, lambda: os._exit(0))
        watchdog.daemon = True
        watchdog.start()

    def show_wizard(self) -> None:
        """Abre o assistente de perfil (chamável de qualquer thread)."""
        self.ui(self._open_wizard_ui)

    def _open_wizard_ui(self) -> None:
        if self.wizard_win is None:
            from .wizard_ui import WizardWindow

            self.wizard_win = WizardWindow(self.root, self)
        self.wizard_win.show()

    def _maybe_offer_wizard(self) -> None:
        """Primeiro uso (sem prompt.md): oferece o assistente de perfil uma única vez."""
        try:
            from .promptgen import should_offer_on_boot

            if should_offer_on_boot():
                log.info("primeiro uso sem prompt.md — abrindo o assistente de perfil")
                self._open_wizard_ui()
        except Exception:
            log.exception("não consegui oferecer o assistente de perfil")

    def reload_config(self) -> None:
        self.cfg = load()
        self._setup_hotkey()
        log.info("configuração recarregada")

    # -------------------------------------------------------------- hotkey --

    def _setup_hotkey(self) -> None:
        from .hotkey import GlobalHotkey

        if self.hotkey is not None:
            self.hotkey.stop()
            self.hotkey = None
        spec = (self.cfg.ui.hotkey or "").strip()
        if not spec:
            return
        hk = GlobalHotkey(spec, self._hotkey_toggle)
        if hk.start():
            self.hotkey = hk
        else:
            log.warning("hotkey '%s' não pôde ser registrada", spec)
            self._toast("Atalho indisponível", f"Não consegui registrar '{spec}' (em uso por outro app?)")

    def _hotkey_toggle(self) -> None:
        if self.is_recording():
            self.stop_recording(keep=True)  # intenção explícita: guarda mesmo curta
        else:
            self.start_recording("manual")

    # ---------------------------------------------------------------- pill --

    def _ensure_pill(self):
        if self.pill is None:
            from .overlay import RecordingPill

            self.pill = RecordingPill(
                self.root,
                # ■ = intenção explícita de guardar: ignora a duração mínima
                on_stop=lambda: threading.Thread(
                    target=self.stop_recording, kwargs={"keep": True}, daemon=True
                ).start(),
                on_discard=lambda: threading.Thread(
                    target=self.stop_recording, kwargs={"discard": True}, daemon=True
                ).start(),
                on_record=lambda: threading.Thread(
                    target=self.start_recording, args=("manual",), daemon=True
                ).start(),
                on_speakers=self._set_speakers_live,  # #13: nº de participantes ao vivo
            )
        return self.pill

    def _show_pill(self) -> None:
        """Pílula em modo gravação (●/cronômetro/■/×)."""
        if not self.cfg.ui.overlay or self.root is None:
            return
        pill = self._ensure_pill()
        pill.set_mode("recording")
        pill.reset_speakers(util.read_state().get("last_num_speakers") or 0)  # #13: sugere o último
        pill.show()
        self._tick_pill()

    def _show_pill_idle(self) -> None:
        """Pílula em modo espera (⏺ Gravar reunião) — call ativa sem gravação."""
        if not self.cfg.ui.overlay or self.root is None:
            return
        pill = self._ensure_pill()
        pill.set_mode("idle")
        pill.show()

    def _hide_pill(self) -> None:
        if self.pill is not None:
            self.pill.hide()

    def _pill_finishing(self) -> None:
        if self.pill is not None and self.is_recording():
            self.pill.set_status("finalizando…")

    def _pill_resume(self) -> None:
        if self.pill is not None:
            self.pill.clear_status()

    def _pill_processing(self, status: str) -> None:
        """Mostra o estágio do processamento na pílula (fora de call/gravação ativa)."""
        if not self.cfg.ui.overlay or self.root is None:
            return
        if self.is_recording() or self.call_active:
            return  # a pílula está servindo a uma call/gravação ativa
        pill = self._ensure_pill()
        pill.show()
        pill.set_processing(util.stage_label(status))

    def _hide_pill_if_processing(self) -> None:
        """Esconde a pílula só se ela não estiver servindo a uma call/gravação nova."""
        if self.is_recording() or self.call_active:
            return
        self._hide_pill()

    def _tick_pill(self) -> None:
        if self.pill is None or self.root is None:
            return
        with self.rec_lock:
            rec = self.rec
        if rec is None:
            return
        self.pill.set_elapsed(rec.duration_seconds())
        self.root.after(500, self._tick_pill)

    # -------------------------------------------------------------- worker --

    def _process_subprocess(self, folder) -> None:
        """Carrega o modelo, transcreve e gera a nota num subprocesso isolado.

        A saída vai para process.log NA PASTA DA REUNIÃO — nunca para PIPE: os
        warnings nativos (ex.: torchcodec, ~8 KB no import do pyannote) enchiam
        o buffer do pipe e travavam o subprocesso no write, porque a leitura só
        acontecia depois do exit. Acompanha o meta.json e espelha o estágio na
        pílula; crash nativo no subprocesso não derruba a bandeja. Se o processo
        sair com erro, o meta vai para "failed" — nunca fica "transcribing" eterno.
        """
        import json
        import subprocess
        import time
        from pathlib import Path

        folder = Path(folder)
        log.info("processando %s (subprocesso)", folder.name)
        python = Path(sys.prefix) / "Scripts" / "python.exe"
        args = [str(python), "-X", "utf8", "-m", "scriba.cli", "process", str(folder)]
        meta_path = folder / "meta.json"
        log_path = folder / "process.log"
        out = None
        try:
            try:
                out = open(log_path, "a", encoding="utf-8", errors="replace")
                out.write(f"\n==== scriba process · {time.strftime('%d/%m/%Y %H:%M:%S')} ====\n")
                out.flush()
            except OSError:
                out = None  # sem log em disco; segue com DEVNULL
            proc = subprocess.Popen(
                args,
                stdout=out if out is not None else subprocess.DEVNULL,
                stderr=subprocess.STDOUT if out is not None else subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception:
            log.exception("não consegui iniciar o subprocesso de %s", folder.name)
            self._toast("ScribaDev: falha ao processar", folder.name)
            self.ui(self._hide_pill_if_processing)
            return
        finally:
            if out is not None:
                out.close()  # o filho herdou o handle; o nosso pode fechar já

        # acompanha o estágio pelo meta.json e espelha na pílula
        last_status = None
        while proc.poll() is None:
            try:
                status = json.loads(meta_path.read_text(encoding="utf-8")).get("status")
            except (OSError, ValueError):
                status = None
            if status and status != last_status:
                last_status = status
                self.ui(lambda s=status: self._pill_processing(s))
            time.sleep(1.0)

        tail = ""
        try:
            tail = "\n".join(
                log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-12:]
            )
        except OSError:
            pass
        log.info("processamento de %s (rc=%s):\n%s", folder.name, proc.returncode, tail)
        self.ui(self._hide_pill_if_processing)
        if proc.returncode != 0:
            # invariante: subprocesso saiu com erro => status terminal no meta
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if meta.get("status") in util.IN_PROGRESS_STATUSES:
                    meta["status"] = "failed"
                    meta["error"] = tail[-600:]
                    util.atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))
            except Exception:
                log.exception("não consegui marcar %s como failed", folder.name)
            self._toast(
                "ScribaDev: falha ao processar",
                f"{folder.name} — detalhes em process.log; retry: scribadev process",
            )
            return
        export_path = title = None
        try:
            done_meta = json.loads(meta_path.read_text(encoding="utf-8"))
            export_path = done_meta.get("export_path")
            title = done_meta.get("title")
            # diarização falhou mas o processamento seguiu (com "Participantes"): o erro
            # ficou no process.log da pasta — repete no scriba.log central p/ ficar visível
            # na janela de Log e no diagnóstico (pedido do Allan)
            if done_meta.get("diarization_error"):
                log.warning("diarização não rodou em %s: %s (detalhes no process.log da pasta)",
                            folder.name, done_meta["diarization_error"])
        except Exception:
            log.exception("meta.json ilegível pós-processamento")
        if export_path and self.notifier:
            try:
                self.notifier.notes_ready(Path(export_path))
            except Exception:
                log.exception("toast de notas falhou")
        # com o título pronto, a pasta da gravação passa a se explicar no Explorer
        if title:
            renamed = util.rename_recording_folder(folder, title)
            if renamed != folder:
                log.info("pasta renomeada: %s", renamed.name)
                folder = renamed
        log.info("concluído %s -> %s", folder.name, export_path)

    def _worker(self) -> None:
        """Fila serial de processamento: gravações recém-encerradas e pendências
        órfãs de sessões anteriores passam por aqui, uma de cada vez."""
        while not self.stop_event.is_set():
            try:
                folder = self.jobs.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self._process_subprocess(folder)
            except Exception:
                log.exception("worker: erro ao processar %s", getattr(folder, "name", folder))

    def scan_pending(self) -> None:
        """Enfileira reuniões gravadas que ainda não viraram notas (ex.: app caiu)."""
        import json

        from .recorder import repair_folder

        count = 0
        # rglob: acha reuniões na árvore ano/mês/dia E nas pastas legadas (planas)
        for meta_path in sorted(self.cfg.output.resolved_recordings_dir().rglob("meta.json")):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            status = meta.get("status")
            if status == "recording":
                # gravação órfã de um crash: repara o header e adota
                duration = repair_folder(meta_path.parent)
                meta["status"] = "recorded"
                meta["duration_seconds"] = round(duration, 1)
                meta["interrupted"] = True  # o notas.md avisa que a call foi cortada
                util.atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))
                log.info("gravação órfã adotada (%.0fs): %s", duration, meta_path.parent.name)
                if duration < self.cfg.detection.min_call_seconds:
                    continue
                status = "recorded"
            # além de recorded/transcribed, readota também quem morreu NO MEIO do
            # processamento (transcribing/summarizing presos por crash ou kill) —
            # "failed" é terminal e não volta sozinho (retry manual: scriba process)
            if status in ("recorded", "transcribed", "transcribing", "summarizing"):
                # não readota pasta com .lock ativo (PID vivo/recente): pode haver
                # um 'scriba process' manual em andamento sobre a mesma reunião
                if util.is_locked(meta_path.parent):
                    log.info("pendente pulada (.lock ativo): %s", meta_path.parent.name)
                    continue
                self.jobs.put(meta_path.parent)
                count += 1
        log.info("pendentes enfileiradas: %d", count)
        if count:
            self._toast("Processando pendentes", f"{count} reunião(ões) na fila")

    def purge_old(self) -> None:
        """Apaga gravações já transcritas além do prazo ([audio].retention_days)."""
        try:
            from .retention import purge_old_recordings

            removed = purge_old_recordings(self.cfg)
        except Exception:
            log.exception("retenção falhou")
            return
        if removed:
            days = int(getattr(self.cfg.audio, "retention_days", 0) or 0)
            log.info("retenção: %d gravação(ões) > %d dias removida(s)", len(removed), days)
            self._toast("Limpeza de gravações", f"{len(removed)} além de {days} dias removida(s)")

    def _purge_loop(self) -> None:
        """Roda a retenção (em thread, fora da UI) e reagenda a cada 6 h."""
        threading.Thread(target=self.purge_old, daemon=True, name="purge").start()
        if self.root is not None and not self.stop_event.is_set():
            self.root.after(6 * 3600 * 1000, self._purge_loop)

    def _reindex_search_index(self) -> None:
        """Boot (#12): reconstrói o índice de busca das reuniões se estiver vazio
        (index.db ausente/recriado/nunca indexado). Em thread — não bloqueia a
        bandeja; no-op se já populado. Falha aqui nunca derruba o app."""
        def work() -> None:
            try:
                from . import meetings_index

                n = meetings_index.reindex_if_needed(self.cfg.output.resolved_recordings_dir())
                log.info("índice de busca pronto: %d reunião(ões)", n)
            except Exception:
                log.exception("reindex de boot do índice de busca falhou")

        threading.Thread(target=work, daemon=True, name="reindex").start()

    def _show_window_listener(self) -> None:
        """Espera o sinal de uma 2ª instância (atalho clicado) e mostra a janela."""
        handle = ctypes.windll.kernel32.CreateEventW(None, False, False, _SHOW_EVENT_NAME)
        if not handle:
            log.warning("não consegui criar o evento de ativação")
            return
        while not self.stop_event.is_set():
            # timeout de 1 s para reavaliar o stop_event; 0 = WAIT_OBJECT_0 (sinalizado)
            if ctypes.windll.kernel32.WaitForSingleObject(handle, 1000) == 0:
                log.info("ativado por uma 2ª instância (atalho) — mostrando a janela")
                self.show_main()

    # ----------------------------------------------------------------- run --

    def request_quit(self) -> None:
        self.stop_event.set()

    def _drain_ui(self) -> None:
        while True:
            try:
                fn = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                log.exception("erro em callback de UI")
        if self.stop_event.is_set():
            self.root.quit()
            return
        self.root.after(100, self._drain_ui)

    def run(self) -> int:
        # exceções não tratadas (em threads ou callbacks Tk) iam para o nada no modo
        # sem console — agora ficam no log E abrem um diálogo amigável (#22)
        sys.excepthook = lambda *exc: self._on_unhandled(exc, "geral")
        threading.excepthook = lambda args: self._on_unhandled(
            (args.exc_type, args.exc_value, args.exc_traceback),
            f"thread {args.thread.name if args.thread else '?'}",
        )
        self.root = tk.Tk()
        self.root.withdraw()  # inicia só na bandeja; a janela abre pela bandeja/atalho
        self.root.report_callback_exception = lambda *exc: self._on_unhandled(exc, "callback Tk")

        from .main_window import MainWindow

        self.main_win = MainWindow(self.root, self)

        from .tray import Tray

        self.tray = Tray(self)
        self.tray.start()

        self.detector = Detector(
            self.cfg.detection,
            on_call_started=self._on_call_started,
            on_call_ended=self._on_call_ended,
            # feedback imediato ao desligar a call: a pílula avisa que está na tolerância
            on_grace=lambda: self.ui(self._pill_finishing),
            on_grace_cancel=lambda: self.ui(self._pill_resume),
        )
        threading.Thread(target=self.detector.run, args=(self.stop_event,), daemon=True, name="detector").start()
        threading.Thread(target=self._worker, daemon=True, name="worker").start()
        threading.Thread(target=self._show_window_listener, daemon=True, name="activate").start()
        self._setup_hotkey()
        self.scan_pending()
        self.root.after(8000, self._purge_loop)  # limpeza de gravações antigas (e a cada 6 h)
        self.root.after(2500, self._maybe_offer_wizard)  # 1º uso: assistente de perfil
        self.root.after(5000, self._reindex_search_index)  # índice de busca (#12), só se vazio
        self.root.after(9000, self._check_updates_boot)    # aviso de nova versão (#19)

        if not self.start_hidden:
            self.show_main()  # lançamento manual (atalho): abre a janela na frente
        log.info("ScribaDev iniciado — monitorando calls do Teams")
        self.root.after(100, self._drain_ui)
        self.root.mainloop()

        # saída: salva gravação em andamento; pendências ficam para o próximo start
        if self.is_recording():
            self.stop_recording(False)
        self.tray.stop()
        log.info("ScribaDev encerrado")
        return 0


def run_app(minimized: bool = False) -> int:
    # No scribadev-tray.exe (pythonw) não existe stdout/stderr: qualquer print()
    # — nosso ou de bibliotecas (tqdm do Whisper) — estouraria AttributeError.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    util.preload_msvc_runtime()  # antes do winrt (toasts) trazer o runtime velho
    util.quiet_crt_asserts()  # sem diálogos modais de assert do CRT (PortAudio)
    # identidade na taskbar: sem isso a janela (pythonw filho) herda ícone/hint
    # do atalho do IDLE; precisa vir antes de qualquer janela
    util.set_explicit_app_id()
    _setup_logging()
    if not _single_instance():
        # clique no atalho com o app já vivo: pede à instância ativa que abra a
        # janela principal (antes a 2ª instância morria muda e "nada acontecia")
        if _signal_show_window():
            print("ScribaDev já está rodando — abrindo a janela da instância ativa.")
        else:
            print("ScribaDev já está rodando (ícone na bandeja).")
        return 0
    return ScribaApp(start_hidden=minimized).run()

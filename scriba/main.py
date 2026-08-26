"""Orquestração do ScribaDev: detector → gravador → pipeline → notificações/UI.

Modelo de threads:
- main thread: QApplication (Qt) drenando uma fila de comandos de UI via UiPump;
- detector: thread vigiando o registro;
- worker: thread processando pastas gravadas (transcrição + notas), em fila —
  uma call nova pode ser gravada enquanto a anterior ainda transcreve.
A UI é PySide6 (scriba/qt/); qualquer thread agenda trabalho na GUI por self.ui(fn).
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import queue
import sys
import threading
import time

from . import plat, util
from .config import load
from .detector import Detector
from .recorder import Recording

log = logging.getLogger("scriba")

# Instância única + sinal "mostre a janela" + relaunch: por SO, na camada de
# plataforma (plat._win = mutex/evento/PowerShell históricos; plat._posix =
# flock/socket/Popen). O retry pós-update (#74) vive lá.

# Watchdog do subprocesso de processamento (#176). A fila é SERIAL: um filho
# pendurado não travava só a reunião dele, travava todas as seguintes - e a capa
# seguia mostrando "Transcrevendo…" com a máquina a 0% (foi exatamente o relato).
# Sinais de vida sondados: CPU acumulada do filho, mtime do meta.json e tamanho do
# process.log. Qualquer um deles andando conta como progresso.
_TICK_PROCESSAMENTO_S = 1.0     # passo do laço (espelha o estágio na pílula)
_SONDA_PROGRESSO_S = 30.0       # de quanto em quanto tempo os sinais são lidos
_PARADO_S = 20 * 60             # tudo parado por mais que isto = travado
_CPU_DELTA_MIN = 0.5            # s de CPU que já contam como "andou"


def _andou(antes: tuple, depois: tuple) -> bool:
    """Houve QUALQUER sinal de progresso entre duas sondagens do filho?

    A CPU é o sinal forte: transcrever/diarizar queima CPU sem parar, então CPU
    congelada por minutos a fio é travamento, não lentidão. Onde a sondagem de CPU
    não existe (SO sem suporte), ela vem como None e a decisão fica com o meta.json
    e o process.log - por isso a comparação é campo a campo, e não igualdade da
    tupla: None == None não pode ser lido como "não andou".
    """
    cpu_a, meta_a, log_a = antes
    cpu_d, meta_d, log_d = depois
    if cpu_a is not None and cpu_d is not None and cpu_d - cpu_a >= _CPU_DELTA_MIN:
        return True
    return meta_d != meta_a or log_d != log_a


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
        # início de gravação em andamento (#114): Recording() é montado FORA do
        # rec_lock (abrir streams pode demorar; segurar o lock congelava a GUI).
        # _rec_starting evita partida dupla; _pending_stop guarda um stop que
        # chegou no meio da montagem, executado assim que ela termina.
        self._rec_starting = False
        self._pending_stop: dict | None = None
        self.call_active = False  # call do Teams em andamento (a pílula segue a call)
        self._app = None  # QApplication (criado em run())
        self._pump = None  # UiPump que drena a ui_queue na thread da GUI
        self._quit_watchdog = None  # Timer que força a saída se o encerramento travar
        self.pill = None
        self.tray = None
        self.settings = None
        self.notes_win = None
        self.action_hub = None   # hub de pendências (#78), instância única
        self.wizard_win = None
        self.log_win = None
        self.main_win = None
        self.timesheet_win = None  # janela de Apontamentos (#118/#124)
        self.update_news = None  # versão nova detectada (#19); a capa exibe o aviso
        self.hotkey = None
        self.hotkey_split = None  # #38: atalho "nova call" (dividir a gravação)
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
        """Toast SEMPRE em thread própria (#161): a entrega fala com o serviço de
        notificações por COM (cross-process) — se o serviço estiver pendurado, quem
        chamou (GUI, detector, worker) segue vivo; no pior caso o toast atrasa/some."""
        if not self.notifier:
            return

        def _go() -> None:
            try:
                self.notifier.info(title, body)
            except Exception:
                log.exception("toast falhou")

        threading.Thread(target=_go, daemon=True, name="toast").start()

    def ui(self, fn) -> None:
        """Agenda um callable para rodar na thread da GUI (drenado pelo UiPump)."""
        self.ui_queue.put(fn)

    def _after(self, ms: int, fn) -> None:
        """Agenda `fn` na thread da GUI após `ms` (substitui o root.after do Tk)."""
        from PySide6.QtCore import QTimer

        QTimer.singleShot(ms, fn)

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
            if self._app is not None and not self.stop_event.is_set():
                self._after(700, self._tick_tray)  # ritmo de pulso (~como a pílula)

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
            self._toast("ScribaDev: áudio indisponível",
                        "Não consegui acessar microfone/loopback agora. "
                        "Detalhes e reporte: janela de Log (bandeja).")
            return
        with self.rec_lock:
            if self.rec is not None or self._rec_starting:
                return
            # reserva o slot e SOLTA o lock: montar Recording() abre streams e pode
            # demorar — segurá-lo aqui congelava is_recording()/_tick da GUI quando
            # algo nativo pendurava a montagem (#114)
            self._rec_starting = True
            self._pending_stop = None
        rec = None
        try:
            rec = Recording(self.cfg)
        except Exception as e:
            log.exception("falha ao iniciar gravação")
            self._toast("ScribaDev: erro ao iniciar gravação",
                        f"{e} — detalhes e reporte na janela de Log (bandeja).")
            return
        finally:
            with self.rec_lock:
                pending = self._pending_stop
                self._pending_stop = None
                self._rec_starting = False
                if rec is not None:
                    self.rec = rec
                    self.rec_source = source
        if pending is not None:
            # a call terminou enquanto a gravação nascia: encerra pelo fluxo normal
            log.info("stop chegou durante o início da gravação — encerrando já")
            self.stop_recording(**pending)
            return
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
            if rec is None and self._rec_starting:
                # gravação ainda nascendo (#114): registra a intenção — o
                # start_recording encerra assim que a montagem terminar
                self._pending_stop = {"discard": discard, "keep": keep}
                return
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
        from .qt.speakers_ui import ask_num_speakers

        def done(n: int | None) -> None:
            if n is not None:
                self._save_num_speakers(folder, n)
                util.update_state(last_num_speakers=int(n))
            self.jobs.put(folder)

        try:
            last = util.read_state().get("last_num_speakers")
            ask_num_speakers(
                done,
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

    def _on_title(self, title: str) -> None:
        """Título estável capturado durante a gravação (#35): preenche meeting_title
        no meta da gravação ATIVA se ele estava vazio — melhora a nota e o índice de
        busca para calls que demoram a expor o título. Roda na thread do detector."""
        import json

        with self.rec_lock:
            rec = self.rec
        if rec is None:
            return
        meta_path = rec.folder / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("meeting_title"):
                return  # já tem título de janela: não sobrescreve
            meta["meeting_title"] = title
            util.atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))
            log.info("meeting_title capturado tardiamente: %s (%s)", title, rec.folder.name)
        except Exception:
            log.exception("não consegui gravar meeting_title tardio em %s", rec.folder.name)

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

    def _refresh_home_after_process(self) -> None:
        """Re-atualiza a capa depois que o processamento terminou E o índice foi
        reconciliado pós-rename (#90). Roda na thread da GUI. Janela fechada/oculta
        não precisa: refresh_home roda no próximo show()."""
        if self.main_win is not None and self.main_win.isVisible():
            try:
                self.main_win.refresh_home()
            except Exception:
                log.exception("refresh da capa pós-processamento falhou")

    def show_settings(self) -> None:
        """Abre a janela de Configurações (chamável de qualquer thread)."""
        self.ui(self._open_settings_ui)

    def _open_settings_ui(self) -> None:
        if self.settings is None:
            from .qt.settings_ui import SettingsWindow

            self.settings = SettingsWindow(self)
        self.settings.show()

    def show_notes(self, note_path=None) -> None:
        """Abre a janela de Notas (chamável de qualquer thread). Se `note_path`
        for dado (caminho do .md de uma reunião), navega até ela na árvore assim
        que a janela abre - usado pela capa ao clicar numa reunião recente."""
        self.ui(lambda: self._open_notes_ui(note_path))

    def _open_notes_ui(self, note_path=None) -> None:
        if self.notes_win is None:
            from .qt.notes_ui import NotesWindow

            self.notes_win = NotesWindow(self)
        self.notes_win.show()
        if note_path is not None:
            self.notes_win.reveal_note(note_path)

    def show_action_hub(self, note_path=None) -> None:
        """Abre o hub de pendências (#78/#82), instância única, chamável de qualquer thread.
        Se `note_path` for dado, posiciona o hub na reunião correspondente."""
        self.ui(lambda: self._open_action_hub_ui(note_path))

    def _open_action_hub_ui(self, note_path=None) -> None:
        if self.notes_win is None:
            from .qt.notes_ui import NotesWindow

            self.notes_win = NotesWindow(self)   # o hub reusa collect + navegação de Notas
        if self.action_hub is None:
            from .qt.notes_ui import _ActionItemsWindow

            self.action_hub = _ActionItemsWindow(
                self, self.notes_win._collect_action_groups,
                self.notes_win.reveal_note_at_section)
        self.action_hub.refresh()
        self.action_hub.show()
        if note_path is not None:
            self.action_hub.focus_meeting(note_path)

    def show_timesheet(self) -> None:
        """Abre a janela de Apontamentos (#118/#124), instância única, chamável de
        qualquer thread. O acesso é condicionado ao módulo ativado (#126): o item
        da bandeja só aparece com [timesheet].enabled."""
        self.ui(self._open_timesheet_ui)

    def _open_timesheet_ui(self) -> None:
        if self.timesheet_win is None:
            from .qt.timesheet_ui import TimesheetWindow

            self.timesheet_win = TimesheetWindow(self)
        self.timesheet_win.show()

    def refresh_timesheet(self) -> None:
        """Reflete NA HORA, na janela de Apontamentos ABERTA, mudanças feitas no
        banco por fora dela - sugestão nova no fim de uma call, varredura do
        boot. Sem isso a janela só recarregava no show() (fechar e reabrir).
        Chamável de qualquer thread; mesmo padrão do _refresh_home_after_process:
        o teste de existe+visível roda NA thread da GUI, e janela fechada/oculta
        é no-op (o show() dela já recarrega ao abrir)."""
        def _do() -> None:
            win = self.timesheet_win
            if win is not None and win.isVisible():
                win.refresh()

        self.ui(_do)

    def show_log(self) -> None:
        """Abre a janela de Log/diagnóstico (chamável de qualquer thread)."""
        self.ui(self._open_log_ui)

    def _open_log_ui(self) -> None:
        if self.log_win is None:
            from .qt.log_ui import LogWindow

            self.log_win = LogWindow(self)
        self.log_win.show()

    # -- exceções não tratadas (#22) -------------------------------------------

    def _on_unhandled(self, exc_info, where: str) -> None:
        """Handler único dos 3 excepthooks: SEMPRE loga e, se a UI já existe, mostra um
        diálogo amigável (sem console, uma exceção sumia calada). Crash cedo demais
        (root ainda None) ou já encerrando → fica só no log."""
        log.error("exceção não tratada (%s)", where, exc_info=exc_info)
        if self._app is None or self.stop_event.is_set():
            return
        import traceback as _tb

        detail = f"[{where}]\n\n" + "".join(_tb.format_exception(*exc_info))
        try:
            self.ui(lambda: self._show_crash(detail))
        except Exception:
            pass  # nunca deixar o handler de erro levantar outro erro

    def _show_crash(self, detail: str) -> None:
        try:
            from .qt.log_ui import show_crash_dialog

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
        from PySide6.QtWidgets import QMessageBox

        # o COMO relançar é por SO (plat._win = PowerShell Wait-Process/Start-Process;
        # plat._posix = Popen detached + retry no lock); o fluxo de UX fica aqui
        try:
            util.ensure_app_dirs()
            plat.spawn_relaunch(os.getpid(), util.LOGS_DIR / "relaunch.log")
            scheduled = True
        except Exception:
            log.exception("falha ao agendar o relançamento")
            scheduled = False

        # UMA única mensagem com o resultado final — nunca uma 2ª em cascata.
        try:
            if scheduled:
                QMessageBox.information(
                    None, "ScribaDev atualizado",
                    "A atualização foi aplicada com sucesso.\n\n"
                    "O app vai fechar e reabrir sozinho em alguns segundos.",
                )
            else:
                QMessageBox.warning(
                    None, "ScribaDev atualizado",
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
            from .qt.wizard_ui import WizardWindow

            self.wizard_win = WizardWindow(app=self)
        self.wizard_win.show()

    def _maybe_offer_wizard(self) -> None:
        """Primeiro uso: numa instalação CONGELADA sem setup concluído, o wizard de
        1º uso (#147: análise da máquina + downloads) vem PRIMEIRO e, ao terminar,
        encadeia a oferta do assistente de perfil. Fora disso, só o de perfil."""
        try:
            from .qt.setup_wizard import SetupWizardWindow, should_run

            if should_run():
                log.info("instalação por instalador sem setup — abrindo o wizard de 1º uso")
                self._setup_wizard = SetupWizardWindow(
                    self, on_finish=self._maybe_offer_profile_wizard)
                self._setup_wizard.show()
                return
        except Exception:
            log.exception("não consegui abrir o wizard de 1º uso")
        self._maybe_offer_profile_wizard()

    def _maybe_offer_profile_wizard(self) -> None:
        """Quem nunca escolheu um perfil recebe o assistente uma única vez."""
        try:
            from .promptgen import mark_profile_offered, should_offer_on_boot

            if should_offer_on_boot():
                log.info("perfil nunca escolhido — abrindo o assistente de perfil")
                mark_profile_offered()  # antes de abrir: fechar no X não repete a oferta
                self._open_wizard_ui()
        except Exception:
            log.exception("não consegui oferecer o assistente de perfil")

    def reload_config(self) -> None:
        self.cfg = load()
        self._setup_hotkey()
        log.info("configuração recarregada")

    # -------------------------------------------------------------- hotkey --

    def _setup_hotkey(self) -> None:
        # (re)registra os atalhos globais: gravar/parar e "nova call" (#38)
        for attr in ("hotkey", "hotkey_split"):
            hk = getattr(self, attr)
            if hk is not None:
                hk.stop()
                setattr(self, attr, None)
        self._register_hotkey("hotkey", self.cfg.ui.hotkey, self._hotkey_toggle)
        self._register_hotkey("hotkey_split", getattr(self.cfg.ui, "hotkey_split", ""), self._split_now)

    def _register_hotkey(self, attr: str, spec: str, handler) -> None:
        from .hotkey import GlobalHotkey

        spec = (spec or "").strip()
        if not spec:
            return
        hk = GlobalHotkey(spec, handler)
        if hk.start():
            setattr(self, attr, hk)
        else:
            log.warning("hotkey '%s' não pôde ser registrada", spec)
            self._toast("Atalho indisponível", f"Não consegui registrar '{spec}' (em uso por outro app?)")

    def _hotkey_toggle(self) -> None:
        if self.is_recording():
            self.stop_recording(keep=True)  # intenção explícita: guarda mesmo curta
        else:
            self.start_recording("manual")

    def _split_now(self) -> None:
        """✂ nova call (#38): encerra a gravação atual (guardando) e começa outra na
        hora, num clique. Fecha a fronteira de call que o usuário SABE onde é. Pula a
        sonda de áudio (probe=False): os dispositivos estão funcionando agora."""
        if not self.is_recording():
            self.start_recording("manual")  # nada gravando ainda: só começa
            return
        self.stop_recording(keep=True)                # parte 1 -> fila (intenção explícita)
        self.start_recording("manual", probe=False)   # parte 2 já em andamento
        self._toast("Gravação dividida", "Nova gravação em andamento.")

    # ---------------------------------------------------------------- pill --

    def _ensure_pill(self):
        if self.pill is None:
            from .qt.overlay import RecordingPill

            self.pill = RecordingPill(
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
                on_split=lambda: threading.Thread(  # #38: ✂ fecha esta call e começa outra
                    target=self._split_now, daemon=True
                ).start(),
            )
        return self.pill

    def _show_pill(self) -> None:
        """Pílula em modo gravação (●/cronômetro/■/×)."""
        if not self.cfg.ui.overlay or self._app is None:
            return
        pill = self._ensure_pill()
        pill.set_mode("recording")
        pill.reset_speakers(util.read_state().get("last_num_speakers") or 0)  # #13: sugere o último
        pill.show()
        self._tick_pill()

    def _show_pill_idle(self) -> None:
        """Pílula em modo espera (⏺ Gravar reunião) — call ativa sem gravação."""
        if not self.cfg.ui.overlay or self._app is None:
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
        if not self.cfg.ui.overlay or self._app is None:
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
        if self.pill is None or self._app is None:
            return
        with self.rec_lock:
            rec = self.rec
        if rec is None:
            return
        self.pill.set_elapsed(rec.duration_seconds())
        self._after(500, self._tick_pill)

    # -------------------------------------------------------------- worker --

    def _marcar_falha(self, meta_path, folder, erro: str) -> None:
        """Invariante do worker: filho que não terminou bem => status TERMINAL no
        meta. Sem isto a capa fica num "Transcrevendo…" que não acaba nunca."""
        import json

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("status") in util.IN_PROGRESS_STATUSES:
                meta["status"] = "failed"
                meta["error"] = erro
                util.atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))
        except Exception:
            log.exception("não consegui marcar %s como failed", folder.name)

    @staticmethod
    def _sinais_do_filho(pid: int, meta_path, log_path) -> tuple:
        """(CPU acumulada, mtime do meta.json, tamanho do process.log) - o trio que
        diz se o subprocesso está VIVO no sentido que importa: fazendo alguma coisa.
        Cada campo vira None quando não dá para ler; nada aqui levanta."""
        try:
            cpu = plat.pid_cpu_seconds(pid)
        except Exception:
            cpu = None

        def _stat(caminho, campo):
            try:
                return campo(caminho.stat())
            except OSError:
                return None

        return (cpu,
                _stat(meta_path, lambda s: s.st_mtime_ns),
                _stat(log_path, lambda s: s.st_size))

    def _limite_sem_progresso(self) -> float:
        """Quanto tempo sem sinal nenhum antes de declarar travado.

        Piso de _PARADO_S, esticado pelo timeout do resumo: a geração do resumo é
        uma espera legítima de rede (ollama, nuvem, CLI do Claude) que pode não
        queimar CPU nem escrever no log enquanto acontece.
        """
        try:
            timeout = float(getattr(self.cfg.summary, "timeout_seconds", 0) or 0)
        except Exception:
            timeout = 0.0
        if timeout <= 0:
            return float(_PARADO_S)
        return max(float(_PARADO_S), timeout + 300)

    def _matar_filho(self, proc, folder) -> None:
        """Encerra o subprocesso travado e limpa o `.lock` DELE.

        Só o dele: um `scribadev process` manual sobre a mesma pasta tem lock
        próprio e não pode ser apagado por engano. O lock ficaria órfão de todo
        jeito (o filho morre sem rodar o `finally`), mas deixar sujeira para o
        `is_locked` limpar depois é pior do que limpar aqui, onde se sabe o dono.
        """
        try:
            proc.kill()
            proc.wait(timeout=10)
        except Exception:
            log.exception("não consegui encerrar o subprocesso de %s", folder.name)
        util.clear_lock(folder, proc.pid)

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
        from . import addons

        if addons.is_installing():
            # instalação de componentes reescrevendo o addons (#167): processar
            # agora = EACCES no import e reunião "falhada" sem culpa. Fica como
            # está; a varredura de pendentes readota quando a instalação acabar.
            log.info("instalação de componentes em andamento — processamento de %s adiado",
                     folder.name)
            self.ui(self._hide_pill_if_processing)
            return
        log.info("processando %s (subprocesso)", folder.name)
        args = util.app_command("process", str(folder))
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
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            log.exception("não consegui iniciar o subprocesso de %s", folder.name)
            self._toast("ScribaDev: falha ao processar", folder.name)
            self.ui(self._hide_pill_if_processing)
            return
        finally:
            if out is not None:
                out.close()  # o filho herdou o handle; o nosso pode fechar já

        # acompanha o estágio pelo meta.json e espelha na pílula, vigiando o
        # PROGRESSO do filho (#176): um subprocesso pendurado congelava a fila
        # inteira para sempre - a espera aqui não tinha nem timeout nem watchdog
        last_status = None
        limite = self._limite_sem_progresso()
        sinais = self._sinais_do_filho(proc.pid, meta_path, log_path)
        ultimo_sinal = ultima_sonda = time.monotonic()
        travado = False
        while proc.poll() is None:
            try:
                status = json.loads(meta_path.read_text(encoding="utf-8")).get("status")
            except (OSError, ValueError):
                status = None
            if status and status != last_status:
                last_status = status
                self.ui(lambda s=status: self._pill_processing(s))
            agora = time.monotonic()
            if agora - ultima_sonda >= _SONDA_PROGRESSO_S:
                ultima_sonda = agora
                novos = self._sinais_do_filho(proc.pid, meta_path, log_path)
                if _andou(sinais, novos):
                    sinais, ultimo_sinal = novos, agora
                elif agora - ultimo_sinal >= limite:
                    travado = True
                    break
            time.sleep(_TICK_PROCESSAMENTO_S)

        if travado:
            parado_min = (time.monotonic() - ultimo_sinal) / 60
            # foto da máquina ANTES de matar: é o único registro do instante real
            # do travamento - o Diagnóstico roda depois, com a carga já outra
            # (#182/#183: travas em CUDA chegavam sem GPU, driver nem VRAM)
            try:
                from . import sysprobe

                foto = sysprobe.snapshot_line()
            except Exception:
                foto = "(snapshot indisponivel)"
            log.error("processamento de %s TRAVADO: %.0f min sem progresso (CPU, meta.json e "
                      "process.log parados) - encerrando o subprocesso pid=%s · maquina no "
                      "momento: %s", folder.name, parado_min, proc.pid, foto)
            self._matar_filho(proc, folder)
            self.ui(self._hide_pill_if_processing)
            self._marcar_falha(
                meta_path, folder,
                f"processamento encerrado pelo ScribaDev: ficou {parado_min:.0f} min sem "
                f"nenhum sinal de progresso (nem CPU, nem log). Detalhes em process.log; "
                f"para tentar de novo: scribadev process")
            self._toast("ScribaDev: processamento travado",
                        f"{folder.name} parou de responder e foi encerrado - a fila seguiu.")
            return

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
            # componentes danificados (#170): rc próprio da CLI ou o texto do erro
            # denunciando o addons - o usuário precisa de conserto, não de retry
            damaged = proc.returncode == 4 or addons.looks_damaged_text(tail)
            # invariante: subprocesso saiu com erro => status terminal no meta
            self._marcar_falha(meta_path, folder,
                               addons.DAMAGED_HINT if damaged else tail[-600:])
            if damaged:
                log.error("componentes danificados: %s", addons.DAMAGED_HINT)
                self._toast("ScribaDev: componentes danificados",
                            "Repare em Configurações → Sobre para voltar a processar.")
            else:
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
                # o build_notes indexou a nota sob o nome ANTIGO da pasta; o índice é
                # chaveado pelo path, então reconcilia (remove a órfã, indexa o path novo)
                # senão a capa mostra a reunião com pasta quebrada / duplicada
                try:
                    from . import meetings_index

                    meetings_index.reindex_renamed(folder, renamed)
                except Exception:
                    log.exception("índice: falha ao reconciliar rename de %s", folder.name)
                folder = renamed
        # o poll da faixa viva pode ter disparado refresh_home ANTES do rename +
        # reindex_renamed acima (recentes/pendências com o path antigo, já morto):
        # refresh autoritativo da capa DEPOIS do índice reconciliado (#90)
        self.ui(self._refresh_home_after_process)
        # sugestão de apontamento (#118/#123): DEPOIS do rename, para gravar a pasta
        # final; dormente (#126) = nem importa o módulo. Falha nunca quebra o fluxo.
        ts = self.cfg.timesheet
        if ts.enabled and ts.suggest:
            try:
                from . import timesheet_suggest

                result = timesheet_suggest.suggest_for_folder(folder, ts)
                if result in ("created", "updated"):
                    # janela de Apontamentos aberta vê a sugestão no ato,
                    # sem precisar fechar e reabrir
                    self.refresh_timesheet()
                if result == "created":
                    try:
                        m = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        m = {}
                    who = (m.get("client") or "").strip() or "sem cliente"
                    self._toast("Apontamento sugerido",
                                f"{who} - {m.get('title') or folder.name}")
            except Exception:
                log.exception("sugestão de apontamento falhou para %s", folder.name)
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
                # gravação órfã de um crash: repara o header e adota; curta demais
                # ganha status TERMINAL (too_short, como no caminho vivo) - senão
                # ficava "recorded" eterna, virando "Na fila…" fantasma na capa
                duration = repair_folder(meta_path.parent)
                too_short = duration < self.cfg.detection.min_call_seconds
                meta["status"] = "too_short" if too_short else "recorded"
                meta["duration_seconds"] = round(duration, 1)
                meta["interrupted"] = True  # o notas.md avisa que a call foi cortada
                util.atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))
                log.info("gravação órfã adotada (%.0fs): %s", duration, meta_path.parent.name)
                if too_short:
                    continue
                status = "recorded"
            # além de recorded/transcribed, readota também quem morreu NO MEIO do
            # processamento (transcribing/diarizing/summarizing presos por crash ou
            # kill) - "failed" é terminal e não volta sozinho (retry: scriba process)
            # ...COM UMA exceção (#167): failed por EACCES no addons é vítima da
            # instalação de componentes, transitório por definição - readota
            retryable = (status == "failed" and
                         "addons" in str(meta.get("error") or "") and
                         "Permission denied" in str(meta.get("error") or ""))
            if retryable or status in ("recorded", "transcribed", "transcribing",
                                       "diarizing", "summarizing"):
                # não readota pasta com .lock ativo (PID vivo/recente): pode haver
                # um 'scriba process' manual em andamento sobre a mesma reunião
                if util.is_locked(meta_path.parent):
                    log.info("pendente pulada (.lock ativo): %s", meta_path.parent.name)
                    continue
                if retryable:
                    log.info("reunião falhada pela instalação de componentes readotada: %s",
                             meta_path.parent.name)
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
        if self._app is not None and not self.stop_event.is_set():
            self._after(6 * 3600 * 1000, self._purge_loop)

    def _migrate_export_dir_boot(self) -> None:
        """Boot: migração one-time das notas .md do antigo default (Documentos, que na
        maioria das máquinas fica no OneDrive) para o novo default LOCAL. Em thread —
        mover arquivos do OneDrive pode hidratar (lento), então nunca na thread da GUI.
        No-op depois da 1ª vez. Ao mover algo, atualiza a capa/notas."""
        def work() -> None:
            try:
                from . import notes

                n = notes.migrate_export_dir()   # faz o reindex completo se mover algo
                if n and self.main_win is not None:
                    self.ui(self.main_win.refresh_home)
            except Exception:
                log.exception("migração de notas no boot falhou")
            # só DEPOIS da migração (sem corrida no index.db): reconstrói se vazio (#12)
            try:
                from . import meetings_index

                n = meetings_index.reindex_if_needed(self.cfg.output.resolved_recordings_dir())
                log.info("índice de busca pronto: %d reunião(ões)", n)
            except Exception:
                log.exception("reindex de boot do índice de busca falhou")

        threading.Thread(target=work, daemon=True, name="migrate-notes").start()

    def _timesheet_boot(self) -> None:
        """Boot do timesheet (#123): com o módulo ativado (#126), aplica o override
        de caminho do banco, reconcilia sugestões que faltam (reuniões processadas
        com o app fechado, crash entre o done e o hook) e faz o backup diário.
        Dormente = no-op imediato, sem importar os módulos do timesheet."""
        ts = self.cfg.timesheet
        if not ts.enabled:
            return

        def work() -> None:
            try:
                from . import timesheet_db, timesheet_suggest

                timesheet_db.apply_config(ts)
                if ts.suggest:
                    n = timesheet_suggest.sync_pending(
                        self.cfg.output.resolved_recordings_dir(), ts)
                    if n:
                        self._toast("Apontamento de horas",
                                    f"{n} sugestão(ões) nova(s) para revisar")
                        self.refresh_timesheet()
                timesheet_db.backup_daily(ts)
            except Exception:
                log.exception("boot do timesheet falhou")

        threading.Thread(target=work, daemon=True, name="timesheet-boot").start()

    def _show_window_listener(self) -> None:
        """Espera o sinal de uma 2ª instância (atalho clicado) e mostra a janela."""

        def on_signal() -> None:
            log.info("ativado por uma 2ª instância (atalho) — mostrando a janela")
            self.show_main()

        plat.show_window_listener(self.stop_event, on_signal)

    # ----------------------------------------------------------------- run --

    def request_quit(self) -> None:
        self.stop_event.set()
        if self._app is not None:
            self.ui(self._app.quit)   # encerra o loop Qt na thread da GUI
        # rede de segurança: se a saída travar (quit engolido, thread da GUI ocupada,
        # teardown do Qt preso), força o encerramento — assim "Sair" não deixa o processo
        # pendurado (o sintoma de "clicar 3x"). Mesmo watchdog que o relaunch já usa. O
        # run() CANCELA este timer assim que o exec() volta, então uma gravação sendo
        # salva na saída limpa nunca é cortada por ele. daemon=True não atrasa a saída.
        self._quit_watchdog = threading.Timer(3.0, lambda: os._exit(0))
        self._quit_watchdog.daemon = True
        self._quit_watchdog.start()

    def run(self) -> int:
        # exceções não tratadas (em threads/slots) iam para o nada no modo sem console —
        # agora ficam no log E abrem um diálogo amigável (#22)
        sys.excepthook = lambda *exc: self._on_unhandled(exc, "geral")
        threading.excepthook = lambda args: self._on_unhandled(
            (args.exc_type, args.exc_value, args.exc_traceback),
            f"thread {args.thread.name if args.thread else '?'}",
        )

        from PySide6.QtWidgets import QApplication

        from .qt import theme
        from .qt.pump import UiPump

        self._app = QApplication.instance() or QApplication(sys.argv)
        # fechar uma janela (X) não encerra o app: ele segue na bandeja
        self._app.setQuitOnLastWindowClosed(False)
        theme.apply(self._app)
        self._pump = UiPump()
        self._pump.queue = self.ui_queue   # a pump drena a MESMA fila do self.ui(fn)
        self._pump.start()

        # Watchdog da GUI (freeze de 2026-07-08): heartbeat de 1s na thread da GUI; se
        # ela ficar >10s sem bater, o monitor despeja a pilha de TODAS as threads em
        # logs/hang.log — o próximo "não está respondendo" já nasce diagnosticado.
        from PySide6.QtCore import QTimer as _QTimer

        from .watchdog import GuiWatchdog

        self._watchdog = GuiWatchdog()
        self._wd_timer = _QTimer()
        self._wd_timer.timeout.connect(self._watchdog.beat)
        self._wd_timer.start(1000)
        self._watchdog.start()

        from .qt.main_window import MainWindow

        self.main_win = MainWindow(self)   # nasce oculta; abre pela bandeja/atalho

        from .qt.tray import Tray

        self.tray = Tray(self)
        self.tray.start()

        self.detector = Detector(
            self.cfg.detection,
            on_call_started=self._on_call_started,
            on_call_ended=self._on_call_ended,
            # feedback imediato ao desligar a call: a pílula avisa que está na tolerância
            on_grace=lambda: self.ui(self._pill_finishing),
            on_grace_cancel=lambda: self.ui(self._pill_resume),
            on_title=self._on_title,  # #35: preenche meeting_title tardio no meta
            is_recording=self.is_recording,  # #38: rearma o auto-record após ■
        )
        threading.Thread(target=self.detector.run, args=(self.stop_event,), daemon=True, name="detector").start()
        threading.Thread(target=self._worker, daemon=True, name="worker").start()
        threading.Thread(target=self._show_window_listener, daemon=True, name="activate").start()
        self._setup_hotkey()
        self.scan_pending()
        self._after(8000, self._purge_loop)  # limpeza de gravações antigas (e a cada 6 h)
        self._after(2500, self._maybe_offer_wizard)  # 1º uso: assistente de perfil
        # migração one-time das notas p/ pasta local + índice de busca (#12) encadeado
        self._after(5000, self._migrate_export_dir_boot)
        self._after(9000, self._check_updates_boot)    # aviso de nova versão (#19)
        self._after(12000, self._timesheet_boot)       # sugestões + backup diário (#123)

        if not self.start_hidden:
            self.show_main()  # lançamento manual (atalho): abre a janela na frente
        log.info("ScribaDev iniciado — monitorando calls do Teams")
        self._app.exec()

        # o exec() voltou: estamos na saída LIMPA. Cancela o watchdog do request_quit
        # antes de qualquer passo lento (salvar gravação), para ele não cortar o save.
        if self._quit_watchdog is not None:
            self._quit_watchdog.cancel()
        # o heartbeat (QTimer) morreu junto com o loop: para o monitor da GUI antes do
        # save final, senão ele registraria um falso "hang" durante a saída.
        self._watchdog.stop()
        # saída: salva gravação em andamento; pendências ficam para o próximo start
        if self.is_recording():
            self.stop_recording(False)
        self.tray.stop()
        log.info("ScribaDev encerrado")
        # os._exit pula o teardown do interpretador/Qt — que no PySide6 às vezes TRAVA
        # (deixando o processo pendurado após "Sair") ou segfalha. Mesmo padrão dos
        # harnesses das janelas; logging.shutdown() antes p/ não perder as últimas linhas.
        logging.shutdown()
        os._exit(0)
        return 0  # inalcançável (os._exit não retorna); mantém a assinatura -> int


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
    # ANTES do config.load() do ScribaApp, que cria o config.toml e apagaria a
    # única pista de que esta instalação já existia antes de os padrões de área
    # virarem neutros (#181).
    from . import notes

    notes.freeze_area_defaults()
    # Reinício pós-update (#74): a instância antiga pode ainda estar no teardown segurando
    # o mutex; damos alguns retries p/ ela liberar (o 2º-launch normal, do atalho, segue
    # instantâneo — sem retry). Marcado pela env var setada no PS do relaunch.
    relaunched = os.environ.get("SCRIBA_RELAUNCHED") == "1"
    if not plat.single_instance(retries=10 if relaunched else 0):
        # clique no atalho com o app já vivo: pede à instância ativa que abra a
        # janela principal (antes a 2ª instância morria muda e "nada acontecia")
        if plat.signal_show_window():
            print("ScribaDev já está rodando — abrindo a janela da instância ativa.")
        else:
            print("ScribaDev já está rodando (ícone na bandeja).")
        return 0
    return ScribaApp(start_hidden=minimized).run()

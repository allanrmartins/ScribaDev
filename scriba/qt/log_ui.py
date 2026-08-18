"""Janela de Log em PySide6 (fase B / #51). Porta `scriba/log_ui.py`.

Visualizador do scriba.log: erros em vermelho, avisos em âmbar; busca com N/M;
filtros (nível/dia/hora); Copiar / Abrir pasta / Exportar diagnóstico (.zip). A
leitura/parse/filtro (puro) fica em `diagnostics.py`; aqui é só a UI. Inclui o
diálogo de crash amigável (show_crash_dialog). API: LogWindow(app).show().
"""

from __future__ import annotations

import html as _html
import logging

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import diagnostics, util
from . import theme, widgets

log = logging.getLogger("scriba.qt.log_ui")
_MAX_LINES = 6000
_LEVELS_CYCLE = [("Tudo", 0), ("Avisos+", 30), ("Erros", 40)]


class LogWindow(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self._entries: list = []
        self._truncated = False
        self._hits: list[QTextCursor] = []
        self._hit_idx = -1
        self._level_idx = 0
        self._last_stat: tuple | None = None   # (mtime, size) do log no último _reload (#93)

        self.setWindowTitle("ScribaDev — Log")
        self.setMinimumSize(640, 380)
        widgets.remember_geometry(self, "qt_log", default=(120, 120, 960, 600))

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)

        bar = QHBoxLayout()
        t_lbl = QLabel("Log do app"); t_lbl.setStyleSheet(f"font-size:{theme.zpt(13)}pt; font-weight:bold;")
        bar.addWidget(t_lbl); bar.addStretch(1)
        # Copiar/Abrir pasta viram icon-only (mesmo padrão dos botões de busca): a barra
        # precisa caber no mínimo de 640px com os DOIS botões de texto (#64 guarda o corte)
        _cp = widgets.ModernButton("", self._copy)
        _cp.setIcon(theme.qicon("copy"))
        widgets.add_tooltip(_cp, "Copiar o log")
        bar.addWidget(_cp)
        _fo = widgets.ModernButton("", self._open_folder)
        _fo.setIcon(theme.qicon("folder"))
        widgets.add_tooltip(_fo, "Abrir a pasta do log")
        bar.addWidget(_fo)
        _exp = widgets.ModernButton("Diagnóstico", self._export)
        widgets.add_tooltip(_exp, "Exportar diagnóstico (.zip) para suporte")
        bar.addWidget(_exp)
        # reporte de 1 clique p/ QUALQUER erro (#157: mic/loopback falham só com um
        # toast — a janela de Log é o lugar p/ onde todo erro aponta). Rótulo curto
        # p/ caber no mínimo de 640px (#64); o tooltip carrega o sentido completo.
        _rep = widgets.ModernButton("Reportar", self._report_issue, kind="primary")
        widgets.add_tooltip(_rep, "Reportar erro no GitHub: salva o diagnóstico (.zip) e abre "
                                  "uma issue já preenchida com os últimos erros do log")
        bar.addWidget(_rep)
        root.addLayout(bar)

        row1 = QHBoxLayout()
        self._find = widgets.make_entry("buscar no log…")
        self._find.textChanged.connect(lambda: self._find_timer.start())
        self._find.returnPressed.connect(self._next_hit)
        row1.addWidget(self._find, 1)
        _prev = widgets.ModernButton("", self._prev_hit)
        _prev.setIcon(theme.qicon("chevron-up"))
        widgets.add_tooltip(_prev, "Ocorrência anterior")
        _next = widgets.ModernButton("", self._next_hit)
        _next.setIcon(theme.qicon("chevron-down"))
        widgets.add_tooltip(_next, "Próxima ocorrência")
        row1.addWidget(_prev)
        row1.addWidget(_next)
        self._count = QLabel(""); self._count.setProperty("role", "muted"); self._count.setFixedWidth(56)
        row1.addWidget(self._count)
        self._auto = widgets.AnimatedCheckBox("auto-atualizar"); self._auto.setChecked(True)
        row1.addWidget(self._auto)
        root.addLayout(row1)

        row2 = QHBoxLayout()
        self._level_btn = widgets.ModernButton("Nível: Tudo", self._cycle_level)
        row2.addWidget(self._level_btn)
        self._date = widgets.DateFilter("Dia")
        self._date.changed.connect(lambda: self._render_timer.start())
        row2.addWidget(self._date)
        self._time = widgets.TimeFilter("Hora ≥")
        self._time.changed.connect(lambda: self._render_timer.start())
        row2.addWidget(self._time)
        row2.addStretch(1)
        root.addLayout(row2)

        self._status = QLabel(""); self._status.setProperty("role", "muted")
        self._status.setStyleSheet(f"font-size:{theme.active().font_size_small}pt;")
        root.addWidget(self._status)

        self._view = QTextEdit(); self._view.setReadOnly(True)
        self._view.setLineWrapMode(QTextEdit.NoWrap)
        self._view.setStyleSheet(
            f"QTextEdit {{ background:{theme.active().code_bg}; color:{theme.active().text};"
            f" font-family:'{theme.active().font_mono}','Consolas',monospace; border:none; }}")
        root.addWidget(self._view, 1)

        self._find_timer = QTimer(self); self._find_timer.setSingleShot(True)
        self._find_timer.setInterval(250); self._find_timer.timeout.connect(self._apply_search)
        self._render_timer = QTimer(self); self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(250); self._render_timer.timeout.connect(self._render)
        self._tick_timer = QTimer(self); self._tick_timer.setInterval(2000); self._tick_timer.timeout.connect(self._tick)

        self._reload()

    # -- dados ---------------------------------------------------------------

    def _log_stat(self) -> tuple | None:
        """(mtime, size) do arquivo de log, ou None se não dá p/ ler. Barato: usado pelo
        tick p/ pular a releitura quando o arquivo não mudou (#93)."""
        try:
            st = diagnostics.LOG_FILE.stat()
            return (st.st_mtime, st.st_size)
        except OSError:
            return None

    def _reload(self, stick_bottom: bool = False) -> None:
        # registra o stat ANTES de ler: um append entre o stat e a leitura vira uma
        # releitura extra no próximo tick (inofensivo), nunca uma atualização perdida
        self._last_stat = self._log_stat()
        text = diagnostics.read_tail(diagnostics.LOG_FILE, _MAX_LINES)
        self._truncated = len(text.splitlines()) >= _MAX_LINES
        self._entries = diagnostics.parse_entries(text)
        self._render(stick_bottom=stick_bottom)

    def restyle_theme(self) -> None:
        """Troca a quente (#70): re-aplica os estilos com cor de tema (status + view) e
        re-renderiza o HTML (as cores das linhas são embutidas por _render, a partir do
        cache self._entries — não relê o arquivo)."""
        t = theme.active()
        self._status.setStyleSheet(f"font-size:{t.font_size_small}pt;")
        self._view.setStyleSheet(
            f"QTextEdit {{ background:{t.code_bg}; color:{t.text};"
            f" font-family:'{t.font_mono}','Consolas',monospace; border:none; }}")
        self._render()

    def _render(self, stick_bottom: bool = False) -> None:
        date_br = self._date.br()
        time_from = self._time.hhmm()
        level_min = _LEVELS_CYCLE[self._level_idx][1]
        shown = diagnostics.filter_entries(self._entries, level_min, date_br, time_from)

        t = theme.active()
        n_err = n_warn = 0
        parts = ['<pre style="margin:0">']
        for e in shown:
            num = diagnostics.level_num(e.get("level", ""))
            if num >= 40:
                color = t.code_err
                n_err += 1
            elif num == 30:
                color = t.warn
                n_warn += 1
            elif num <= 10:
                color = t.muted
            else:
                color = t.text
            parts.append(f'<span style="color:{color}">{_html.escape(e["text"])}</span>\n')
        parts.append("</pre>")
        self._view.setHtml("".join(parts))
        msg = f"{len(shown)} de {len(self._entries)} entradas · {n_err} erro(s) · {n_warn} aviso(s)"
        if self._truncated:
            msg += f" · últimas {_MAX_LINES} linhas — log completo em Abrir pasta"
        self._status.setText(msg)
        self._apply_search()
        if stick_bottom:
            # auto-atualizar acompanha o fim (tail): setHtml zera o scroll p/ o topo, então
            # rola tudo p/ baixo p/ os eventos mais novos ficarem à vista a cada atualização
            self._view.moveCursor(QTextCursor.End)
            self._view.ensureCursorVisible()

    # -- busca ---------------------------------------------------------------

    def _apply_search(self) -> None:
        self._view.setExtraSelections([])
        self._hits = []
        self._hit_idx = -1
        term = self._find.text().strip()
        if not term:
            self._count.setText("")
            return
        t = theme.active()
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(t.highlight)); fmt.setForeground(QColor(t.on_highlight))
        doc = self._view.document()
        cursor = QTextCursor(doc)
        sels = []
        while True:
            cursor = doc.find(term, cursor)
            if cursor.isNull():
                break
            self._hits.append(QTextCursor(cursor))
            sel = QTextEdit.ExtraSelection(); sel.cursor = cursor; sel.format = fmt
            sels.append(sel)
        self._view.setExtraSelections(sels)
        if self._hits:
            self._hit_idx = 0
            self._goto()
        else:
            self._count.setText("0/0")

    def _goto(self) -> None:
        if not self._hits:
            return
        self._view.setTextCursor(QTextCursor(self._hits[self._hit_idx]))
        self._view.ensureCursorVisible()
        self._count.setText(f"{self._hit_idx + 1}/{len(self._hits)}")

    def _next_hit(self) -> None:
        if self._hits:
            self._hit_idx = (self._hit_idx + 1) % len(self._hits); self._goto()

    def _prev_hit(self) -> None:
        if self._hits:
            self._hit_idx = (self._hit_idx - 1) % len(self._hits); self._goto()

    # -- filtros / ações -----------------------------------------------------

    def _cycle_level(self) -> None:
        self._level_idx = (self._level_idx + 1) % len(_LEVELS_CYCLE)
        self._level_btn.setText(f"Nível: {_LEVELS_CYCLE[self._level_idx][0]}")
        self._render()

    def _tick(self) -> None:
        # auto-atualizar marcado: recarrega e acompanha o fim (tail) a cada tick, p/
        # sincronizar os eventos ao vivo. Pausa durante uma busca (posições estáveis).
        # Desmarcar o auto = scroll livre (para inspecionar o histórico sem ser puxado).
        if not (self.isVisible() and self._auto.isChecked() and not self._find.text().strip()):
            return
        # não puxa o tapete de quem está selecionando texto p/ copiar (o setHtml zeraria
        # a seleção): pausa enquanto há seleção ativa, como a busca pausa (#93)
        if self._view.textCursor().hasSelection():
            return
        # só relê se o arquivo mudou: sem isto, o setHtml a cada 2s reconstruía um documento
        # idêntico (CPU) e ainda assim apagava seleção/scroll mesmo com o log parado (#93)
        stat = self._log_stat()
        if stat == self._last_stat:
            return
        self._reload(stick_bottom=True)

    def _copy(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self._view.toPlainText())
        self._status.setText("Conteúdo exibido copiado para a área de transferência.")

    def _open_folder(self) -> None:
        util.ensure_app_dirs()
        util.open_path(util.LOGS_DIR)

    def _export(self) -> None:
        try:
            rec = self.app.cfg.output.resolved_recordings_dir()
        except Exception:
            rec = None
        try:
            dest = diagnostics.export_zip(rec)
            self._status.setText(f"Diagnóstico salvo: {dest}")
            util.open_path(dest.parent)
        except Exception as e:
            log.exception("falha ao exportar diagnóstico")
            self._status.setText(f"Falha ao exportar: {e}")

    def _report_issue(self) -> None:
        """Reporte de 1 clique (#157 e afins): zip do diagnóstico + issue no GitHub
        pré-preenchida com as últimas entradas de ERRO do log (traceback junto)."""
        from .. import support

        try:
            rec = self.app.cfg.output.resolved_recordings_dir()
        except Exception:
            rec = None
        detail = diagnostics.error_tail(diagnostics.read_tail(diagnostics.LOG_FILE, _MAX_LINES))
        z = support.open_report("Erro em uso (reportado pela janela de Log)", detail, rec)
        self._status.setText(
            f"Abri a página da issue. Para anexar, arraste o arquivo {z.name} "
            "(deixei selecionado no gerenciador de arquivos)." if z else
            "Abri a página da issue — não consegui gerar o zip; descreva o cenário, por favor.")

    def show(self) -> None:  # noqa: A003
        super().show()
        self.raise_()
        self.activateWindow()
        widgets.enable_dark_titlebar(self)
        # abre no fim se seguindo ao vivo, MAS não se há busca ativa (o campo persiste
        # entre aberturas - closeEvent só esconde): grudar no fim jogaria o 1º hit p/
        # fora da tela, deixando o contador "1/N" apontando p/ um match invisível (#93)
        stick = self._auto.isChecked() and not self._find.text().strip()
        self._reload(stick_bottom=stick)
        self._tick_timer.start()

    def closeEvent(self, event) -> None:
        self._tick_timer.stop()
        event.ignore()
        self.hide()


# -- diálogo de crash amigável (#22) -----------------------------------------

_crash_win: QWidget | None = None  # 1 por vez (não empilha numa rajada de erros)


def show_crash_dialog(app, detail: str) -> None:
    """Diálogo "encontrou um erro" com traceback copiável + Abrir log / Exportar
    diagnóstico. Chamado pelos excepthooks (via app.ui, na thread da GUI)."""
    global _crash_win
    if _crash_win is not None:
        return
    try:
        win = QWidget()
        win.setWindowTitle("ScribaDev — erro inesperado")
        win.resize(680, 440)
        lay = QVBoxLayout(win)
        title = QLabel("O ScribaDev encontrou um erro inesperado")
        title.setStyleSheet(f"color:{theme.active().accent}; font-size:{theme.zpt(13)}pt; font-weight:bold;")
        lay.addWidget(title)
        sub = QLabel("O app continua rodando. Se isso atrapalhou uma gravação, exporte o "
                     "diagnóstico e me avise — o detalhe técnico está abaixo.")
        sub.setProperty("role", "muted"); sub.setWordWrap(True)
        lay.addWidget(sub)
        txt = QTextEdit(); txt.setReadOnly(True); txt.setPlainText(detail)
        txt.setStyleSheet(f"background:{theme.active().code_bg}; font-family:'Consolas',monospace;")
        lay.addWidget(txt, 1)

        def _copy():
            from PySide6.QtWidgets import QApplication

            QApplication.clipboard().setText(detail)

        def _open_log():
            try:
                app.show_log()
            except Exception:
                log.exception("crash dialog: falha ao abrir o log")

        def _export():
            try:
                rec = app.cfg.output.resolved_recordings_dir()
            except Exception:
                rec = None
            try:
                util.open_path(diagnostics.export_zip(rec).parent)
            except Exception:
                log.exception("crash dialog: falha ao exportar diagnóstico")

        def _issue():
            """Zip do diagnóstico + issue do GitHub pré-preenchida (o corpo pede o
            arraste do zip, que fica selecionado no gerenciador)."""
            from .. import support

            try:
                rec = app.cfg.output.resolved_recordings_dir()
            except Exception:
                rec = None
            try:
                support.open_report("Erro inesperado durante a execução", detail, rec)
            except Exception:
                log.exception("crash dialog: falha ao abrir o reporte de issue")

        def _close():
            global _crash_win
            _crash_win = None
            win.close()

        bar = QHBoxLayout(); bar.addStretch(1)
        bar.addWidget(widgets.ModernButton("Copiar erro", _copy))
        bar.addWidget(widgets.ModernButton("Abrir log", _open_log))
        bar.addWidget(widgets.ModernButton("Exportar diagnóstico", _export))
        bar.addWidget(widgets.ModernButton("Reportar no GitHub", _issue, kind="primary"))
        bar.addWidget(widgets.ModernButton("Fechar", _close))
        lay.addLayout(bar)

        win.destroyed.connect(lambda: _reset_crash())
        _crash_win = win
        win.show()
        widgets.enable_dark_titlebar(win)
    except Exception:
        _crash_win = None
        log.exception("falha ao montar o diálogo de crash")


def _reset_crash() -> None:
    global _crash_win
    _crash_win = None


# --------------------------------------------------------------- harness ------

def main() -> int:
    import sys

    from PySide6.QtWidgets import QApplication

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    app = QApplication(sys.argv)
    theme.apply(app)

    class _App:
        cfg = type("C", (), {"output": type("O", (), {"resolved_recordings_dir": staticmethod(lambda: None)})()})()

    win = LogWindow(_App())
    win.resize(960, 600)
    win.show()
    return app.exec()


if __name__ == "__main__":
    import os

    rc = main()
    logging.shutdown()
    os._exit(rc)

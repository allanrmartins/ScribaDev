"""Tela "O que há de novo" (#228): abre sozinha na 1ª vez depois de um update e pode
ser reaberta pelo menu (Novidades) e pela aba Sobre. Conteúdo em scriba/novidades.py,
já filtrado pelo sistema."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from .. import __version__
from . import theme, widgets

RELEASES_URL = "https://github.com/allanrmartins/ScribaDev/releases"


def _label(texto: str, *, role: str | None = None, size: float | None = None,
           bold: bool = False, wrap: bool = True) -> QLabel:
    lb = QLabel(texto)
    lb.setWordWrap(wrap)
    lb.setTextFormat(Qt.PlainText)
    if role:
        lb.setProperty("role", role)
    css = []
    if size:
        css.append(f"font-size:{theme.zpt(size)}pt;")
    if bold:
        css.append("font-weight:600;")
    if css:
        lb.setStyleSheet(" ".join(css))
    return lb


class NovidadesWindow(QWidget):
    """Lista as versões recebidas (mais nova primeiro): destaques em cartão, depois
    melhorias e correções em linhas curtas."""

    def __init__(self, versoes: list[dict], on_abrir=None, parent=None):
        """`on_abrir(aba)`: abre as Configurações na aba do destaque (botão Experimentar)."""
        super().__init__(parent)
        self._on_abrir = on_abrir
        self.setWindowTitle("ScribaDev — O que há de novo")
        self.setWindowFlag(Qt.Window, True)
        self.resize(600, 680)
        t = theme.active()

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 18)
        root.setSpacing(14)

        topo = QHBoxLayout()
        icone = QLabel()
        icone.setPixmap(theme.qicon("sparkle", color=t.accent).pixmap(28, 28))
        topo.addWidget(icone, 0, Qt.AlignTop)
        titulos = QVBoxLayout()
        titulos.setSpacing(2)
        titulos.addWidget(_label("O que há de novo", size=17, bold=True))
        if len(versoes) == 1:
            sub = f"Versão {versoes[0]['versao']} · {versoes[0].get('data', '')}".rstrip(" ·")
        else:
            sub = f"{len(versoes)} versões desde a sua última atualização · agora na {__version__}"
        titulos.addWidget(_label(sub, role="muted"))
        topo.addLayout(titulos, 1)
        root.addLayout(topo)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        corpo = QWidget()
        col = QVBoxLayout(corpo)
        col.setContentsMargins(0, 0, 6, 0)
        col.setSpacing(12)
        for i, versao in enumerate(versoes):
            if len(versoes) > 1:
                if i:
                    col.addSpacing(8)
                col.addWidget(_label(f"Versão {versao['versao']}", size=11, bold=True))
            for d in versao["destaques"]:
                col.addWidget(self._cartao(d, t))
            self._lista(col, "Melhorias", versao["melhorias"], "muted")
            self._lista(col, "Correções", versao["correcoes"], "muted")
        col.addStretch(1)
        scroll.setWidget(corpo)
        root.addWidget(scroll, 1)

        rodape = QHBoxLayout()
        link = QLabel(f'<a href="{RELEASES_URL}">Todas as mudanças no GitHub</a>')
        link.setOpenExternalLinks(True)
        rodape.addWidget(link)
        rodape.addStretch(1)
        rodape.addWidget(widgets.ModernButton("Entendi", self.close, kind="primary"))
        root.addLayout(rodape)

    def _cartao(self, d: dict, t) -> QFrame:
        card = QFrame()
        card.setObjectName("novCard")
        card.setStyleSheet(f"QFrame#novCard {{ background:{t.surface}; border:1px solid {t.accent};"
                           f" border-radius:{t.radius + 2}px; }}")
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(6)
        v.addWidget(_label(d["titulo"], size=12.5, bold=True))
        v.addWidget(_label(d["texto"]))
        if d.get("abrir") or d.get("onde"):
            acao = QHBoxLayout()
            acao.setSpacing(10)
            if d.get("abrir") and self._on_abrir is not None:
                aba = d["abrir"]
                acao.addWidget(widgets.ModernButton("Experimentar", lambda a=aba: self._on_abrir(a)))
            if d.get("onde"):
                acao.addWidget(_label(d["onde"], role="muted"), 1)
            else:
                acao.addStretch(1)
            v.addSpacing(2)
            v.addLayout(acao)
        return card

    @staticmethod
    def _lista(col, titulo: str, itens: list[dict], role: str) -> None:
        if not itens:
            return
        col.addSpacing(4)
        col.addWidget(_label(titulo, bold=True))
        for item in itens:
            linha = QHBoxLayout()
            linha.setSpacing(8)
            linha.addWidget(_label("•", role=role, wrap=False), 0, Qt.AlignTop)
            linha.addWidget(_label(item["texto"]), 1)
            col.addLayout(linha)

    def show(self) -> None:  # noqa: A003
        super().show()
        widgets.bring_to_front(self)
        widgets.enable_dark_titlebar(self)

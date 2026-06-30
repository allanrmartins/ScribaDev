# -*- coding: utf-8 -*-
"""Gera as screenshots do README (docs/*.png) com DADOS DE DEMONSTRAÇÃO.

Abre as janelas reais do Scriba (dashboard, Notas, Configurações) alimentadas por
um app fake e notas fictícias — nada da máquina/clientes reais vaza para o GitHub.
Captura via PrintWindow (funciona até com a tela bloqueada). As janelas piscam na
tela por alguns segundos durante a captura.

Uso (no venv do Scriba):  python tools/make_screenshots.py
"""

from __future__ import annotations

import ctypes
import json
import queue
import shutil
import sys
import tempfile
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
DOCS = PROJECT / "docs"

# ----------------------------------------------------------- dados de demo ----

NOTA_VITRINE = """\
---
titulo: Boleto não gerado na fatura ZSD230
cliente: Aurora
data: 2026-06-12T14:02:00
fim: 2026-06-12T14:49:00
duracao_minutos: 47
origem: scriba
whisper: large-v3-turbo (cuda)
---

# Boleto não gerado na fatura ZSD230

*2026-06-12 14:02 · 47 min · Cliente: Aurora*

> **Contexto para IA:** registro técnico de uma reunião SAP/ABAP, derivado de transcrição. **Objetivo** declara o tipo de atividade e o que fazer — execute ESSA atividade. **Detalhamento** e **Regras de negócio** são a fonte da verdade; **Pendências e Ações** lista o que ainda não está definido. A *Transcrição completa* ao final é apenas backup de rastreabilidade.

## Objetivo

**Tipo:** análise/debug

Identificar por que faturas à vista da ZSD230 saem sem boleto desde a entrada do pacote de notas 2024. Erro reportado em produção pelo faturamento [00:01:30].

## Contexto

SD/FI, S/4HANA 2022 FPS01, mandante 300 (produção). O boleto é gerado por job noturno a partir das faturas do dia; clientes à vista reclamaram da ausência do PDF [00:03:05].

## Detalhamento

Sintoma: VF03 mostra a fatura contabilizada, mas o boleto não é emitido — sem mensagem de erro no job [00:04:18].

Já verificado na call:

1. Job `ZSD_BOLETO_NOTURNO` executou sem cancelamento (SM37) [00:05:02];
2. A fatura 90004512 tem `VBRK-ZLSCH` vazio — faturas com forma de pagamento preenchida geram boleto normalmente [00:07:40];
3. Na VA03, a ordem de origem também está sem forma de pagamento [00:09:12].

Próximos pontos de investigação:

1. Debug da determinação da forma de pagamento na criação da ordem (user exit `MV45AFZZ`) [00:11:25];
2. Conferir se a rotina Z lê o novo campo de condição de pagamento à vista criado no pacote 2024 [00:13:50].

### Critérios de aceite

- [ ] Fatura à vista de teste gera boleto no job noturno
- [ ] Causa raiz documentada no chamado
- [x] Passos de reprodução validados em QAS

## Regras de negócio

- RN-01: Boleto só é emitido quando `VBRK-ZLSCH = B` (boleto); a forma de pagamento desce da ordem de venda para a fatura — 1. Determinar na criação da ordem; 2. Copiar para a fatura na VF01; 3. Job noturno seleciona por ZLSCH [00:08:30].
- RN-02: Cliente à vista sem cadastro de forma de pagamento usa o padrão da área de vendas, exceto quando o campo Z de condição à vista está ativo [00:14:05].

## Objetos SAP citados

| Tipo | Objeto | Observação | Quando |
|---|---|---|---|
| transação | VF03 | exibição da fatura sem boleto | [00:04:18] |
| tabela | VBRK | cabeçalho de fatura — campo ZLSCH vazio | [00:07:40] |
| programa | ZSD_BOLETO_NOTURNO | job que emite os boletos | [00:05:02] |
| user exit | MV45AFZZ | determinação da forma de pagamento na ordem | [00:11:25] |
| tabela | KNB1 | forma de pagamento do cliente por empresa | [00:12:10] |

## Decisões

- Reproduzir primeiro em QAS com cópia da ordem de produção, antes de qualquer correção — evita mexer às cegas na exit [00:16:40].

## Pendências e Ações

- Mariana envia a lista de faturas afetadas de junho (responsável: funcional) [00:18:22].
- Confirmar com o fiscal se o campo Z de condição à vista entrou no escopo do pacote 2024 [00:19:01].

## Participantes

- Participante 1 — Mariana, funcional SD
- Eu — desenvolvedor ABAP

---

## Transcrição completa

**[00:00:12] Participante 1:** Bom dia! Conseguiu ver o chamado do boleto?

**[00:00:45] Eu:** Vi sim. Pelo que entendi a fatura contabiliza normal, o que falta é o boleto, certo?

**[00:01:30] Participante 1:** Isso, desde a virada do pacote as faturas à vista saem sem boleto e o faturamento abriu chamado urgente.
"""

NOTA_2 = """\
---
titulo: Relatório ALV de saldo por depósito
cliente: Horizonte Foods
data: 2026-06-12T09:30:00
fim: 2026-06-12T10:02:00
duracao_minutos: 32
origem: scriba
whisper: large-v3-turbo (cuda)
---

# Relatório ALV de saldo por depósito

*2026-06-12 09:30 · 32 min · Cliente: Horizonte Foods*

## Objetivo

**Tipo:** desenvolvimento

Criar relatório ALV de saldo por material e depósito com corte por data.

## Contexto

MM, ECC 6.0 EhP8. Substitui planilha manual do almoxarifado.

## Detalhamento

Selecionar MARD por centro/depósito; saldo via MBEW. Tela de seleção: centro (obrigatório), depósito, grupo de mercadorias.

### Critérios de aceite

- [ ] Saldo bate com a MMBE para os casos de teste

## Participantes

- Participante 1 — Carlos, almoxarifado

---

## Transcrição completa

**[00:00:10] Participante 1:** A planilha de saldo virou um problema...
"""

NOTA_3 = """\
---
titulo: Estimativa interface de frete EDI
cliente: Aurora
data: 2026-06-11T16:00:00
fim: 2026-06-11T16:41:00
duracao_minutos: 41
origem: scriba
whisper: large-v3-turbo (cuda)
---

# Estimativa interface de frete EDI

*2026-06-11 16:00 · 41 min · Cliente: Aurora*

## Objetivo

**Tipo:** estimativa

Levantar insumos para estimar a interface de frete via EDI com a transportadora.

## Participantes

- Participante 1 — Renata, logística

---

## Transcrição completa

**[00:00:08] Participante 1:** Precisamos da estimativa até sexta...
"""

NOTA_4 = """\
---
titulo: Alinhamento sprint 14
cliente:
data: 2026-06-11T11:00:00
fim: 2026-06-11T11:28:00
duracao_minutos: 28
origem: scriba
whisper: large-v3-turbo (cuda)
---

# Alinhamento sprint 14

*2026-06-11 11:00 · 28 min*

## Objetivo

**Tipo:** alinhamento

Revisão do andamento da sprint e replanejamento das entregas.

---

## Transcrição completa

**[00:00:05] Participante 1:** Vamos começar pelo backlog...
"""


def build_demo_dirs() -> tuple[Path, Path]:
    base = Path(tempfile.gettempdir()) / "scriba_demo"
    shutil.rmtree(base, ignore_errors=True)
    notes = base / "notas"
    rec = base / "gravacoes"
    notes.mkdir(parents=True)
    rec.mkdir(parents=True)
    (notes / "2026-06-12_14-02_reuniao.md").write_text(NOTA_VITRINE, encoding="utf-8")
    (notes / "2026-06-12_09-30_reuniao.md").write_text(NOTA_2, encoding="utf-8")
    (notes / "2026-06-11_16-00_reuniao.md").write_text(NOTA_3, encoding="utf-8")
    (notes / "2026-06-11_11-00_reuniao.md").write_text(NOTA_4, encoding="utf-8")
    # uma reunião ainda processando (aparece com ⏳ e barra de progresso)
    pend = rec / "2026" / "06" / "12" / "11-55"
    pend.mkdir(parents=True)
    (pend / "meta.json").write_text(json.dumps({
        "status": "summarizing",
        "started_at": "2026-06-12T11:55:00",
        "duration_seconds": 1860.0,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return notes, rec


# ------------------------------------------------------------- app de demo ----


class FakeApp:
    """O mínimo que as janelas pedem de ScribaApp, com uma call do Meet 'ao vivo'."""

    def __init__(self, root: tk.Tk, cfg):
        self.root = root
        self.cfg = cfg
        self.call_active = True
        self._ui_q: queue.Queue = queue.Queue()

    def ui(self, fn) -> None:
        self._ui_q.put(fn)

    def pump(self, seconds: float) -> None:
        """Drena a fila de UI e processa eventos Tk por N segundos."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                while True:
                    self._ui_q.get_nowait()()
            except queue.Empty:
                pass
            self.root.update()
            time.sleep(0.03)

    # estado de call/gravação exibido no dashboard
    def is_recording(self) -> bool:
        return True

    def current_call_app(self) -> str:
        return "Meet"

    def recording_duration(self) -> float:
        return 12 * 60 + 34

    def call_duration(self) -> float:
        return 12 * 60 + 34

    def detection_label(self) -> str:
        from scriba.detector import browser_patterns_from, friendly_name, patterns_from, title_patterns_from

        names = [friendly_name(p) for p in patterns_from(self.cfg.detection)]
        if browser_patterns_from(self.cfg.detection):
            names += [friendly_name(p) for p in title_patterns_from(self.cfg.detection)] or ["navegador"]
        return ", ".join(dict.fromkeys(names))

    # botões que não serão clicados na captura
    def show_settings(self) -> None: ...
    def show_notes(self) -> None: ...
    def start_recording(self, source: str = "manual") -> None: ...
    def stop_recording(self, **kw) -> None: ...
    def reload_config(self) -> None: ...


# ------------------------------------------------------ captura (PrintWindow) --

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32
_dwmapi = ctypes.windll.dwmapi

_GA_ROOT = 2
_PW_RENDERFULLCONTENT = 2
_DWMWA_EXTENDED_FRAME_BOUNDS = 9
_BI_RGB = 0
_DIB_RGB_COLORS = 0


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


def shoot(win: tk.Misc, out_path: Path) -> None:
    """Captura a janela top-level de `win` (moldura incluída, sem a sombra)."""
    from PIL import Image

    win.update_idletasks()
    hwnd = _user32.GetAncestor(win.winfo_id(), _GA_ROOT)
    wrect = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(wrect))
    w, h = wrect.right - wrect.left, wrect.bottom - wrect.top

    hdc = _user32.GetWindowDC(hwnd)
    mem = _gdi32.CreateCompatibleDC(hdc)
    bmp = _gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = _gdi32.SelectObject(mem, bmp)
    try:
        if not _user32.PrintWindow(hwnd, mem, _PW_RENDERFULLCONTENT):
            raise RuntimeError("PrintWindow falhou")
        bi = _BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bi.biWidth, bi.biHeight = w, -h  # top-down
        bi.biPlanes, bi.biBitCount, bi.biCompression = 1, 32, _BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        if not _gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), _DIB_RGB_COLORS):
            raise RuntimeError("GetDIBits falhou")
        img = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
    finally:
        _gdi32.SelectObject(mem, old)
        _gdi32.DeleteObject(bmp)
        _gdi32.DeleteDC(mem)
        _user32.ReleaseDC(hwnd, hdc)

    # recorta a borda invisível de redimensionamento (GetWindowRect vs frame visível)
    ext = wintypes.RECT()
    if _dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS,
                                     ctypes.byref(ext), ctypes.sizeof(ext)) == 0:
        img = img.crop((ext.left - wrect.left, ext.top - wrect.top,
                        ext.right - wrect.left, ext.bottom - wrect.top))
    img.save(out_path)
    print(f"  {out_path.relative_to(PROJECT)}  ({img.width}x{img.height})")


# ----------------------------------------------------------------- cenários ----


def main() -> int:
    notes_dir, rec_dir = build_demo_dirs()

    import dataclasses

    from scriba import config as config_mod
    from scriba import util
    from scriba.config import Config

    demo_cfg = Config(
        detection=config_mod.Detection(),
        audio=config_mod.Audio(),
        whisper=config_mod.Whisper(hotwords="SAP ABAP BAPI BAdI CDS RAP Fiori OData ALV IDoc"),
        diarization=config_mod.Diarization(enabled=True, hf_token="hf_demo"),
        summary=config_mod.Summary(),
        ui=config_mod.Ui(hotkey="ctrl+alt+r"),
        output=config_mod.Output(export_dir=str(notes_dir), recordings_dir=str(rec_dir)),
    )
    # as janelas que releem o disco passam a ver a config/prompt de demonstração
    config_mod.load = lambda: demo_cfg
    util.PROMPT_PATH = Path(tempfile.gettempdir()) / "scriba_demo" / "prompt.md"

    root = tk.Tk()
    root.withdraw()
    app = FakeApp(root, demo_cfg)

    print("Capturando (as janelas vão piscar na tela)...")

    # 1) dashboard — call do Meet ao vivo + serviços reais da máquina
    from scriba.main_window import MainWindow

    mw = MainWindow(root, app)
    root.geometry("560x640+80+60")
    mw.show()
    app.pump(5.0)  # espera o status (probe de áudio em subprocesso) chegar
    shoot(root, DOCS / "janela.png")
    mw.hide()

    # 2) janela de Notas — nota com cliente identificado + reunião processando
    from scriba.notes_ui import NotesWindow

    nw = NotesWindow(root, app)
    nw.win.geometry("1060x640+100+80")
    nw.show()
    app.pump(1.5)
    shoot(nw.win, DOCS / "notas.png")
    nw.hide()

    # 4) Assistente de perfil — formulário preenchido + prévia por modelo pronto
    from scriba import promptgen
    from scriba.wizard_ui import WizardWindow

    ww = WizardWindow(root, app)
    ww.win.geometry("820x700+140+50")
    ww.base_var.set(promptgen.BASE_LABELS["pm"])
    ww.role_var.set("Gerente de projetos de TI")
    ww.area_var.set("implantação de sistemas, varejo")
    ww.stack_var.set("Jira, MS Project, Teams")
    ww.jargon_text.insert("1.0", "sprint, baseline, go-live, steering, stakeholder, marco")
    ww.musthave_text.insert("1.0", "ações com responsável e prazo, riscos, decisões")
    ww.show()
    ww._generate_template()
    app.pump(0.8)
    shoot(ww.win, DOCS / "wizard.png")
    ww.hide()

    # 3) Configurações — aba Resumo com o editor do prompt.md
    from scriba.settings_ui import SettingsWindow

    # o rótulo "Salvar grava em ..." (lido no construtor) mostra o caminho real;
    # o conteúdo carregado (em show/_load_fields) continua vindo do prompt demo
    demo_prompt = util.PROMPT_PATH
    util.PROMPT_PATH = util.APP_DIR / "prompt.md"
    sw = SettingsWindow(root, app)
    util.PROMPT_PATH = demo_prompt
    sw.show()
    sw.win.geometry("860x620+120+100")  # após show(): sobrepõe o auto-fit à tela (#20)
    sw.nb.select(sw.tab_ia)
    app.pump(1.2)
    shoot(sw.win, DOCS / "configuracoes.png")
    sw.hide()

    root.destroy()
    print("Prontas. Confira as imagens em docs/ antes de commitar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

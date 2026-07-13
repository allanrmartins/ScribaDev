"""Exportação Excel do apontamento de horas (épico #118, #122).

Gera um arquivo NOVO no layout da planilha pessoal de apontamento (colunas A-M,
uma aba por mês) — a planilha original nunca é tocada; se o nome já existe,
sufixa _2, _3... Layout conferido contra a planilha real em 2026-07-13:
cabeçalhos, formatos (A d-mmm, horas h:mm), fórmula D =C-B, marca de hora extra
"hora extra" na coluna H e dropdowns (I: Base/In Loco/Remoto, J: SIM/NÃO).

Generalizações deliberadas sobre o original:
- K (Total dia) sai só na ÚLTIMA linha de cada dia, somando o intervalo exato do
  dia — o original usa janelas fixas de 2 linhas sobrepostas, que só fazem
  sentido em dias com exatamente 2 blocos;
- L/M (Ñ Apontado / Apontados) viram células-resumo do MÊS na linha 2, com
  SUMIF de coluna inteira, em formato [h]:mm (totais de mês passam de 24 h).

Só apontamentos `confirmed` entram; `day_notes` (feriado etc.) viram linha com
data + nota na coluna Cliente, sem horas. A importação do histórico (fase 2,
#125) morará neste mesmo módulo.

Dependência: openpyxl (puro Python; a stdlib não escreve xlsx) — a única lib
nova do épico, também usada para LER na importação da fase 2.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from . import timesheet_db

log = logging.getLogger("scriba.timesheet_xlsx")

# -- layout (posições/fórmulas num lugar só; ajustar aqui se a planilha mudar) --

_HEADERS = ("Data", "Início", "Fim", "Total", "Cliente", "Projeto", "Descrição",
            "Responsavel", "Local", None, "Total dia", "Ñ Apontado ", "Apontados")
(_C_DATA, _C_INICIO, _C_FIM, _C_TOTAL, _C_CLIENTE, _C_PROJETO, _C_DESCRICAO,
 _C_RESP, _C_LOCAL, _C_APONTADO, _C_TOTAL_DIA, _C_NAO_APONT, _C_APONTADOS) = range(1, 14)

_OVERTIME_MARK = "hora extra"          # texto dominante da coluna H na planilha real
_FMT_DATA = "d-mmm"
_FMT_HORA = "h:mm"
_FMT_DUR = "h:mm"                      # D e K, como no original (blocos/dias < 24 h)
_FMT_DUR_MES = "[h]:mm"                # L/M: totais de mês passam de 24 h
_DV_LOCAL = '"Base,In Loco,Remoto"'
_DV_SIM_NAO = '"SIM,NÃO"'
_WIDTHS = {_C_DATA: 8, _C_INICIO: 7, _C_FIM: 7, _C_TOTAL: 7, _C_CLIENTE: 16,
           _C_PROJETO: 14, _C_DESCRICAO: 55, _C_RESP: 12, _C_LOCAL: 9,
           _C_APONTADO: 6, _C_TOTAL_DIA: 9, _C_NAO_APONT: 11, _C_APONTADOS: 11}


def _default_export_dir() -> Path:
    from . import config  # lazy: config.load() cria APP_DIR/config.toml

    ts = config.load().timesheet
    if ts.export_dir:
        return Path(ts.export_dir).expanduser()
    return Path.home() / "Documents" / "ScribaDev" / "Apontamentos"


def _unique_path(path: Path) -> Path:
    """Nunca sobrescreve: Apontamento_X.xlsx -> _2, _3... se já existir."""
    if not path.exists():
        return path
    for n in range(2, 1000):
        cand = path.with_stem(f"{path.stem}_{n}")
        if not cand.exists():
            return cand
    raise FileExistsError(f"sem nome livre para {path}")


def _hhmm_to_time(hhmm: str):
    return datetime.strptime(hhmm, "%H:%M").time()


def export_month(month: str, dest: Path | None = None) -> Path:
    """Exporta os apontamentos confirmados de `month` ('AAAA-MM') para um xlsx novo.

    `dest` é a PASTA de destino (None = [timesheet].export_dir do config, vazio =
    Documentos/ScribaDev/Apontamentos). Devolve o caminho gerado. ValueError se o
    mês é inválido ou não tem nada confirmado (nem dia especial) para exportar.
    """
    try:
        datetime.strptime(month, "%Y-%m")
    except ValueError:
        raise ValueError(f"mês inválido (use AAAA-MM): {month!r}") from None
    entries = timesheet_db.list_entries(month=month, status="confirmed")
    notes = timesheet_db.day_notes_month(month)
    if not entries and not notes:
        raise ValueError(f"nenhum apontamento confirmado em {month}")

    dest_dir = Path(dest) if dest is not None else _default_export_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = _unique_path(dest_dir / f"Apontamento_{month}.xlsx")

    wb = Workbook()
    ws = wb.active
    ws.title = month
    bold = Font(bold=True)
    for col, header in enumerate(_HEADERS, start=1):
        if header:
            cell = ws.cell(row=1, column=col, value=header)
            cell.font = bold
        ws.column_dimensions[get_column_letter(col)].width = _WIDTHS[col]
    ws.freeze_panes = "A2"

    # agrupa por dia (entries já vêm ordenadas por dia/hora); dias especiais juntos
    by_day: dict[str, list[dict]] = {}
    for e in entries:
        by_day.setdefault(e["work_date"], []).append(e)

    row = 2
    for day in sorted(set(by_day) | set(notes)):
        day_val = date.fromisoformat(day)
        first = row
        for e in by_day.get(day, ()):
            ws.cell(row=row, column=_C_DATA, value=day_val).number_format = _FMT_DATA
            ws.cell(row=row, column=_C_INICIO,
                    value=_hhmm_to_time(e["start_time"])).number_format = _FMT_HORA
            ws.cell(row=row, column=_C_FIM,
                    value=_hhmm_to_time(e["end_time"])).number_format = _FMT_HORA
            ws.cell(row=row, column=_C_TOTAL,
                    value=f"=C{row}-B{row}").number_format = _FMT_DUR
            ws.cell(row=row, column=_C_CLIENTE,
                    value=e["client_name"] or e["client_text"] or None)
            ws.cell(row=row, column=_C_PROJETO,
                    value=e["project_code"] or e["project_text"] or None)
            ws.cell(row=row, column=_C_DESCRICAO, value=e["description"] or None)
            if e["overtime"]:
                ws.cell(row=row, column=_C_RESP, value=_OVERTIME_MARK)
            if e["location"]:
                ws.cell(row=row, column=_C_LOCAL, value=e["location"])
            ws.cell(row=row, column=_C_APONTADO,
                    value="SIM" if e["posted"] else "NÃO")
            row += 1
        if day in by_day:  # Total dia na última linha do dia (dias de 1, 2 ou N blocos)
            ws.cell(row=row - 1, column=_C_TOTAL_DIA,
                    value=f"=SUM(D{first}:D{row - 1})").number_format = _FMT_DUR
        if day in notes:   # feriado/férias: linha só com a data e a nota
            ws.cell(row=row, column=_C_DATA, value=day_val).number_format = _FMT_DATA
            ws.cell(row=row, column=_C_CLIENTE, value=notes[day])
            row += 1

    last = row - 1
    ws.cell(row=2, column=_C_NAO_APONT,
            value='=SUMIF(J:J,"NÃO",D:D)').number_format = _FMT_DUR_MES
    ws.cell(row=2, column=_C_APONTADOS,
            value='=SUMIF(J:J,"SIM",D:D)').number_format = _FMT_DUR_MES

    dv_local = DataValidation(type="list", formula1=_DV_LOCAL, allow_blank=True)
    dv_apontado = DataValidation(type="list", formula1=_DV_SIM_NAO, allow_blank=True)
    ws.add_data_validation(dv_local)
    ws.add_data_validation(dv_apontado)
    col_i, col_j = get_column_letter(_C_LOCAL), get_column_letter(_C_APONTADO)
    dv_local.add(f"{col_i}2:{col_i}{last}")
    dv_apontado.add(f"{col_j}2:{col_j}{last}")

    wb.save(out)
    log.info("timesheet: mês %s exportado para %s", month, out)
    return out

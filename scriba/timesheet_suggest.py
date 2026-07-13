"""Motor de sugestões do apontamento de horas (épico #118, #120).

Transforma reunião processada (`status == 'done'` no meta.json) em apontamento
`suggested` no timesheet.db, pré-preenchido com cliente, horários arredondados e
descrição (título da ata). O usuário revisa na UI: aceita, edita ou descarta.

Detecção sem polling: o app chama `suggest_for_folder` no fim do processamento
(hook da #123) e `sync_pending` na varredura de boot/CLI — os dois caminhos são
idempotentes porque o dedupe vive no banco (índice único em meeting_started_at,
ver timesheet_db.upsert_suggestion e a regra do reprocesso).

DORMÊNCIA (#126): importar este módulo não cria nada. Quando chamado SEM config
explícita, o motor se auto-bloqueia com [timesheet].enabled = false — defesa em
profundidade além do gate dos pontos de integração.

`suggest_for_folder`/`sync_pending` NUNCA levantam (padrão index_meeting): falha
do timesheet não pode quebrar o fluxo do app.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from . import timesheet_db

log = logging.getLogger("scriba.timesheet_suggest")

_MODES = ("nearest", "down", "up")
_LAST_MINUTE = 23 * 60 + 59  # 23:59 — teto de qualquer horário sugerido

# Sinalização de fim clampado (call cruzou a meia-noite): visível na descrição
# para o usuário revisar; caso raro, não vale modelar dois apontamentos sozinho.
_MIDNIGHT_FLAG = "[call cruzou a meia-noite - revisar fim]"


def round_hhmm(hhmm: str, step_min: int, mode: str = "nearest") -> str:
    """Arredonda um 'HH:MM' para passos de `step_min` minutos (0 = sem arredondar).

    `nearest` desempata para cima (14:07:30 do relógio já virou 14:07 antes de
    chegar aqui; 7,5 min → 15). Resultado clampado em 23:59 — nunca vira 24:00.
    """
    if mode not in _MODES:
        raise ValueError(f"mode inválido: {mode!r} (use nearest|down|up)")
    t = datetime.strptime(hhmm, "%H:%M")
    total = t.hour * 60 + t.minute
    step = int(step_min)
    if step > 0:
        q, r = divmod(total, step)
        if mode == "down":
            total = q * step
        elif mode == "up":
            total = (q + (1 if r else 0)) * step
        else:  # nearest, meio-a-meio sobe
            total = (q + (1 if r * 2 >= step else 0)) * step
    total = min(total, _LAST_MINUTE)
    return f"{total // 60:02d}:{total % 60:02d}"


def _add_minutes(hhmm: str, minutes: int) -> str:
    t = datetime.strptime(hhmm, "%H:%M")
    total = min(t.hour * 60 + t.minute + max(1, minutes), _LAST_MINUTE)
    return f"{total // 60:02d}:{total % 60:02d}"


def suggestion_from_meta(meta: dict, ts_cfg) -> dict | None:
    """Monta o dict de sugestão a partir de um meta.json. PURA: sem IO/banco.

    None quando a reunião não deve virar apontamento: status != 'done', horários
    ausentes/ilegíveis, ou duração real menor que min_meeting_minutes. O cliente
    sai cru em client_text (a resolução contra o cadastro é de quem tem banco —
    suggest_for_folder). Call cruzando a meia-noite: fim clampado em 23:59 do dia
    de início + flag na descrição para revisão manual.
    """
    if (meta or {}).get("status") != "done":
        return None
    try:
        started = datetime.fromisoformat(meta["started_at"])
        ended = datetime.fromisoformat(meta["ended_at"])
    except (KeyError, TypeError, ValueError):
        return None
    # duração REAL (não arredondada) decide se vale um apontamento; o gravado
    # (duration_seconds) vence o delta — pausas de device à parte, é o que houve.
    duration_s = meta.get("duration_seconds") or (ended - started).total_seconds()
    if ended <= started or duration_s < ts_cfg.min_meeting_minutes * 60:
        return None
    step = max(0, int(ts_cfg.round_minutes))
    start_hhmm = round_hhmm(started.strftime("%H:%M"), step)
    crossed = ended.date() != started.date()
    end_hhmm = "23:59" if crossed else round_hhmm(ended.strftime("%H:%M"), step)
    if end_hhmm <= start_hhmm:
        # arredondamento colapsou o bloco: garante ao menos 1 passo de duração
        end_hhmm = _add_minutes(start_hhmm, step)
        if end_hhmm <= start_hhmm:  # encostou em 23:59 sem espaço — sem sugestão
            return None
    description = (meta.get("title") or meta.get("meeting_title") or "").strip()
    if crossed:
        description = f"{description} {_MIDNIGHT_FLAG}".strip()
    return {
        "work_date": started.date().isoformat(),
        "start_time": start_hhmm,
        "end_time": end_hhmm,
        "client_text": (meta.get("client") or "").strip(),
        "description": description,
        "meeting_started_at": meta["started_at"],
    }


def _load_enabled_cfg():
    """[timesheet] do config real, ou None se o módulo está dormente (#126)."""
    from . import config  # lazy: config.load() cria APP_DIR/config.toml — só aqui

    ts = config.load().timesheet
    return ts if ts.enabled else None


def _apply(meta: dict, folder, ts_cfg) -> str:
    """Meta já lido → resolve cliente → upsert. Compartilhado por hook e varredura."""
    rec = suggestion_from_meta(meta, ts_cfg)
    if rec is None:
        return "ignored"
    client_id, text = timesheet_db.resolve_client(rec.pop("client_text"))
    rec["client_id"] = client_id
    rec["client_text"] = "" if client_id is not None else text
    rec["meeting_folder"] = str(folder)
    return timesheet_db.upsert_suggestion(rec)


def suggest_for_folder(folder, ts_cfg=None) -> str:
    """Sugere o apontamento de UMA pasta de gravação.

    Devolve 'created' | 'updated' | 'skipped' | 'ignored'. NUNCA levanta: meta
    ilegível/ausente, banco indisponível ou reunião não-elegível viram 'ignored'
    no log — o processamento da reunião jamais quebra por causa do timesheet.
    Sem ts_cfg explícito, lê o config e se auto-bloqueia se enabled = false.
    """
    try:
        cfg = ts_cfg if ts_cfg is not None else _load_enabled_cfg()
        if cfg is None:
            return "ignored"
        meta = json.loads((Path(folder) / "meta.json").read_text(encoding="utf-8"))
        return _apply(meta, folder, cfg)
    except Exception:
        log.exception("sugestão de apontamento falhou para %s", folder)
        return "ignored"


def sync_pending(recordings_dir=None, ts_cfg=None) -> int:
    """Varredura de reconciliação: toda reunião 'done' sem sugestão ganha uma.

    Rede de segurança para reuniões processadas via CLI com o app fechado, crash
    entre o 'done' e o hook, e histórico anterior à ativação do módulo. Devolve
    quantas sugestões NOVAS criou (atualizações não contam). Idempotente — o
    dedupe do banco segura reexecuções. Nunca levanta.
    """
    try:
        if ts_cfg is None or recordings_dir is None:
            from . import config  # lazy (mesma razão de _load_enabled_cfg)

            full = config.load()
            if ts_cfg is None:
                if not full.timesheet.enabled:
                    return 0
                ts_cfg = full.timesheet
            if recordings_dir is None:
                recordings_dir = full.output.resolved_recordings_dir()
        from . import notes  # lazy: reusa o scanner tolerante da casa

        created = 0
        for meta in notes.scan_meetings_by_status(recordings_dir, ("done",)):
            try:
                if _apply(meta, meta["folder"], ts_cfg) == "created":
                    created += 1
            except Exception:
                log.exception("sugestão falhou para %s (varredura segue)", meta.get("folder"))
        if created:
            log.info("timesheet: %d sugestão(ões) nova(s) na varredura", created)
        return created
    except Exception:
        log.exception("varredura de sugestões do timesheet falhou")
        return 0

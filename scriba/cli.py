"""CLI do ScribaDev."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from . import __version__


class _VersionAction(argparse.Action):
    """`--version` mostra a versão COM o build do git (sobe a cada commit/push)."""

    def __call__(self, parser, namespace, values, option_string=None):
        from . import updates

        print(f"scribadev {updates.build_string()}")
        parser.exit()


class _StampWriter:
    """Prefixa cada LINHA com HH:MM:SS — dá timestamp ao process.log do subprocesso de
    `process` (antes os prints de transcrição/diarização/resumo saíam sem hora)."""

    def __init__(self, stream):
        self._s = stream
        self._bol = True  # begin-of-line: a próxima escrita inicia uma linha

    def write(self, text) -> int:
        import time

        s, bol = self._s, self._bol
        for i, part in enumerate(str(text).split("\n")):
            if i > 0:
                s.write("\n")
                bol = True
            if part and bol:
                s.write(time.strftime("%H:%M:%S "))
                bol = False
            if part:
                s.write(part)
        self._bol = bol
        return len(text)

    def flush(self) -> None:
        try:
            self._s.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._s, name)


def _timestamp_subprocess_output() -> None:
    """Liga o HH:MM:SS por linha na saída do subprocesso `process` (vai pro process.log)."""
    if sys.stdout is not None and not isinstance(sys.stdout, _StampWriter):
        sys.stdout = _StampWriter(sys.stdout)
    if sys.stderr is not None and not isinstance(sys.stderr, _StampWriter):
        sys.stderr = _StampWriter(sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scribadev",
        description="Gravação e transcrição automática de reuniões do Microsoft Teams — local e privada.",
    )
    parser.add_argument("--version", action=_VersionAction, nargs=0,
                        help="mostra a versão (semver + build do git)")
    sub = parser.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="inicia o app de bandeja (monitora calls do Teams)")
    p_run.add_argument("--minimized", action="store_true",
                       help="inicia só na bandeja, sem abrir a janela (usado pelo autostart)")

    p_rec = sub.add_parser("record", help="grava manualmente por N segundos (teste)")
    p_rec.add_argument("seconds", type=int)
    p_rec.add_argument("--no-ui", action="store_true", help="sem a pílula flutuante")

    p_tr = sub.add_parser("transcribe", help="(re)transcreve uma pasta de reunião")
    p_tr.add_argument("folder", type=Path)
    p_tr.add_argument("--cpu", action="store_true", help="força transcrição em CPU")
    p_tr.add_argument("--speakers", type=int, default=None, metavar="N",
                      help="nº de participantes remotos: trava a diarização em N vozes (persiste no meta)")

    p_su = sub.add_parser("summarize", help="(re)gera o resumo e o notas.md de uma pasta de reunião")
    p_su.add_argument("folder", type=Path)

    p_proc = sub.add_parser("process", help="transcreve + resume + exporta uma pasta de reunião")
    p_proc.add_argument("folder", type=Path)
    p_proc.add_argument(
        "--when-ready", action="store_true",
        help="espera a gravação terminar e SÓ ENTÃO carrega o modelo (não segura a GPU "
             "durante a call); usado pelo app",
    )
    p_proc.add_argument("--speakers", type=int, default=None, metavar="N",
                        help="nº de participantes remotos: trava a diarização em N vozes (persiste no meta)")
    p_proc.add_argument("--cpu", action="store_true",
                        help="força transcrição E diarização em CPU (#115: recuperação pós-erro de GPU)")

    p_split = sub.add_parser(
        "split", help="corta uma reunião já processada em duas no offset dado (#37)"
    )
    p_split.add_argument("folder", type=Path)
    p_split.add_argument(
        "offset", help="ponto do corte relativo ao início: HH:MM:SS, MM:SS ou SS"
    )

    sub.add_parser("devices", help="lista dispositivos de áudio (microfone e loopback)")
    sub.add_parser("detect", help="modo debug: imprime as transições de estado da detecção")
    sub.add_parser("wizard", help="assistente de perfil: gera o prompt.md e as hotwords")

    p_doc = sub.add_parser("doctor", help="diagnóstico do ambiente")
    p_doc.add_argument("--toast", action="store_true", help="dispara uma notificação de teste")

    p_auto = sub.add_parser("autostart", help="liga/desliga o início automático com o Windows")
    p_auto.add_argument("mode", choices=["on", "off"])

    p_sc = sub.add_parser("shortcut", help="cria atalhos do ScribaDev (Área de Trabalho e menu Iniciar)")
    p_sc.add_argument("--desktop-only", action="store_true", help="só na Área de Trabalho")
    p_sc.add_argument("--start-menu-only", action="store_true", help="só no menu Iniciar")

    p_purge = sub.add_parser("purge", help="apaga gravações já transcritas além do prazo de retenção")
    p_purge.add_argument("--days", type=int, default=None, help="sobrepõe [audio].retention_days do config")
    p_purge.add_argument("--dry-run", action="store_true", help="só lista o que seria apagado")

    sub.add_parser("reindex", help="reconstrói o índice de busca das reuniões a partir das pastas (#10)")

    p_search = sub.add_parser("search", help="busca nas reuniões indexadas (texto, participante, cliente, datas)")
    p_search.add_argument("query", nargs="*", help="termos de busca full-text (título/resumo/participantes)")
    p_search.add_argument("--participant", "-p", metavar="NOME", help="filtra por participante (casa parcial)")
    p_search.add_argument("--client", "-c", metavar="CLIENTE", help="filtra por cliente (casa parcial)")
    p_search.add_argument("--since", metavar="AAAA-MM-DD", help="reuniões a partir desta data")
    p_search.add_argument("--until", metavar="AAAA-MM-DD", help="reuniões até esta data")
    p_search.add_argument("--status", default="done", help="status (default: done; use '' p/ todos)")
    p_search.add_argument("--limit", type=int, default=20, help="máximo de resultados (default: 20)")
    p_search.add_argument("--json", action="store_true",
                          help="saída JSON p/ agentes/skills (inclui id p/ o `show` e o snippet do trecho que casou)")

    p_show = sub.add_parser("show", help="mostra a nota de uma reunião (resumo; --transcript p/ a transcrição completa)")
    p_show.add_argument("target", help="pasta da gravação OU id numérico do índice (o id sai no `search --json`)")
    p_show.add_argument("--transcript", action="store_true",
                        help="imprime a transcrição completa em vez do resumo")
    p_show.add_argument("--json", action="store_true",
                        help="saída JSON (meta + participantes + pendências + resumo; com --transcript inclui a transcrição)")

    p_upd = sub.add_parser("update", help="checa atualizações (e aplica via git pull, se for instalação via git)")
    p_upd.add_argument("--check", action="store_true", help="só verifica se há nova versão, sem aplicar")

    p_ts = sub.add_parser(
        "timesheet",
        help="apontamento de horas (#118) — dormente até ativar ([timesheet] enabled = true)",
    )
    ts_sub = p_ts.add_subparsers(dest="ts_cmd")
    p_tl = ts_sub.add_parser("list", help="lista os apontamentos do mês, agrupados por dia")
    p_tl.add_argument("--month", metavar="AAAA-MM", default=None, help="mês (default: o corrente)")
    g_tl = p_tl.add_mutually_exclusive_group()
    g_tl.add_argument("--suggested", action="store_true", help="só sugestões pendentes de revisão")
    g_tl.add_argument("--unposted", action="store_true",
                      help="só confirmados ainda não lançados no sistema de horas")
    p_ta = ts_sub.add_parser("add", help="apontamento manual (atividade sem call)")
    p_ta.add_argument("--date", metavar="AAAA-MM-DD", default=None, help="data (default: hoje)")
    p_ta.add_argument("--start", required=True, metavar="HH:MM", help="hora de início")
    p_ta.add_argument("--end", required=True, metavar="HH:MM", help="hora de fim")
    p_ta.add_argument("--client", required=True, help="cliente (nome ou alias do cadastro)")
    p_ta.add_argument("--project", default="", help="código de OS/GAP (ex.: 403240)")
    p_ta.add_argument("--desc", default="", help="descrição breve da atividade")
    p_ta.add_argument("--extra", action="store_true", help="marca como hora extra")
    ts_sub.add_parser("sync", help="varre reuniões prontas e cria as sugestões que faltam")
    ts_sub.add_parser("backup", help="snapshot do timesheet.db (com rotação)")
    p_te = ts_sub.add_parser("export",
                             help="exporta o mês para Excel no layout da planilha de apontamento")
    p_te.add_argument("--month", metavar="AAAA-MM", default=None, help="mês (default: o corrente)")
    p_te.add_argument("--out", metavar="PASTA", default=None,
                      help="pasta de destino (default: [timesheet].export_dir)")
    p_ti = ts_sub.add_parser("import",
                             help="importa o histórico da planilha de apontamento (todas as abas mensais)")
    p_ti.add_argument("xlsx", metavar="PLANILHA.xlsx", help="planilha de apontamento a importar")
    p_ti.add_argument("--dry-run", action="store_true",
                      help="só imprime o relatório do que seria importado, sem gravar nada")

    args = parser.parse_args(argv)

    if not args.cmd:
        parser.print_help()
        return 0
    if args.cmd == "doctor":
        return cmd_doctor(args)
    if args.cmd == "detect":
        from .detector import debug_loop

        return debug_loop()
    if args.cmd == "wizard":
        return cmd_wizard()
    if args.cmd == "record":
        from .recorder import record_for

        return record_for(args.seconds, show_ui=not args.no_ui)
    if args.cmd == "devices":
        from .recorder import list_devices

        return list_devices()
    if args.cmd == "transcribe":
        from .pipeline import transcribe_folder

        return transcribe_folder(args.folder, force_cpu=args.cpu, num_speakers=args.speakers)
    if args.cmd == "summarize":
        from .pipeline import summarize_folder

        return summarize_folder(args.folder)
    if args.cmd == "split":
        return cmd_split(args)
    if args.cmd == "process":
        from .pipeline import process_folder, process_when_ready

        _timestamp_subprocess_output()  # process.log com HH:MM:SS por linha
        if args.when_ready:
            return process_when_ready(args.folder)
        return 0 if process_folder(args.folder, force_cpu=args.cpu, num_speakers=args.speakers) else 1
    if args.cmd == "run":
        from .main import run_app

        return run_app(minimized=args.minimized)
    if args.cmd == "autostart":
        from .autostart import set_autostart

        return set_autostart(args.mode == "on")
    if args.cmd == "shortcut":
        from .shortcuts import create_shortcuts

        return create_shortcuts(
            desktop=not args.start_menu_only,
            start_menu=not args.desktop_only,
        )
    if args.cmd == "purge":
        import dataclasses

        from .config import load
        from .retention import purge_old_recordings

        cfg = load()
        if args.days is not None:
            cfg = dataclasses.replace(cfg, audio=dataclasses.replace(cfg.audio, retention_days=args.days))
        affected = purge_old_recordings(cfg, dry_run=args.dry_run)
        rec_root = cfg.output.resolved_recordings_dir()
        for folder, age in affected:
            try:
                shown = folder.relative_to(rec_root)
            except ValueError:
                shown = folder.name
            print(f"  {shown} ({age:.0f} dias)")
        verb = "seria(m) apagada(s)" if args.dry_run else "apagada(s)"
        print(f"{len(affected)} gravação(ões) {verb} (retenção: {int(cfg.audio.retention_days)} dias)")
        return 0
    if args.cmd == "reindex":
        from .meetings_index import reindex

        n = reindex()
        print(f"índice reconstruído: {n} reunião(ões) indexada(s)")
        return 0
    if args.cmd == "search":
        return cmd_search(args)
    if args.cmd == "show":
        return cmd_show(args)
    if args.cmd == "update":
        return cmd_update(args)
    if args.cmd == "timesheet":
        return cmd_timesheet(args)
    parser.error(f"comando desconhecido: {args.cmd}")
    return 2


def cmd_split(args) -> int:
    """`scribadev split <pasta> <offset>`: corte retroativo de uma reunião em duas (#37)."""
    from .split import SplitError, parse_offset, split_recording

    try:
        offset = parse_offset(args.offset)
    except ValueError as e:
        print(f"erro: {e}")
        return 2
    try:
        folder1, folder2 = split_recording(args.folder, offset)
    except SplitError as e:
        print(f"erro: {e}")
        return 1
    print(f"dividido em duas reuniões:\n  parte 1: {folder1}\n  parte 2: {folder2}")
    return 0


def _hhmm(minutes: int) -> str:
    """Minutos -> 'H:MM' (formato dos totais da planilha: 5:00, 45:30)."""
    return f"{minutes // 60}:{minutes % 60:02d}"


def cmd_timesheet(args) -> int:
    """`scribadev timesheet`: apontamento de horas (#118).

    Módulo DORMENTE (#126): sem [timesheet].enabled = true nenhum subcomando roda
    nem cria banco — a CLI é ponto de integração e o gate mora aqui.
    """
    from . import timesheet_db as tsdb
    from .config import load

    cfg = load()
    ts = cfg.timesheet
    if not ts.enabled:
        print("apontamento de horas não ativado — ligue com [timesheet] enabled = true "
              "no config.toml (ou pelo botão nas Configurações, quando disponível)")
        return 2
    tsdb.apply_config(ts)
    if args.ts_cmd == "list":
        return _ts_list(args, tsdb)
    if args.ts_cmd == "add":
        return _ts_add(args, tsdb)
    if args.ts_cmd == "sync":
        from . import timesheet_suggest

        n = timesheet_suggest.sync_pending(cfg.output.resolved_recordings_dir(), ts)
        print(f"{n} sugestão(ões) nova(s) de apontamento")
        return 0
    if args.ts_cmd == "backup":
        return _ts_backup(ts, tsdb)
    if args.ts_cmd == "export":
        return _ts_export(args, ts)
    if args.ts_cmd == "import":
        return _ts_import(args)
    print("uso: scribadev timesheet {list,add,sync,backup,export,import} (veja --help)")
    return 2


def _ts_list(args, tsdb) -> int:
    """Lista agrupada por dia: [?] = sugestão a revisar, [L] = já lançado."""
    from datetime import date, datetime

    month = args.month or date.today().strftime("%Y-%m")
    try:
        datetime.strptime(month, "%Y-%m")
    except ValueError:
        print(f"erro: mês inválido (use AAAA-MM): {month!r}")
        return 2
    if args.suggested:
        entries = tsdb.list_entries(month=month, status="suggested")
    elif args.unposted:
        entries = tsdb.list_entries(month=month, status="confirmed", posted=False)
    else:
        entries = [e for e in tsdb.list_entries(month=month) if e["status"] != "discarded"]
    if not entries:
        print(f"nenhum apontamento em {month}.")
        return 0
    totals = tsdb.day_totals(month)
    day = None
    for e in entries:
        if e["work_date"] != day:
            day = e["work_date"]
            total = totals.get(day)
            print(f"{day}" + (f"  ({_hhmm(total)})" if total else ""))
        marks = " [?]" if e["status"] == "suggested" else (" [L]" if e["posted"] else "")
        client = e["client_name"] or e["client_text"] or "-"
        project = e["project_code"] or e["project_text"]
        extra = " (extra)" if e["overtime"] else ""
        detail = "  ".join(x for x in (client, project, e["description"]) if x)
        print(f"  {e['start_time']}-{e['end_time']}  {_hhmm(e['minutes']):>5}  "
              f"{detail}{extra}{marks}")
    s = tsdb.month_summary(month)
    print(f"{month}: total {_hhmm(s['total'])} | lançado {_hhmm(s['posted'])} | "
          f"a lançar {_hhmm(s['unposted'])} | sugestões {_hhmm(s['suggested'])}")
    return 0


def _ts_add(args, tsdb) -> int:
    """Apontamento manual; cliente resolvido pelo cadastro (não resolvido = texto cru)."""
    from datetime import date

    work_date = args.date or date.today().isoformat()
    client_id, text = tsdb.resolve_client(args.client)
    if client_id is None:
        print(f"aviso: cliente {text!r} não cadastrado — gravado como texto cru "
              "(cadastre pela UI ou adicione um alias)")
    project_id, project_text = None, args.project.strip()
    if client_id is not None and project_text:
        match = [p for p in tsdb.list_projects(client_id)
                 if p["code"].casefold() == project_text.casefold()]
        if match:
            project_id, project_text = match[0]["id"], ""
    try:
        entry_id = tsdb.add_entry(
            work_date=work_date, start_time=args.start, end_time=args.end,
            client_id=client_id, client_text="" if client_id is not None else text,
            project_id=project_id, project_text=project_text,
            description=args.desc, overtime=args.extra,
        )
    except ValueError as e:
        print(f"erro: {e}")
        return 2
    mine = next(r for r in tsdb.list_entries(day=work_date) if r["id"] == entry_id)
    print(f"registrado #{entry_id}: {mine['work_date']} {mine['start_time']}-{mine['end_time']} "
          f"({_hhmm(mine['minutes'])}) {mine['client_name'] or mine['client_text'] or '-'}")
    return 0


def _ts_export(args, ts) -> int:
    """Exporta o mês para xlsx no layout da planilha de apontamento (#122)."""
    from datetime import date, datetime
    from pathlib import Path as _P

    from . import timesheet_xlsx

    month = args.month or date.today().strftime("%Y-%m")
    try:
        datetime.strptime(month, "%Y-%m")
    except ValueError:
        print(f"erro: mês inválido (use AAAA-MM): {month!r}")
        return 2
    dest = _P(args.out).expanduser() if args.out else (
        _P(ts.export_dir).expanduser() if ts.export_dir else None)
    try:
        out = timesheet_xlsx.export_month(month, dest)
    except ValueError as e:
        print(f"erro: {e}")
        return 1
    print(f"exportado: {out}")
    return 0


def _ts_import(args) -> int:
    """Importa o histórico da planilha (#125). Idempotente: reimportar não duplica."""
    from pathlib import Path as _P

    from . import timesheet_xlsx

    path = _P(args.xlsx).expanduser()
    if not path.exists():
        print(f"erro: planilha não encontrada: {path}")
        return 2
    try:
        report = timesheet_xlsx.import_workbook(path, dry_run=args.dry_run)
    except ValueError as e:
        print(f"erro: {e}")
        return 1
    print(report.summary())
    return 0


def _ts_backup(ts, tsdb) -> int:
    from pathlib import Path as _P

    if not tsdb._db_path().exists():
        print("nada para fazer backup — o banco ainda não existe.")
        return 1
    out = tsdb.backup(_P(ts.backup_dir).expanduser() if ts.backup_dir else None)
    if out is None:
        print("backup falhou — veja o log.")
        return 1
    print(f"backup gravado em {out}")
    return 0


def _utf8_out() -> None:
    """Força stdout em UTF-8: o console/pipe do Windows costuma ser cp1252, que
    estoura UnicodeEncodeError com qualquer caractere da transcrição fora do code
    page. Agentes (skill scriba-reunioes) leem UTF-8 sem drama."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, OSError):
        pass


def _print_json(data) -> None:
    import json

    _utf8_out()
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_search(args) -> int:
    """`scribadev search`: lista as reuniões do índice (#10) que casam os filtros.
    `--json` é a saída p/ agentes (skill scriba-reunioes): registros completos com
    `id` (aceito pelo `show`) e `snippet` do trecho onde os termos casaram."""
    from .meetings_index import search

    results = search(
        query=" ".join(args.query) or None,
        participant=args.participant,
        client=args.client,
        since=args.since,
        until=args.until,
        status=(args.status or None),
        limit=args.limit,
        snippets=args.json,
    )
    if args.json:
        _print_json(results)
        return 0
    if not results:
        print("nenhuma reunião encontrada.")
        return 0
    for r in results:
        when = (r.get("started_at") or "")[:16].replace("T", " ")
        mins = (r.get("duration_s") or 0) // 60
        cli = f" · {r['client']}" if r.get("client") else ""
        dur = f"  [{mins} min]" if mins else ""
        print(f"{when or '?':16}  {r.get('title') or '(sem título)'}{cli}{dur}")
        pres = [p["name"] for p in r.get("participants", []) if p["kind"] == "present"]
        if pres:
            print(f"    presentes: {', '.join(pres)}")
        if r.get("export_path"):
            print(f"    {r['export_path']}")
    print(f"\n{len(results)} resultado(s)")
    return 0


def cmd_show(args) -> int:
    """`scribadev show`: imprime a nota de UMA reunião — resumo por padrão,
    `--transcript` p/ a transcrição completa, `--json` p/ a saída estruturada que a
    skill scriba-reunioes consome (meta + participantes + pendências + resumo).
    Aceita a pasta da gravação OU o id numérico que o `search --json` devolve."""
    import json

    from . import notes
    from .meetings_index import get_folder, split_note

    folder = Path(args.target)
    if not folder.is_dir() and str(args.target).isdigit():
        found = get_folder(int(args.target))
        if found:
            folder = Path(found)
    if not folder.is_dir():
        print(f"erro: pasta ou id de reunião não encontrado: {args.target}")
        return 2
    notas = folder / "notas.md"
    if not notas.exists():
        print(f"erro: {notas} não existe — a nota ainda não foi gerada (veja `scribadev summarize`)")
        return 1
    _utf8_out()
    md = notas.read_text(encoding="utf-8")
    summary, transcript = split_note(md)
    try:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    if args.json:
        presentes, mencionados = notes.parse_participants(md)
        state = notes.load_action_state(folder)
        items = [{**it, "state": state.get(it["key"], "open")}
                 for it in notes.parse_action_items(md)]
        dur = meta.get("duration_seconds")
        out = {
            "folder": str(folder),
            "title": (meta.get("title") or "").strip(),
            "client": (meta.get("client") or "").strip(),
            "meeting_title": (meta.get("meeting_title") or "").strip(),
            "started_at": meta.get("started_at") or "",
            "ended_at": meta.get("ended_at") or "",
            "duration_s": int(float(dur)) if dur else None,
            "status": meta.get("status") or "",
            "export_path": meta.get("export_path") or "",
            "participants": {
                "present": [{"name": n, "role": (r or "").strip()} for n, r in presentes.items()],
                "mentioned": list(mencionados),
            },
            "action_items": items,
            "summary": summary,
        }
        if args.transcript:
            out["transcript"] = transcript
        _print_json(out)
        return 0
    if args.transcript:
        print(transcript or "(a nota não tem a seção de transcrição completa)")
        return 0
    print(summary or md.strip())
    return 0


def cmd_update(args) -> int:
    """`scribadev update`: checa a última versão no GitHub e, se for instalação via git,
    aplica com `git pull`. `--check` só informa, sem aplicar."""
    from . import __version__, updates

    latest = updates.latest_version()
    if latest is None:
        print("não consegui checar atualizações (sem internet ou GitHub indisponível).")
        return 1
    print(f"versão atual: {__version__}  ·  última publicada: {latest}")
    if not updates._is_newer(__version__, latest):
        print("Você já está na versão mais recente.")
        return 0
    print(f"Nova versão disponível: v{latest}")
    if args.check:
        if updates.is_git_install():
            print("  rode `scribadev update` (sem --check) para aplicar via git pull.")
        else:
            print("  baixe em: " + updates.RELEASES_PAGE)
        return 0
    ok, msg = updates.apply_git_update()
    print(msg)
    return 0 if ok else 1


def main_tray() -> int:
    """Entry point do scribadev-tray.exe (sem console). Repassa os argumentos do
    atalho/autostart: o autostart usa `--minimized` (inicia só na bandeja); o atalho
    normal vem sem args e abre a janela na frente."""
    return main(["run", *sys.argv[1:]])


# ---------------------------------------------------------------- doctor ---

_OK, _WARN, _FAIL = "[OK]   ", "[AVISO]", "[FALHA]"


def _print(level: str, label: str, detail: str = "") -> None:
    print(f"{level} {label}" + (f" — {detail}" if detail else ""))


def cmd_wizard() -> int:
    """`scriba wizard`: abre o assistente de perfil sozinho (sem a bandeja)."""
    from PySide6.QtWidgets import QApplication

    from .qt import theme
    from .qt.wizard_ui import WizardWindow

    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    win = WizardWindow(app=None, on_applied=app.quit, standalone=True)  # aplicar/fechar encerra
    win.show()
    app.exec()
    print("assistente encerrado — o prompt aplicado (se houver) vale na próxima ata.")
    return 0


def cmd_doctor(args) -> int:
    from . import config, util

    failures = 0

    # Python
    v = sys.version_info
    if v >= (3, 12):
        _print(_OK, "Python", f"{v.major}.{v.minor}.{v.micro}")
    else:
        _print(_FAIL, "Python", f"{v.major}.{v.minor} — ScribaDev precisa de 3.12+")
        failures += 1

    # Config
    try:
        cfg = config.load()
        _print(_OK, "Config", str(util.CONFIG_PATH))
        _print(_OK, "Notas exportadas para", str(cfg.output.resolved_export_dir()))
        _print(_OK, "Gravações em", str(cfg.output.resolved_recordings_dir()))
    except Exception as e:
        _print(_FAIL, "Config", str(e))
        failures += 1
        cfg = None

    # Imports nativos — pyaudiowpatch/windows_toasts só existem (e só instalam,
    # pelos markers do #95) no Windows; fora dele são não-aplicáveis (#98)
    native = [
        ("faster_whisper", "transcrição"),
        ("ctranslate2", "motor do Whisper"),
    ]
    if sys.platform == "win32":
        native = [("pyaudiowpatch", "captura de áudio"), *native, ("windows_toasts", "notificações")]
    for mod, why in native:
        try:
            __import__(mod)
            _print(_OK, f"import {mod}")
        except Exception as e:
            _print(_FAIL, f"import {mod}", f"({why}) {e}")
            failures += 1
    if sys.platform != "win32":
        _print(_OK, "import pyaudiowpatch", "não se aplica neste SO (captura é Windows-only por ora)")
        _print(_OK, "import windows_toasts", "não se aplica neste SO (toasts são Windows-only)")

    # CUDA
    try:
        util.bootstrap_cuda_dlls()
        import ctranslate2

        n = ctranslate2.get_cuda_device_count()
        if n > 0:
            _print(_OK, "GPU CUDA", f"{n} dispositivo(s) — transcrição acelerada")
        else:
            _print(_WARN, "GPU CUDA", "nenhuma — transcrição usará CPU (mais lenta)")
    except Exception as e:
        _print(_WARN, "GPU CUDA", f"indisponível ({e}) — transcrição usará CPU")

    # motor de transcrição (STT): local (Whisper) ou nuvem (Groq/OpenAI-compat)
    try:
        weng = (cfg.whisper.engine if cfg else "local").strip().lower()
        if weng == "cloud":
            from . import stt_cloud

            ok, msg = stt_cloud.test_connection(cfg.whisper)
            _print(_OK if ok else _WARN, "Transcrição (nuvem)", f"{cfg.whisper.cloud_model} — {msg}")
        else:
            _print(_OK, "Transcrição (local)", cfg.whisper.model if cfg else "large-v3-turbo")
    except Exception as e:
        _print(_WARN, "Transcrição", f"erro ao checar ({e})")

    # provider de IA do resumo (claude CLI / Ollama / OpenAI-compatível)
    try:
        prov = (cfg.summary.provider if cfg else "claude").strip().lower()
        if prov == "claude":
            cmd = util.claude_command()
            if cmd is None:
                _print(_WARN, "Resumo (claude CLI)", "claude não encontrado — resumo será pulado (transcrição funciona normal)")
            else:
                out = subprocess.run(cmd + ["-v"], capture_output=True, text=True, timeout=60)
                ver = (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr) else "?"
                _print(_OK, "Resumo (claude CLI)", ver)
        else:
            from . import ai

            ok, msg = ai.test_connection(cfg.summary)
            _print(_OK if ok else _WARN, f"Resumo ({prov})", msg)
    except Exception as e:
        _print(_WARN, "Resumo (IA)", f"erro ao checar o provider ({e})")

    # Registro dos apps monitorados — a detecção lê o ConsentStore do Windows;
    # fora dele ainda não há detecção automática (#98; stubs na #102)
    if sys.platform != "win32":
        _print(_OK, "Detecção de calls", "não suportada neste SO ainda — use a gravação manual")
    else:
        try:
            from .detector import active_app, app_key_status, patterns_from

            pats = patterns_from(cfg.detection) if cfg else ["teams"]
            in_call = active_app(pats)
            for name, exists in app_key_status(pats).items():
                if name == in_call:
                    _print(_OK, f"Detecção {name}", "call ATIVA agora")
                elif exists:
                    _print(_OK, f"Detecção {name}", "chave do registro OK")
                else:
                    _print(_WARN, f"Detecção {name}", "sem chave ainda — entre numa call dele uma vez para o Windows criá-la")
        except Exception as e:
            _print(_FAIL, "Detecção de apps", str(e))
            failures += 1

        # Reuniões no navegador (Google Meet, Teams web...)
        try:
            from .detector import (
                browser_key_status,
                browser_patterns_from,
                desktop_names_from,
                title_patterns_from,
                web_service_label,
            )

            bpats = browser_patterns_from(cfg.detection) if cfg else []
            if bpats:
                desktop = desktop_names_from(cfg.detection)
                tpats = title_patterns_from(cfg.detection) if cfg else []
                svcs = ", ".join(dict.fromkeys(web_service_label(t, desktop) for t in tpats)) or "qualquer site com mic"
                ready = [b for b, ok in browser_key_status(bpats).items() if ok]
                if ready:
                    _print(_OK, "Detecção no navegador", f"{svcs} — via {', '.join(ready)}")
                else:
                    _print(_WARN, "Detecção no navegador", f"{svcs} — nenhum navegador usou o mic ainda; entre numa call (ex.: Meet) uma vez")
            else:
                _print(_OK, "Detecção no navegador", "desligada (browsers = \"\")")
        except Exception as e:
            _print(_WARN, "Detecção no navegador", str(e))

    # macOS (#104): segredos no Keychain + orientação de permissões. Os itens de
    # captura/detecção nativas entram aqui quando os marcos M3/M4 chegarem.
    if sys.platform == "darwin":
        if util.keychain_ok():
            _print(_OK, "Segredos (Keychain)", "chaveiro acessível — chaves de API ficam fora do config.toml")
        else:
            _print(_WARN, "Segredos (Keychain)",
                   "chaveiro inacessível (trancado? sessão SSH?) — chaves de API serão gravadas em texto plano")
        _print(_OK, "Permissões do macOS",
               "nenhuma necessária por ora; captura (Microfone + Áudio do Sistema) chega em marcos futuros")

    # Áudio — enumeração WASAPI (pyaudiowpatch); fora do Windows a captura ainda
    # não existe e o item é não-aplicável (#98)
    if sys.platform != "win32":
        _print(_OK, "Dispositivos de áudio", "captura não suportada neste SO ainda (Windows-only)")
    else:
        try:
            import pyaudiowpatch as pyaudio

            p = pyaudio.PyAudio()
            try:
                mic = p.get_default_input_device_info()["name"]
                lb = p.get_default_wasapi_loopback()["name"]
            finally:
                p.terminate()
            _print(_OK, "Microfone padrão", mic)
            _print(_OK, "Loopback padrão", lb)
        except Exception as e:
            _print(_FAIL, "Dispositivos de áudio", str(e))
            failures += 1

    # Compressão do áudio guardado (ffmpeg): WAV cru -> opus/flac. Sem ele os arquivos
    # ficam gigantes (~1,3 GB/h); também decodifica áudio comprimido numa re-transcrição.
    keep = bool(cfg.audio.keep_audio) if cfg else False
    fmt = ((cfg.audio.archive_format if cfg else "opus") or "wav").strip().lower()
    lvl = util.ffmpeg_status(keep, fmt)
    if lvl == "ok":
        exe = util.ffmpeg_command()
        _print(_OK, "Compressão de áudio (ffmpeg)", f"no PATH: {exe[0] if exe else 'ffmpeg'}")
    elif lvl == "err":
        if sys.platform == "win32":
            hint = (f"AUSENTE no PATH do Windows — o áudio fica em WAV cru (~1,3 GB/h), não {fmt} (~20 MB/h). "
                    "Instale: winget install ffmpeg (entra no PATH do Windows sozinho — não é uma pasta do "
                    "ScribaDev), feche e reabra o app. Confira com: where ffmpeg")
        else:
            hint = (f"AUSENTE no PATH — o áudio fica em WAV cru (~1,3 GB/h), não {fmt} (~20 MB/h). "
                    "Instale pelo gerenciador do SO (ex.: apt install ffmpeg / brew install ffmpeg) "
                    "e confira com: which ffmpeg")
        _print(_FAIL, "Compressão de áudio (ffmpeg)", hint)
        failures += 1
    else:
        _print(_WARN, "Compressão de áudio (ffmpeg)",
               "ausente no PATH — necessário p/ compactar o áudio guardado e re-transcrever áudio comprimido")

    # Diarização
    if cfg and cfg.diarization.enabled:
        try:
            import pyannote.audio  # noqa: F401
            import torch

            gpu = "GPU" if torch.cuda.is_available() else "CPU"
            if cfg.diarization.hf_token:
                mode = "pergunta nº de participantes" if cfg.diarization.ask_speakers else "automático"
                _print(_OK, "Diarização", f"habilitada ({gpu}); modelo {cfg.diarization.model}; {mode}")
            else:
                _print(_WARN, "Diarização", "habilitada mas SEM token HF — configure na aba Gravação")
        except ImportError as e:
            _print(_WARN, "Diarização", f"habilitada mas dependências ausentes ({e}) — pip install -e .[diarization]")
    else:
        _print(_OK, "Diarização", "desabilitada (participantes saem agrupados)")

    # Enrollment de voz (#1): vozes que o app já aprende a reconhecer
    try:
        from . import speakers

        n = len(speakers.load_store())
        if n:
            _print(_OK, "Vozes cadastradas", f"{n} pessoa(s) reconhecida(s) por voz (enrollment)")
    except Exception:
        pass

    # Cache do modelo Whisper
    try:
        model = cfg.whisper.model if cfg else "large-v3-turbo"
        hub = Path.home() / ".cache" / "huggingface" / "hub"
        cached = bool(list(hub.glob(f"models--*{model}*"))) if hub.exists() else False
        if cached:
            _print(_OK, "Modelo Whisper", f"{model} (em cache)")
        else:
            setup_script = "setup.ps1" if sys.platform == "win32" else "setup.sh"
            _print(_WARN, "Modelo Whisper", f"{model} ainda não baixado — baixa (~1,6 GB) na primeira transcrição ou no {setup_script}")
    except Exception as e:
        _print(_WARN, "Modelo Whisper", str(e))

    # Toast de teste
    if args.toast:
        try:
            from .notify import Notifier

            Notifier().test()
            if sys.platform == "win32":
                _print(_OK, "Toast", "notificação de teste disparada")
            else:
                _print(_OK, "Toast", "no-op neste SO (toasts nativos ainda não suportados) — logado")
        except Exception as e:
            _print(_FAIL, "Toast", str(e))
            failures += 1

    print()
    if failures:
        print(f"{failures} problema(s) critico(s). Corrija antes de usar o scribadev run.")
        return 1
    print("Tudo pronto. Use: scribadev run  (ou scribadev autostart on)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

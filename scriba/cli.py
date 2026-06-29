"""CLI do ScribaDev."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from . import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scribadev",
        description="Gravação e transcrição automática de reuniões do Microsoft Teams — local e privada.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
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
    if args.cmd == "process":
        from .pipeline import process_folder, process_when_ready

        if args.when_ready:
            return process_when_ready(args.folder)
        return 0 if process_folder(args.folder, num_speakers=args.speakers) else 1
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
    parser.error(f"comando desconhecido: {args.cmd}")
    return 2


def cmd_search(args) -> int:
    """`scribadev search`: lista as reuniões do índice (#10) que casam os filtros."""
    from .meetings_index import search

    results = search(
        query=" ".join(args.query) or None,
        participant=args.participant,
        client=args.client,
        since=args.since,
        until=args.until,
        status=(args.status or None),
        limit=args.limit,
    )
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
    import tkinter as tk

    from .wizard_ui import WizardWindow

    root = tk.Tk()
    root.withdraw()
    win = WizardWindow(root, app=None, on_applied=root.quit)
    win.win.protocol("WM_DELETE_WINDOW", root.quit)  # fechar a janela encerra o comando
    win.show()
    root.mainloop()
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

    # Imports nativos
    for mod, why in [
        ("pyaudiowpatch", "captura de áudio"),
        ("faster_whisper", "transcrição"),
        ("ctranslate2", "motor do Whisper"),
        ("pystray", "ícone de bandeja"),
        ("windows_toasts", "notificações"),
    ]:
        try:
            __import__(mod)
            _print(_OK, f"import {mod}")
        except Exception as e:
            _print(_FAIL, f"import {mod}", f"({why}) {e}")
            failures += 1

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

    # Registro dos apps monitorados
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

    # Áudio
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
            _print(_WARN, "Modelo Whisper", f"{model} ainda não baixado — baixa (~1,6 GB) na primeira transcrição ou no setup.ps1")
    except Exception as e:
        _print(_WARN, "Modelo Whisper", str(e))

    # Toast de teste
    if args.toast:
        try:
            from .notify import Notifier

            Notifier().test()
            _print(_OK, "Toast", "notificação de teste disparada")
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

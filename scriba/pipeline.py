"""Pipeline pós-gravação: transcrever → mesclar → resumir → exportar."""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import merge as merge_mod
from . import util as util_mod
from .config import load
from .transcription import TranscriptionProvider, make_transcriber


def transcribe_folder(folder: Path, force_cpu: bool = False, transcriber: TranscriptionProvider | None = None,
                      num_speakers: int | None = None) -> int:
    folder = Path(folder)
    meta_path = folder / "meta.json"
    if not meta_path.exists():
        print(f"meta.json não encontrado em {folder}")
        return 1
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    # nº de vozes remotas para travar a diarização: o argumento (CLI --speakers)
    # sobrepõe e passa a valer nas próximas reprocessadas; sem ele, usa o que a
    # janela de fim de call gravou no meta. None nos dois = diarização automática.
    if num_speakers is not None:
        meta["num_speakers"] = int(num_speakers)
    num_speakers = meta.get("num_speakers")

    if meta.get("audio_removed"):
        # áudio apagado DE PROPÓSITO (keep_audio=false): os WAVs não existem mais,
        # mas a transcrição original segue em transcript.json. NÃO é "sem áudio" —
        # não rebaixa o status nem marca no_audio. (O re-resumo normal, scriba
        # process, nem chega aqui: skip_transcribe pula direto para o resumo.)
        print("áudio removido de propósito (keep_audio=false) — nada para (re)transcrever; "
              "a transcrição original permanece em transcript.json")
        return 1

    from .recorder import repair_folder

    repair_folder(folder)  # idempotente; conserta headers de gravações pós-crash

    # marca o estágio antes de carregar o modelo: a UI mostra "Transcrevendo…"
    # já durante a carga da GPU (que faz parte do tempo percebido)
    meta["status"] = "transcribing"
    util_mod.atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))

    cfg = load()
    tr = transcriber or make_transcriber(cfg.whisper, force_cpu=force_cpu)
    device = tr.ensure_loaded()
    if (cfg.whisper.engine or "local").strip().lower() == "cloud":
        print(f"transcrição na nuvem: {cfg.whisper.cloud_model}")
    else:
        print(f"modelo {cfg.whisper.model} em {device}")

    by_speaker: dict[str, tuple[list, float]] = {}
    # 1ª passada: SÓ transcrição (Whisper carregado). Guarda os segmentos e a wav do
    # loopback p/ diarizar DEPOIS — liberar o Whisper antes de carregar o pyannote faz
    # o pico de RAM/VRAM virar max(Whisper, pyannote) em vez da soma dos dois.
    pending: list[tuple[str, str, list, float]] = []  # (stream, speaker, segments, offset)
    loopback_wav: Path | None = None
    for stream_name, speaker in (("mic", "Eu"), ("loopback", "Participantes")):
        s = (meta.get("streams") or {}).get(stream_name)
        if not s or not s.get("file"):
            continue
        wav = folder / s["file"]
        if not wav.exists():
            print(f"aviso: {wav.name} não existe, pulando")
            continue
        if wav.stat().st_size <= 44:  # só o header (ou nem isso): nada gravado
            print(f"aviso: {wav.name} vazio, pulando")
            continue
        print(f"transcrevendo {wav.name} ...")
        segments = tr.transcribe(wav)
        offset = float(s.get("offset_seconds", 0.0))
        print(f"  {len(segments)} segmentos")
        pending.append((stream_name, speaker, segments, offset))
        if stream_name == "loopback" and segments:
            loopback_wav = wav

    final_device = tr.device_used or device
    tr.close()  # libera o Whisper ANTES da diarização: corta o pico de memória

    # 2ª passada: diarização do loopback (pyannote), já sem o Whisper na memória
    diarized_groups: dict[str, list] | None = None
    if loopback_wav is not None:
        from . import diarize as diarize_mod

        # marca o estágio antes de carregar o pyannote: a UI mostra "Separando vozes…"
        meta["status"] = "diarizing"
        util_mod.atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))
        lb_segments = next((seg for st, _sp, seg, _off in pending if st == "loopback"), [])
        dz_result = diarize_mod.diarize(loopback_wav, cfg.diarization, num_speakers=num_speakers, meta=meta)
        if dz_result and dz_result.turns:
            meta.pop("diarization_error", None)  # sucesso: limpa erro de tentativa anterior
            diarized_groups, order = diarize_mod.assign_speakers(lb_segments, dz_result.turns)
            meta["diarization_model"] = cfg.diarization.model
            # enrollment de voz (#1): casa cada "Participante N" ao seu embedding,
            # aplica nomes já conhecidos e guarda as vozes p/ rotular na UI
            diarized_groups = _apply_voice_enrollment(
                folder, meta, diarized_groups, order, dz_result.embeddings
            )

    for stream_name, speaker, segments, offset in pending:
        if stream_name == "loopback" and diarized_groups is not None:
            for name, group in diarized_groups.items():
                by_speaker[name] = (group, offset)
        else:
            by_speaker[speaker] = (segments, offset)

    if not by_speaker:
        print("nenhum áudio para transcrever")
        meta["status"] = "no_audio"  # terminal: não fica "transcribing" eterno
        util_mod.atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))
        return 1

    turns = merge_mod.merge(by_speaker)
    # escrita atômica do transcript.json: ponto crítico — são vários MB e a única
    # cópia da transcrição; write_text direto trunca o arquivo se o processo morrer
    # no meio (antivírus, kill), corrompendo a retomada em process_folder
    util_mod.atomic_write_text(
        folder / "transcript.json",
        json.dumps([t.__dict__ for t in turns], ensure_ascii=False, indent=1),
    )
    meta["status"] = "transcribed"
    _cloud = (cfg.whisper.engine or "local").strip().lower() == "cloud"
    meta["whisper_model"] = cfg.whisper.cloud_model if _cloud else cfg.whisper.model
    meta["whisper_device"] = final_device
    util_mod.atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"transcrição pronta: {len(turns)} turnos -> {folder / 'transcript.json'}")
    return 0


def summarize_folder(folder: Path) -> int:
    from . import notes

    return 0 if notes.build_notes(Path(folder)) else 1


def _apply_voice_enrollment(folder: Path, meta: dict, grouped: dict, order: dict, embeddings: dict) -> dict:
    """Enrollment de voz (#1): renomeia "Participante N" → nome já conhecido
    (match por embedding) e grava voices.json na pasta para rotulagem na UI.

    voices.json: {rótulo exibido: {embedding, auto, score}} — só vozes do loopback.
    Sem embeddings (pyannote legacy) é no-op e a nota segue com "Participante N".
    """
    if not embeddings:
        return grouped
    from . import speakers

    store = speakers.load_store()
    part_to_label = {f"Participante {n}": label for label, n in order.items()}
    voices: dict[str, dict] = {}
    renames: dict[str, str] = {}
    used: set[str] = set()
    for part, label in part_to_label.items():
        emb = embeddings.get(label)
        if emb is None:
            continue
        name, score = speakers.match(emb, store)
        final, auto = part, False
        # não cola o mesmo nome em duas vozes distintas (mantém a 2ª como Participante)
        if name and name not in used:
            final, auto = name, True
            renames[part] = name
        used.add(final)
        voices[final] = {"embedding": emb, "auto": auto, "score": round(float(score), 3)}

    if renames:
        merged: dict[str, list] = {}
        for k, segs in grouped.items():
            merged.setdefault(renames.get(k, k), []).extend(segs)
        grouped = merged
        print("vozes reconhecidas: " + ", ".join(f"{p} -> {n}" for p, n in renames.items()))

    try:
        util_mod.atomic_write_text(folder / "voices.json", json.dumps(voices, ensure_ascii=False))
    except Exception as e:
        print(f"AVISO: não salvei voices.json ({e})")
    recognized = sorted(n for n, v in voices.items() if v["auto"])
    if recognized:
        meta["speakers_recognized"] = recognized
    return grouped


def _mark_failed(folder: Path, exc: BaseException) -> None:
    """Status terminal "failed" no meta (se ainda estava em andamento) + motivo."""
    meta_path = Path(folder) / "meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("status") in util_mod.IN_PROGRESS_STATUSES:
            meta["status"] = "failed"
            meta["error"] = str(exc)[:300]
            util_mod.atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))
    except Exception:
        pass  # meta ilegível: o supervisor (app) ainda marca failed pelo rc


def process_folder(folder: Path, force_cpu: bool = False, transcriber: TranscriptionProvider | None = None,
                   num_speakers: int | None = None) -> Path | None:
    """Fluxo completo pós-call: transcrever + gerar notas. Retorna o MD exportado.

    Qualquer exceção vira status="failed" no meta antes de propagar — a UI nunca
    fica num "Transcrevendo…" eterno. Se o transcript.json já existe (retomada de
    um crash pós-transcrição), pula direto para o resumo.

    Guarda de corrida: cria <pasta>/.lock com o PID ao iniciar e remove em finally.
    A retenção (retention.py) recusa remover a pasta enquanto o .lock existir,
    fechando a janela TOCTOU mesmo em reprocessamentos manuais ("scriba process").
    """
    import os as _os

    folder = Path(folder)
    # guard de corrida: se outro processo já está com o .lock desta pasta (ex.:
    # worker da GUI + 'scriba process' manual ao mesmo tempo), não reprocessa —
    # evitaria custo dobrado de IA/GPU e escrita concorrente em meta/transcript/notas.
    if util_mod.is_locked(folder):
        print(f"{folder.name}: .lock ativo de outro processo — pulando reprocessamento")
        return None
    lock_path = folder / ".lock"
    # grava PID e timestamp no lock: a retenção pode ignorar lock órfão de crash
    # verificando se o PID ainda existe e se o lock é muito antigo
    try:
        lock_path.write_text(
            json.dumps({"pid": _os.getpid(), "started": time.time()}),
            encoding="utf-8",
        )
    except OSError:
        pass  # pasta sumiu ou sem permissão: o processamento tentará e falhará adiante
    try:
        skip_transcribe = False
        if (folder / "transcript.json").exists():
            try:
                st = json.loads((folder / "meta.json").read_text(encoding="utf-8")).get("status")
            except (OSError, ValueError):
                st = None
            skip_transcribe = st in ("transcribed", "summarizing")
        if skip_transcribe:
            print("transcript.json já existe — retomando direto no resumo")
        elif transcribe_folder(folder, force_cpu=force_cpu, transcriber=transcriber,
                               num_speakers=num_speakers) != 0:
            return None
        from . import notes

        md = notes.build_notes(folder)
    except Exception as e:
        _mark_failed(folder, e)
        raise
    finally:
        # remove o lock mesmo em exceção: garante que a retenção não fique bloqueada
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass
    if md:
        archive_audio(folder, load())
    return md


# Codec → argumentos do ffmpeg (sempre 16 kHz mono: é o que Whisper/pyannote usam).
_ARCHIVE_CODECS = {
    "opus": (["-c:a", "libopus", "-b:a", "24k", "-application", "voip"], "opus"),
    "flac": (["-c:a", "flac"], "flac"),
}


def _transcode_one(ff: list, w: Path, codec_args: list, ext: str) -> dict:
    """Transcoda um WAV para `ext` (16 kHz mono); em sucesso remove o WAV e devolve
    o resultado. Thread-safe — cada stream é um arquivo independente, então os
    streams (mic + loopback) podem ser transcodados em paralelo."""
    import subprocess

    out = w.with_suffix("." + ext)
    cmd = ff + ["-hide_banner", "-loglevel", "error", "-y", "-i", str(w),
                "-ac", "1", "-ar", "16000", *codec_args, str(out)]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:  # ffmpeg sumiu no meio, etc.
        return {"wav": w, "ok": False, "err": str(e)}
    if r.returncode == 0 and out.exists() and out.stat().st_size > 0:
        before = w.stat().st_size
        w.unlink(missing_ok=True)
        return {"wav": w, "ok": True, "out": out, "before": before, "after": out.stat().st_size}
    out.unlink(missing_ok=True)  # não deixa arquivo parcial
    return {"wav": w, "ok": False, "rc": r.returncode}


def archive_audio(folder: Path, cfg) -> None:
    """Pós-transcrição: encolhe o áudio guardado conforme [audio].archive_format.

    keep_audio=false   → apaga o áudio (nada guardado).
    archive_format=wav → mantém o WAV cru (comportamento antigo).
    opus/flac          → transcoda para 16 kHz mono e remove o WAV — sem nunca
                         apagar o original antes de confirmar o destino gravado.
    """
    from . import util

    wavs = sorted(folder.glob("*.wav"))
    if not wavs:
        return
    if not cfg.audio.keep_audio:
        for w in wavs:
            w.unlink(missing_ok=True)
        # registra no meta que o áudio foi apagado DE PROPÓSITO (não é "sem áudio"):
        # mantém o audit-trail (LGPD) e impede que um 'scriba transcribe' posterior
        # marque no_audio enganosamente. Espelha o ramo opus/flac, que também
        # atualiza o meta. Escrita atômica = padrão do projeto.
        meta_path = folder / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["audio_removed"] = True
            for st in (meta.get("streams") or {}).values():
                st["file"] = None  # ponteiro morto: o WAV não existe mais
            util_mod.atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))
        except Exception as e:
            print(f"AVISO: não marquei audio_removed no meta.json ({e})")
        print("áudio removido (keep_audio = false)")
        return

    fmt = (cfg.audio.archive_format or "wav").strip().lower()
    if fmt in ("", "wav", "raw"):
        return  # mantém o WAV cru
    codec = _ARCHIVE_CODECS.get(fmt)
    if codec is None:
        print(f"AVISO: archive_format '{fmt}' desconhecido — mantendo .wav")
        return
    ff = util.ffmpeg_command()
    if ff is None:
        print(f"AVISO: ffmpeg ausente no PATH — não compactei para {fmt}, mantendo .wav")
        return

    codec_args, ext = codec
    renamed: dict[str, str] = {}
    saved = 0
    # Transcoda os streams (mic + loopback) em PARALELO: ffmpeg é processo externo
    # (libera o GIL) e cada stream é um arquivo independente, então roda os 2 ao
    # mesmo tempo — corta ~metade do tempo de arquivamento. Cap em 2 (são 2 streams;
    # não toca a GPU). A ordem dos resultados acompanha `wavs` (ThreadPoolExecutor.map).
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda w: _transcode_one(ff, w, codec_args, ext), wavs))
    for res in results:
        w = res["wav"]
        if res["ok"]:
            renamed[w.name] = res["out"].name
            saved += res["before"] - res["after"]
            print(f"  {w.name} → {res['out'].name} "
                  f"({res['before'] / 1e6:.0f} MB → {res['after'] / 1e6:.1f} MB)")
        elif "err" in res:
            print(f"AVISO: transcode de {w.name} falhou ({res['err']}); mantendo .wav")
        else:
            print(f"AVISO: ffmpeg falhou em {w.name} (rc={res.get('rc')}); mantendo .wav")

    if not renamed:
        return
    # aponta o meta.json para os arquivos novos (uma re-transcrição precisa achá-los)
    meta_path = folder / "meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        for st in (meta.get("streams") or {}).values():
            if st.get("file") in renamed:
                st["file"] = renamed[st["file"]]
                st["rate"] = 16000
                st["channels"] = 1
        meta["archive_format"] = fmt
        util_mod.atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"AVISO: não atualizei o meta.json pós-compactação ({e})")
    print(f"áudio compactado para {fmt}: ~{saved / 1e6:.0f} MB economizados")


def process_when_ready(folder: Path, poll_seconds: float = 2.0) -> int:
    """Espera a gravação terminar e SÓ ENTÃO carrega o modelo e processa.

    O modelo (e a VRAM da GPU) só é tocado quando a call termina — nada de
    segurar a GPU durante a reunião inteira.
    """
    folder = Path(folder)
    meta_path = folder / "meta.json"

    last_size, last_change = -1, time.monotonic()
    while True:
        try:
            status = json.loads(meta_path.read_text(encoding="utf-8")).get("status")
        except (OSError, ValueError):
            status = None
        if status in ("recorded", "transcribed"):
            break
        # preso no MEIO do processamento (crash/kill durante transcrição,
        # diarização ou resumo): sem .lock ativo não há worker vivo - readota,
        # espelhando o scan_pending do boot (process_folder re-checa o .lock)
        if status in ("transcribing", "diarizing", "summarizing") and not util_mod.is_locked(folder):
            break
        if status in ("discarded", "too_short", "done"):
            print(f"nada a processar (status: {status})")
            return 0
        # gravação órfã (app morreu no meio): wavs parados há 2 min
        size = sum((p.stat().st_size for p in folder.glob("*.wav")), 0)
        now = time.monotonic()
        if size != last_size:
            last_size, last_change = size, now
        elif now - last_change > 120:
            print("gravação parece órfã — fica para o processamento de pendentes")
            return 0
        time.sleep(poll_seconds)

    return 0 if process_folder(folder) else 1

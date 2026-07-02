"""Corte retroativo de uma gravação já processada em duas (#37).

Quando duas calls consecutivas foram gravadas juntas (ver #34), este módulo divide
a pasta em duas no offset dado, SEM re-transcrever nem re-diarizar: fatia o
transcript.json por tempo, corta o áudio com ffmpeg `-c copy` e re-resume cada
parte. A diarização já feita (que às vezes distingue vozes homônimas) é preservada
- é a receita validada na cirurgia manual de 2026-07-02.

Segurança: os áudios originais viram `*.orig` e só são apagados quando as DUAS
partes reprocessam com sucesso; nada é destruído antes disso.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from . import util

_TERMINAL_STATUSES = ("done", "failed")  # gravação parada, seguro de cortar
_MIN_MARGIN = 5.0  # o offset tem de sobrar >= 5 s de cada lado


class SplitError(Exception):
    """Erro de validação/execução do corte, com mensagem pronta para o usuário."""


# --------------------------------------------------------------- lógica pura --

def parse_offset(text: str) -> float:
    """'HH:MM:SS', 'MM:SS' ou 'SS' -> segundos (float). ValueError se inválido."""
    parts = (text or "").strip().split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"offset inválido: {text!r} (use HH:MM:SS, MM:SS ou SS)")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        raise ValueError(f"offset inválido: {text!r} (use HH:MM:SS, MM:SS ou SS)")
    if any(n < 0 for n in nums):
        raise ValueError(f"offset negativo: {text!r}")
    secs = 0.0
    for n in nums:  # base-60: [h,m,s] -> ((h*60)+m)*60+s
        secs = secs * 60 + n
    return secs


def slice_transcript(turns: list[dict], offset: float) -> tuple[list[dict], list[dict]]:
    """Fatia os turnos em (parte1, parte2) pelo offset.

    Cada turno vai INTEIRO para a parte onde COMEÇA (não dá para partir o texto de
    um turno sem timestamps por palavra); a parte 2 rebaseia os tempos para o zero.
    Um turno que atravessa a fronteira fica na parte 1 - aceitável e visível.
    """
    part1: list[dict] = []
    part2: list[dict] = []
    for t in turns:
        start = float(t.get("start", 0.0))
        if start < offset:
            part1.append(dict(t))
        else:
            t2 = dict(t)
            t2["start"] = round(max(0.0, start - offset), 3)
            t2["end"] = round(max(0.0, float(t.get("end", 0.0)) - offset), 3)
            part2.append(t2)
    return part1, part2


def split_meta(
    meta: dict, offset: float,
    part1_audio: dict[str, float], part2_audio: dict[str, float],
) -> tuple[dict, dict]:
    """Deriva os metas das duas partes a partir do meta original.

    part{1,2}_audio: {chave do stream -> audio_seconds do trecho cortado}.
    Limpa title/client/export_path/speakers_recognized (o re-resumo regenera) e
    zera o meeting_title da parte 2 (a janela não foi recapturada -> a IA titula).
    """
    started = datetime.fromisoformat(meta["started_at"])
    cut_at = started + timedelta(seconds=offset)
    total = float(meta.get("duration_seconds", 0.0))
    _CLEAR = ("title", "client", "export_path", "speakers_recognized", "error")

    meta1 = json.loads(json.dumps(meta))  # cópia funda
    meta1["ended_at"] = cut_at.isoformat(timespec="seconds")
    meta1["duration_seconds"] = round(offset, 1)
    meta1["status"] = "transcribed"
    for key, st in meta1.get("streams", {}).items():
        if key in part1_audio:
            st["audio_seconds"] = round(part1_audio[key], 1)
    for k in _CLEAR:
        meta1.pop(k, None)
    # meeting_title mantido: a parte 1 é a reunião cujo título foi capturado

    meta2 = json.loads(json.dumps(meta))
    meta2["started_at"] = cut_at.isoformat(timespec="seconds")
    # ended_at herda o original
    meta2["duration_seconds"] = round(max(0.0, total - offset), 1)
    meta2["status"] = "transcribed"
    meta2["meeting_title"] = ""
    for key, st in meta2.get("streams", {}).items():
        if key in part2_audio:
            st["audio_seconds"] = round(part2_audio[key], 1)
        st["offset_seconds"] = 0.0  # o -ss do corte zera o início do stream 2
    for k in _CLEAR:
        meta2.pop(k, None)
    return meta1, meta2


# ------------------------------------------------------------- orquestração --

def _run_ffmpeg(ff: list[str], args: list[str]) -> None:
    proc = subprocess.run(ff + args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SplitError("ffmpeg falhou: " + (proc.stderr or "").strip()[-300:])


def _audio_duration(path: Path) -> float | None:
    """Duração do áudio em segundos via ffprobe, ou None se ffprobe indisponível."""
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True,
        )
        return float(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None
    except (ValueError, OSError):
        return None


def _sibling_folder(base_dir: Path, when: datetime) -> Path:
    """Cria a pasta da parte 2 no padrão ano/mês/dia/HH-MM (com colisão _N)."""
    base = (base_dir / when.strftime("%Y") / when.strftime("%m")
            / when.strftime("%d") / when.strftime("%H-%M"))
    folder, n = base, 1
    while folder.exists():
        n += 1
        folder = base.with_name(f"{base.name}_{n}")
    folder.mkdir(parents=True)
    return folder


def split_recording(folder: Path, offset: float, *, reprocess: bool = True) -> tuple[Path, Path]:
    """Divide a gravação de `folder` em duas no `offset` (segundos).

    Retorna (pasta_parte1, pasta_parte2). A parte 1 REUSA a pasta original (mesmo
    started_at -> mesma nota, que é sobrescrita); a parte 2 ganha uma pasta irmã.
    `reprocess=False` deixa as duas prontas para `scribadev summarize` mas não
    chama o re-resumo (usado nos testes de integração).
    """
    folder = Path(folder)
    ff = util.ffmpeg_command()
    if ff is None:
        raise SplitError("ffmpeg não está no PATH — é pré-requisito para cortar o áudio")

    meta_path = folder / "meta.json"
    transcript_path = folder / "transcript.json"
    if not meta_path.exists():
        raise SplitError(f"{folder} não tem meta.json")
    if not transcript_path.exists():
        raise SplitError(f"{folder} não tem transcript.json — nada para fatiar")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("status") not in _TERMINAL_STATUSES:
        raise SplitError(
            f"status '{meta.get('status')}' não é terminal — espere a reunião terminar de processar"
        )
    if util.is_locked(folder):
        raise SplitError(f"{folder.name} está em processamento (.lock ativo)")

    total = float(meta.get("duration_seconds", 0.0))
    if not (_MIN_MARGIN < offset < total - _MIN_MARGIN):
        raise SplitError(
            f"offset {offset:.0f}s fora do intervalo válido "
            f"({_MIN_MARGIN:.0f}s .. {total - _MIN_MARGIN:.0f}s desta gravação de {total:.0f}s)"
        )

    streams = meta.get("streams", {})
    src = {key: folder / st["file"] for key, st in streams.items() if st.get("file")}
    missing = [str(p) for p in src.values() if not p.exists()]
    if missing:
        raise SplitError("áudio ausente: " + ", ".join(missing))

    # 1) fatiar o transcript (rápido e determinístico; preserva a diarização)
    turns = json.loads(transcript_path.read_text(encoding="utf-8"))
    part1_turns, part2_turns = slice_transcript(turns, offset)

    # 2) cortar cada stream para arquivos temporários e medir a duração real.
    # Mantém a extensão original (.opus/.flac/.wav): o ffmpeg infere o container pela
    # extensão do arquivo de saída — um ".tmp" daria "Invalid argument". Qualquer falha
    # aqui limpa TODOS os temporários (os originais ainda estão intactos).
    def _tmp(path: Path, part: str) -> Path:
        return path.parent / (path.stem + part + path.suffix)

    tmp1: dict[str, Path] = {}
    tmp2: dict[str, Path] = {}
    part1_audio: dict[str, float] = {}
    part2_audio: dict[str, float] = {}
    try:
        for key, path in src.items():
            t1, t2 = _tmp(path, ".p1"), _tmp(path, ".p2")
            # -c copy: corte sem recodificar (fronteira de página ~0,5 s p/ opus; ok p/
            # playback). -t no fim (parte 1) e -ss antes do -i (parte 2, rápido).
            _run_ffmpeg(ff, ["-y", "-i", str(path), "-t", f"{offset:.3f}", "-c", "copy", str(t1)])
            _run_ffmpeg(ff, ["-y", "-ss", f"{offset:.3f}", "-i", str(path), "-c", "copy", str(t2)])
            if not (t1.exists() and t2.exists() and t1.stat().st_size and t2.stat().st_size):
                raise SplitError(f"corte de {path.name} produziu arquivo vazio")
            tmp1[key], tmp2[key] = t1, t2
            part1_audio[key] = _audio_duration(t1) or offset
            part2_audio[key] = _audio_duration(t2) or max(0.0, total - offset)
    except BaseException:
        for path in src.values():
            _cleanup([_tmp(path, ".p1"), _tmp(path, ".p2")])
        raise

    # 3) montar os metas e a pasta irmã da parte 2
    meta1, meta2 = split_meta(meta, offset, part1_audio, part2_audio)
    folder2 = _sibling_folder(_base_dir_for(folder), datetime.fromisoformat(meta2["started_at"]))

    # 4) commit em 3 passadas: PRIMEIRO preserva todos os originais como *.orig
    # (recuperável se algo falhar adiante), depois posiciona as duas partes
    orig: list[Path] = []
    for key, path in src.items():
        keep = path.parent / (path.name + ".orig")
        path.replace(keep)
        orig.append(keep)
    for key, path in src.items():
        tmp1[key].replace(path)                                 # parte 1: pasta original
    for key, path in src.items():
        shutil.move(str(tmp2[key]), str(folder2 / path.name))   # parte 2: pasta nova

    _write_json(transcript_path, part1_turns)
    _write_json(folder2 / "transcript.json", part2_turns)
    _write_json(meta_path, meta1)
    _write_json(folder2 / "meta.json", meta2)
    # voices.json (reconhecimento de voz) segue válido: copia p/ a parte 2, mantém na 1
    voices = folder / "voices.json"
    if voices.exists():
        shutil.copyfile(voices, folder2 / "voices.json")
    _cleanup([folder / "notas.md"])  # a nota da parte 1 será regravada pelo re-resumo

    if not reprocess:
        return folder, folder2

    # 5) re-resume as duas (NÃO re-transcreve): a parte 1 regrava a nota antiga
    from . import notes
    ok1 = notes.build_notes(folder) is not None
    ok2 = notes.build_notes(folder2) is not None
    if ok1 and ok2:
        _cleanup(orig)  # só descarta os originais com as duas notas prontas
    else:
        raise SplitError(
            f"corte feito, mas o re-resumo falhou (originais preservados em *.orig). "
            f"Rode: scribadev summarize \"{folder}\" e \"{folder2}\""
        )

    # 6) o índice é cache derivado das pastas: reconstrói do zero (some a fundida)
    try:
        from . import meetings_index
        meetings_index.reindex()
    except Exception:
        pass
    return folder, folder2


def _base_dir_for(folder: Path) -> Path:
    """Raiz da árvore ano/mês/dia que contém a pasta (para criar a parte 2 como irmã).

    Uso normal: <gravacoes>/YYYY/MM/DD/HH-MM -> raiz = parents[3]. Pasta legada plana
    (fora da árvore): a parte 2 vira irmã direta do mesmo diretório.
    """
    p = folder.resolve()
    parents = p.parents
    if (len(parents) >= 4 and p.parent.name.isdigit() and len(p.parent.name) == 2
            and parents[1].name.isdigit() and parents[2].name.isdigit()):
        return parents[3]
    return p.parent


def _write_json(path: Path, data) -> None:
    util.atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def _cleanup(paths: list[Path]) -> None:
    for p in paths:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass

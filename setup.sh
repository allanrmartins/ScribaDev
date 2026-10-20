#!/bin/bash
# Instalação do ScribaDev no macOS (espelho do setup.ps1; épico #104 / docs/port-mac.md).
# Uso: ./setup.sh   (na raiz do repositório)
set -u

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$HOME/Library/Application Support/ScribaDev"
VENV="$APP_DIR/venv"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "Este script é para macOS. No Linux, siga docs/port-linux.md (pip install -e .)."
    exit 1
fi

echo "== [1/7] Python 3.12+ =="
PY=""
for cand in python3.14 python3.13 python3.12 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
        if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
            PY="$cand"; break
        fi
    fi
done
if [[ -z "$PY" ]]; then
    echo "FALHA: nenhum Python 3.12+ encontrado. Instale com: brew install python@3.12"
    exit 1
fi
echo "usando: $PY ($("$PY" --version))"

echo "== [2/7] venv em $VENV =="
mkdir -p "$APP_DIR"
if [[ ! -x "$VENV/bin/python" ]]; then
    "$PY" -m venv "$VENV" || { echo "FALHA ao criar o venv"; exit 1; }
fi

echo "== [3/7] GPU =="
echo "macOS não tem CUDA — a transcrição roda na GPU do chip via MLX (Metal); a separação de vozes, via MPS."

echo "== [4/7] pip install -e . =="
"$VENV/bin/pip" install --upgrade pip >/dev/null
"$VENV/bin/pip" install -e "$REPO_DIR" || { echo "FALHA no pip install"; exit 1; }

echo "== [5/7] comandos no PATH =="
# symlink em vez do shim .cmd do Windows; primeiro diretório gravável do PATH, sem sudo
LINKED=""
for dir in /opt/homebrew/bin /usr/local/bin "$HOME/.local/bin"; do
    if [[ -d "$dir" && -w "$dir" ]] || { [[ "$dir" == "$HOME/.local/bin" ]] && mkdir -p "$dir" 2>/dev/null; }; then
        ln -sf "$VENV/bin/scribadev" "$dir/scribadev"
        ln -sf "$VENV/bin/scribadev-tray" "$dir/scribadev-tray"
        LINKED="$dir"
        break
    fi
done
if [[ -n "$LINKED" ]]; then
    echo "scribadev -> $LINKED/scribadev"
    case ":$PATH:" in
        *":$LINKED:"*) ;;
        *) echo "AVISO: $LINKED não está no PATH — adicione ao seu ~/.zshrc: export PATH=\"$LINKED:\$PATH\"" ;;
    esac
else
    echo "AVISO: nenhum diretório gravável no PATH — use $VENV/bin/scribadev diretamente"
fi

echo "== [6/7] app lançador (ScribaDev Dev.app) =="
# o app precisa nascer num .app: sem as frases de uso do Info.plist o macOS nega o
# áudio do sistema EM SILÊNCIO (docs/port-mac.md). Abra pelo Launchpad/Spotlight.
"$VENV/bin/python3" "$REPO_DIR/installer/macos/make_source_app.py" "$VENV" \
    || echo "AVISO: não consegui criar o ScribaDev Dev.app — rode $VENV/bin/scribadev-tray (o áudio do sistema pode sair zerado)"

# ffmpeg: não-fatal (mesma regra do ffmpeg_status: sem ele o áudio fica em WAV cru)
if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "AVISO: ffmpeg ausente — o áudio guardado fica em WAV cru (~1,3 GB/h). Instale: brew install ffmpeg"
fi

echo "== [7/7] modelo Whisper (pré-download; não-fatal) =="
"$VENV/bin/python" -m scriba.predownload || echo "AVISO: pré-download falhou — o modelo baixa na primeira transcrição"

echo
"$VENV/bin/scribadev" doctor

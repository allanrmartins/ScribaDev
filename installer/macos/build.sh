#!/usr/bin/env bash
# Build do ScribaDev.app + DMG (macOS, #143 do épico #138).
#
# Requisitos: macOS 14+, python3 com as deps do projeto (pip install -e .) e
# pyinstaller. Ferramentas nativas: sips/iconutil (icns) e hdiutil (DMG).
#
# Uso:  installer/macos/build.sh [pasta-de-saida]     (default: installer/macos/out)
# Env:  PYTHON=<python a usar>                        (default: python3)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
OUT="${1:-$HERE/out}"
PY="${PYTHON:-python3}"

VER="$("$PY" - <<EOF
import re, pathlib
print(re.search(r'__version__\s*=\s*"([^"]+)"',
      pathlib.Path("$REPO/scriba/__init__.py").read_text()).group(1))
EOF
)"
echo "== ScribaDev v$VER (macOS)"

# 1) .icns a partir do scriba.png (sips + iconutil, sem dependência nova)
ICONSET="$HERE/build/scriba.iconset"
mkdir -p "$ICONSET"
for s in 16 32 64 128 256 512; do
  sips -z "$s" "$s" "$REPO/scriba/assets/scriba.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  d=$((s * 2))
  sips -z "$d" "$d" "$REPO/scriba/assets/scriba.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$HERE/build/scriba.icns"
echo "== icns ok"

# 2) PyInstaller (.app com CLI dentro)
"$PY" -m PyInstaller "$HERE/scribadev-mac.spec" --noconfirm \
  --workpath "$HERE/build/pyi" --distpath "$HERE/dist"
echo "== .app ok"

# 3) DMG: staging com o .app + atalho p/ /Applications
STAGE="$HERE/build/dmg"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R "$HERE/dist/ScribaDev.app" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
mkdir -p "$OUT"
DMG="$OUT/ScribaDev-$VER.dmg"
rm -f "$DMG"
hdiutil create -volname "ScribaDev $VER" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
echo "== OK: $DMG ($(du -h "$DMG" | cut -f1))"

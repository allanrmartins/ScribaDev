"""Gera o ícone do ScribaDev — um pergaminho escrito por um raio (tile teal + marca </>).

Desenha tudo com Pillow (sem dependência de rasterizador SVG), em 4x de
supersampling, e empacota um .ico multi-resolução + um .png 256 para a UI.

Uso:  python tools/make_icon.py
Saída: scriba/assets/scriba.ico, scriba/assets/scriba.png,
       scriba/assets/scriba_rec.png  (variante "gravando")
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

N = 256          # tamanho base
S = 4            # supersampling
W = N * S        # canvas de trabalho

ASSETS = Path(__file__).resolve().parent.parent / "scriba" / "assets"

# paleta
TILE_TOP = (22, 120, 100)     # teal claro (ScribaDev — distingue da Scriba, que é indigo)
TILE_BOT = (8, 44, 40)        # teal escuro
PARCH = (243, 228, 178)       # creme do pergaminho
PARCH_EDGE = (214, 192, 130)  # borda/sombra do creme
ROLL = (231, 211, 152)        # rolos do pergaminho
ROLL_END = (198, 172, 104)    # miolo dos rolos
INK = (168, 138, 78)          # linhas de "texto"
BOLT = (255, 206, 58)         # ouro do raio
BOLT_EDGE = (245, 158, 35)    # contorno âmbar do raio
BOLT_CORE = (255, 240, 200)   # brilho interno
REC = (229, 69, 69)           # vermelho "gravando"


def _lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _tile(d: ImageDraw.ImageDraw) -> None:
    """Fundo arredondado com gradiente vertical indigo."""
    grad = Image.new("RGB", (1, W))
    gp = grad.load()
    for y in range(W):
        gp[0, y] = _lerp(TILE_TOP, TILE_BOT, y / W)
    grad = grad.resize((W, W))
    mask = Image.new("L", (W, W), 0)
    md = ImageDraw.Draw(mask)
    m = 24 * S
    md.rounded_rectangle([m, m, W - m, W - m], radius=56 * S, fill=255)
    d._image.paste(grad, (0, 0), mask)


def _parchment(img: Image.Image) -> None:
    """Pergaminho (folha + rolos em cima e embaixo) com sombra suave."""
    d = ImageDraw.Draw(img)

    # sombra
    sh = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    sd.rounded_rectangle([86 * S, 92 * S, 178 * S, 188 * S], radius=10 * S, fill=(0, 0, 0, 110))
    sh = sh.filter(ImageFilter.GaussianBlur(7 * S))
    img.alpha_composite(sh)

    bx0, bx1 = 84 * S, 172 * S     # folha
    by0, by1 = 74 * S, 182 * S
    d.rounded_rectangle([bx0, by0, bx1, by1], radius=7 * S, fill=PARCH, outline=PARCH_EDGE, width=S)

    # rolos (cilindros) topo e base
    for cy in (by0, by1):
        d.ellipse([bx0 - 12 * S, cy - 11 * S, bx1 + 12 * S, cy + 11 * S], fill=ROLL, outline=ROLL_END, width=S)
        d.ellipse([bx0 - 4 * S, cy - 4 * S, bx0 + 6 * S, cy + 4 * S], fill=ROLL_END)
        d.ellipse([bx1 - 6 * S, cy - 4 * S, bx1 + 4 * S, cy + 4 * S], fill=ROLL_END)

    # linhas de "texto" — encurtam perto da ponta do raio (como se ele escrevesse)
    lines = [(98, 0.46), (98, 0.60), (98, 0.30)]
    for i, (x0f, wf) in enumerate(lines):
        ly = (96 + i * 18) * S
        x0 = x0f * S
        x1 = x0 + int((bx1 - 14 * S - x0) * wf)
        d.rounded_rectangle([x0, ly, x1, ly + 4 * S], radius=2 * S, fill=INK)


def _bolt(img: Image.Image) -> None:
    """Raio em ouro descendo na diagonal, ponta tocando o pergaminho."""
    # polígono do raio (em coordenadas 256, escalado por S)
    pts = [
        (150, 40), (104, 128), (138, 128),
        (96, 214), (170, 110), (132, 110), (176, 40),
    ]
    poly = [(x * S, y * S) for x, y in pts]

    layer = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    # brilho/halo
    glow = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.polygon(poly, fill=(255, 210, 70, 150))
    glow = glow.filter(ImageFilter.GaussianBlur(5 * S))
    img.alpha_composite(glow)

    ld.polygon(poly, fill=BOLT, outline=BOLT_EDGE)
    # reforça o contorno
    ld.line(poly + [poly[0]], fill=BOLT_EDGE, width=2 * S, joint="curve")
    # núcleo claro
    core = [(x, y + 6) for x, y in [(140, 56), (118, 122), (150, 100), (118, 176)]]
    ld.line([(x * S, y * S) for x, y in core], fill=BOLT_CORE, width=3 * S, joint="curve")
    img.alpha_composite(layer)


def _dev_mark(img: Image.Image) -> None:
    """Marca </> ('dev') no canto inferior esquerdo — distingue da Scriba na bandeja/Iniciar."""
    d = ImageDraw.Draw(img)

    def stroke(pts, color, w):
        d.line([(x * S, y * S) for x, y in pts], fill=color, width=int(w * S), joint="curve")

    chev_l = [(55, 199), (40, 210), (55, 221)]   # <
    slash = [(63, 223), (76, 197)]               # /
    chev_r = [(82, 199), (92, 210), (82, 221)]   # >
    shadow = (6, 34, 31, 235)
    ink = (226, 246, 238)
    for pts in (chev_l, slash, chev_r):          # sombra fina p/ legibilidade no teal
        stroke([(x + 1.5, y + 1.5) for x, y in pts], shadow, 5)
    for pts in (chev_l, slash, chev_r):
        stroke(pts, ink, 5)


def render_base() -> Image.Image:
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    _tile(ImageDraw.Draw(img))
    _parchment(img)
    _bolt(img)
    _dev_mark(img)
    return img.resize((N, N), Image.LANCZOS)


def add_rec_badge(base: Image.Image) -> Image.Image:
    """Variante 'gravando': disco vermelho no canto inferior direito."""
    img = base.copy()
    d = ImageDraw.Draw(img)
    r = 52
    cx, cy = N - r - 8, N - r - 8
    d.ellipse([cx - r // 2 - 6, cy - r // 2 - 6, cx + r // 2 + 6, cy + r // 2 + 6], fill=(255, 255, 255, 255))
    d.ellipse([cx - r // 2, cy - r // 2, cx + r // 2, cy + r // 2], fill=REC + (255,))
    return img


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    base = render_base()
    base.save(ASSETS / "scriba.png")
    add_rec_badge(base).save(ASSETS / "scriba_rec.png")
    sizes = [16, 24, 32, 48, 64, 128, 256]
    base.save(ASSETS / "scriba.ico", sizes=[(s, s) for s in sizes])
    print("ok ->", ASSETS)


if __name__ == "__main__":
    main()

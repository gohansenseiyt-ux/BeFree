"""
theme_sumi.py — Design system "Sumi" (encre de nuit) pour BeFree.
Palette + polices + helpers, portes depuis la refonte visuelle Claude Design
(direction 1b, dojo/kendo sombre).
"""
import ctypes
import os

from PIL import Image, ImageDraw
import customtkinter as ctk

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR = os.path.join(BASE_DIR, "fonts")

# ── Palette ──
SUMI = "#0A0908"           # fond app
SUMI_2 = "#141210"         # sidebar, cartes
SURFACE = "#1F1B18"        # elevation
PAPER = "#F2E8D3"          # feuillet contrat
INK = "#E8DFCE"            # texte primaire
INK_2 = "#B8AF9E"          # texte secondaire
MUTED = "#8A8071"          # meta, hints
RULE = "#2A2622"           # bordures
HANKO = "#E63946"          # accent, hardcore
HANKO_DEEP = "#A82230"     # hover, danger, mode negatif
GOLD = "#D4A24C"           # progression, grade
MOSS = "#7A9B5C"           # succes

# Teintes derivees (hover/pression/ombre) non fournies telles quelles par le
# design mais necessaires pour reskinner des etats (hover, fond de badge...)
# que le mockup ne detaille pas explicitement. Deduites par interpolation
# dans la meme famille de teinte que leur ancre du design.
SUMI_HOVER = "#1A1714"
SURFACE_HOVER = "#28231F"
RULE_LIGHT = "#3A352E"       # bordure claire (ex: etat inactif plus visible que RULE)
MUTED_DEEP = "#5C574C"       # texte tres attenue / desactive

HANKO_HOVER = "#C4303C"
HANKO_LIGHT = "#EA5561"      # rouge clair (alerte texte sur fond sombre)
HANKO_MUTED = "#5C3A38"      # rouge desature (bordures, etats discrets)
HANKO_SHADOW = "#241012"     # quasi-noir teinte rouge (fonds profonds)

GOLD_DEEP = "#B3822F"
GOLD_LIGHT = "#E8C99A"
GOLD_SHADOW = "#3D2E17"

MOSS_DEEP = "#5C7A46"
MOSS_SHADOW = "#16210F"
MOSS_SHADOW_HOVER = "#1C2913"

PAPER_DEEP = "#E3D6B8"
INK_HOVER = "#D8CFC0"

# Tokens supplementaires rencontres dans les ecrans du design (feuillet papier,
# champs sur papier, fond du code de deblocage d'urgence)
INK_DARK = "#1A1613"        # encre foncee sur papier (texte du contrat)
INK_DARK_2 = "#4A4239"      # texte secondaire sur papier
PAPER_LIGHT = "#FBF8F1"     # fond ivoire clair pour champs sur papier
HANKO_FIELD = "#8A1D25"     # fond du champ code de deblocage (verrouillage)

# ── Polices (familles enregistrees au runtime, voir register_fonts) ──
FONT_SERIF = "Cormorant Garamond Medium"
FONT_SERIF_SEMIBOLD = "Cormorant Garamond SemiBold"
FONT_MONO = "JetBrains Mono"
FONT_MONO_MEDIUM = "JetBrains Mono Medium"
FONT_UI = "Segoe UI"

_FONT_FILES = [
    "CormorantGaramond-Regular.ttf",
    "CormorantGaramond-Medium.ttf",
    "CormorantGaramond-SemiBold.ttf",
    "JetBrainsMono-Regular.ttf",
    "JetBrainsMono-Medium.ttf",
    "JetBrainsMono-Bold.ttf",
]

FR_PRIVATE = 0x10
_fonts_registered = False


def register_fonts():
    """Charge les polices custom dans le process courant (pas d'install systeme,
    pas de droits admin requis). Idempotent."""
    global _fonts_registered
    if _fonts_registered:
        return
    _fonts_registered = True
    try:
        gdi32 = ctypes.windll.gdi32
    except Exception:
        return
    for filename in _FONT_FILES:
        path = os.path.join(FONTS_DIR, filename)
        if os.path.exists(path):
            gdi32.AddFontResourceExW(path, FR_PRIVATE, 0)


def serif(size, weight="medium", italic=False):
    """Retourne un tuple de police serif (titres, rangs, wordmark)."""
    family = FONT_SERIF_SEMIBOLD if weight == "semibold" else FONT_SERIF
    if italic:
        return (family, size, "italic")
    return (family, size)


def mono(size, weight="normal"):
    """Retourne un tuple de police mono (donnees, minuteur, meta)."""
    if weight == "bold":
        return (FONT_MONO, size, "bold")
    if weight == "medium":
        return (FONT_MONO_MEDIUM, size)
    return (FONT_MONO, size)


def ui(size, weight="normal"):
    """Retourne un tuple de police UI standard (corps, boutons, formulaires)."""
    if weight == "bold":
        return (FONT_UI, size, "bold")
    return (FONT_UI, size)


# ── Icones de navigation (dessinees au runtime, pas d'assets binaires) ──
# Trait fin monochrome, 2 teintes precuites (repos / actif), fidele au
# systeme d'icones documente dans le design ("raffinement v2").
_ICON_SIZE = 40   # rendu interne @2x, affiche a 20x20 via CTkImage
_ICON_STROKE = 3


def _icon_canvas():
    img = Image.new("RGBA", (_ICON_SIZE, _ICON_SIZE), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def _draw_accueil(color):
    """Torii minimal : deux barres horizontales + deux montants verticaux."""
    img, d = _icon_canvas()
    d.line([(3, 9), (37, 9)], fill=color, width=_ICON_STROKE)
    d.line([(7, 16), (33, 16)], fill=color, width=_ICON_STROKE)
    d.line([(11, 9), (11, 35)], fill=color, width=_ICON_STROKE)
    d.line([(29, 9), (29, 35)], fill=color, width=_ICON_STROKE)
    return img


def _draw_demarrer(color):
    """Cercle + point central."""
    img, d = _icon_canvas()
    d.ellipse([7, 7, 33, 33], outline=color, width=_ICON_STROKE)
    d.ellipse([17, 17, 23, 23], fill=color)
    return img


def _draw_statistiques(color):
    """3 barres verticales de hauteurs croissantes."""
    img, d = _icon_canvas()
    d.rectangle([5, 22, 13, 34], outline=color, width=_ICON_STROKE)
    d.rectangle([16, 14, 24, 34], outline=color, width=_ICON_STROKE)
    d.rectangle([27, 6, 35, 34], outline=color, width=_ICON_STROKE)
    return img


def _draw_applications(color):
    """Grille 2x2 de 4 carres."""
    img, d = _icon_canvas()
    for x0, y0 in ((5, 5), (22, 5), (5, 22), (22, 22)):
        d.rectangle([x0, y0, x0 + 13, y0 + 13], outline=color, width=_ICON_STROKE)
    return img


def _draw_sites(color):
    """Globe : cercle + equateur + meridien."""
    img, d = _icon_canvas()
    d.ellipse([5, 5, 35, 35], outline=color, width=_ICON_STROKE)
    d.line([(5, 20), (35, 20)], fill=color, width=2)
    d.ellipse([13, 5, 27, 35], outline=color, width=2)
    return img


def _draw_parametres(color):
    """Molette simplifiee : cercle + 4 traits cardinaux."""
    img, d = _icon_canvas()
    d.ellipse([13, 13, 27, 27], outline=color, width=_ICON_STROKE)
    d.line([(20, 2), (20, 10)], fill=color, width=_ICON_STROKE)
    d.line([(20, 30), (20, 38)], fill=color, width=_ICON_STROKE)
    d.line([(2, 20), (10, 20)], fill=color, width=_ICON_STROKE)
    d.line([(30, 20), (38, 20)], fill=color, width=_ICON_STROKE)
    return img


_ICON_DRAWERS = {
    "accueil": _draw_accueil,
    "demarrer": _draw_demarrer,
    "stats": _draw_statistiques,
    "apps": _draw_applications,
    "sites": _draw_sites,
    "parametres": _draw_parametres,
}

_nav_icons_cache = None


def build_nav_icons():
    """Construit les 6 icones de nav (repos + actif) une seule fois.
    Retourne {nom: {"rest": CTkImage, "active": CTkImage}}."""
    global _nav_icons_cache
    if _nav_icons_cache is not None:
        return _nav_icons_cache
    icons = {}
    for name, drawer in _ICON_DRAWERS.items():
        icons[name] = {
            "rest": ctk.CTkImage(light_image=drawer(INK_2), size=(20, 20)),
            "active": ctk.CTkImage(light_image=drawer(HANKO), size=(20, 20)),
        }
    _nav_icons_cache = icons
    return icons

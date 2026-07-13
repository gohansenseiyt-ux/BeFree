"""
theme_sumi.py — Design system "Sumi" (encre de nuit) pour BeFree.
Palette + polices + helpers, portes depuis la refonte visuelle Claude Design
(direction 1b, dojo/kendo sombre).
"""
import ctypes
import os

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

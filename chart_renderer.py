"""
chart_renderer.py — Rendu graphique Matplotlib Cyber-Bunker.
generate_chart : temporel (barres pour jour, ligne+aire pour semaine/mois).
generate_app_chart : par application (onglet Total) avec barre TOTAL conditionnelle.
"""

import os
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.ticker as ticker
import matplotlib.font_manager as font_manager

import theme_sumi
from i18n import t

_FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
for _f in ("JetBrainsMono-Regular.ttf", "JetBrainsMono-Bold.ttf"):
    _path = os.path.join(_FONTS_DIR, _f)
    if os.path.exists(_path):
        font_manager.fontManager.addfont(_path)

MONO_FONT = "JetBrains Mono"

_DEFAULT_COLORS = {
    "bg": theme_sumi.SUMI_2, "crimson": theme_sumi.HANKO_DEEP,
    "bar_fill": theme_sumi.SURFACE, "text_dim": theme_sumi.MUTED,
    "grid": theme_sumi.SURFACE,
}

APP_COLORS = {
    "VS Code": "#007ACC", "Discord": "#5865F2", "Roblox Studio": "#C0C0C0",
    "Roblox": "#E8DFCE", "Navigateur": "#5C574C", "Focus Global": "#8A8071",
    "Chrome": "#4285F4", "Firefox": "#FF7139", "Brave": "#FB542B",
    "Edge": "#0078D7", "Steam": "#1B2838", "OBS Studio": "#302C2B",
    "Spotify": "#1DB954", "Slack": "#4A154B", "Teams": "#6264A7",
    "Zoom": "#2D8CFF", "Word": "#2B579A", "Excel": "#217346",
    "PowerPoint": "#D04423", "Outlook": "#0072C6", "Epic Games": "#313131",
    "Origin": "#F56C2D", "Valorant": "#FF4655", "League of Legends": "#C8AA6E",
    "Minecraft": "#4DB848", "IntelliJ": "#FC3751", "PyCharm": "#21D789",
    "Android Studio": "#3DDC84", "Eclipse": "#2C2255", "Notepad++": "#90E59A",
    "Sublime Text": "#FF9800", "Rider": "#DD1265",
}

APP_BADGES = {
    "VS Code": "[VSC]", "Discord": "[DIS]", "Roblox Studio": "[RBX]",
    "Roblox": "[RBX]", "Navigateur": "[NAV]", "Focus Global": "[FCS]",
    "Chrome": "[CHR]", "Firefox": "[FFX]", "Brave": "[BRV]", "Edge": "[EDG]",
    "Steam": "[STM]", "OBS Studio": "[OBS]", "Spotify": "[SPF]",
    "Slack": "[SLK]", "Teams": "[TMS]", "Zoom": "[ZOM]", "Word": "[WRD]",
    "Excel": "[XLS]", "PowerPoint": "[PPT]", "Outlook": "[OUT]",
    "Epic Games": "[EGS]", "Origin": "[ORG]", "Valorant": "[VAL]",
    "League of Legends": "[LOL]", "Minecraft": "[MNC]", "IntelliJ": "[IJ]",
    "PyCharm": "[PYC]", "Android Studio": "[ADR]", "Eclipse": "[ECL]",
    "Notepad++": "[NPP]", "Sublime Text": "[SUB]", "Rider": "[RDR]",
}

_BG = theme_sumi.SUMI


def _smooth_curve(x, y, num_points=300):
    """Interpole les points avec une courbe cubique lisse.
    Utilise scipy.CubicSpline si disponible, sinon numpy.polyfit.
    """
    x_arr = np.array(x, dtype=float)
    y_arr = np.array(y, dtype=float)

    if len(x) < 3:
        # Pas assez de points → retourne les points bruts
        return x_arr, y_arr

    try:
        from scipy.interpolate import CubicSpline
        cs = CubicSpline(x_arr, y_arr, bc_type='natural')
        x_smooth = np.linspace(x_arr[0], x_arr[-1], num_points)
        y_smooth = cs(x_smooth)
        return x_smooth, y_smooth
    except ImportError:
        # Fallback : fit polynomial degré min(3, n-1)
        deg = min(3, len(x) - 1)
        coeffs = np.polyfit(x_arr, y_arr, deg)
        poly = np.poly1d(coeffs)
        x_smooth = np.linspace(x_arr[0], x_arr[-1], num_points)
        y_smooth = poly(x_smooth)
        # Clamp pour éviter les valeurs négatives
        y_smooth = np.maximum(y_smooth, 0)
        return x_smooth, y_smooth


# ═══════════════════════════════════════════════════════════════
#  FONCTION 1 : generate_chart (temporel)
#  chart_type='bar'  → barres verticales (Aujourd'hui)
#  chart_type='line' → courbe + aire (Semaine / Mois)
# ═══════════════════════════════════════════════════════════════

def generate_chart(parent_frame, labels, valeurs,
                   highlight_idx=None, colors=None, chart_type='bar'):
    """Graphique temporel — barres (jour) ou courbe+aire (semaine/mois)."""
    pal = dict(_DEFAULT_COLORS)
    if colors:
        pal.update(colors)

    for widget in parent_frame.winfo_children():
        widget.destroy()

    fig = Figure(figsize=(6.2, 3.4), dpi=100,
                 facecolor=_BG, edgecolor=_BG)
    ax = fig.add_subplot(111, facecolor=_BG)

    total = sum(valeurs)
    if total == 0:
        _draw_empty_state(ax, pal)
        fig.tight_layout(pad=1.5)
        canvas = FigureCanvasTkAgg(fig, master=parent_frame)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)
        parent_frame._current_fig = fig
        return

    x = list(range(len(valeurs)))
    max_v = max(valeurs)

    if chart_type == 'line':
        _draw_line_plot(ax, x, valeurs, labels, max_v, highlight_idx, pal)
    else:
        _draw_bar_chart(ax, x, valeurs, max_v, highlight_idx, pal)

    fig.tight_layout(pad=1.5)
    canvas = FigureCanvasTkAgg(fig, master=parent_frame)
    canvas.draw()
    canvas.get_tk_widget().pack(fill="both", expand=True)
    parent_frame._current_fig = fig


# ═══════════════════════════════════════════════════════════════
#  SOUS-FONCTIONS DE RENDU
# ═══════════════════════════════════════════════════════════════

def _draw_bar_chart(ax, x, valeurs, max_v, highlight_idx, pal):
    """Barres verticales — utilisées pour la vue Aujourd'hui."""
    bar_colors = []
    for i, v in enumerate(valeurs):
        if highlight_idx is not None and i == highlight_idx:
            bar_colors.append(pal["crimson"])
        elif v == max_v and max_v > 0 and highlight_idx is None:
            bar_colors.append(pal["crimson"])
        else:
            bar_colors.append(pal["bar_fill"])

    ax.bar(x, valeurs, width=0.4, color=bar_colors, zorder=3)
    _apply_bar_style(ax, x, valeurs, max_v, pal)


def _draw_line_plot(ax, x, valeurs, labels, max_v, highlight_idx, pal):
    """Courbe d'évolution lissée + aire semi-transparente — semaine/mois, style néon."""
    x_arr = np.array(x)
    y_arr = np.array(valeurs)

    # ── Lissage avec interpolation cubique ──
    if len(x) >= 3:
        x_smooth, y_smooth = _smooth_curve(x, valeurs)
    else:
        x_smooth, y_smooth = x_arr, y_arr

    # ── Aire semi-transparente (glow) sous la courbe lissée ──
    ax.fill_between(x_smooth, y_smooth, 0,
                    facecolor=pal["crimson"], alpha=0.08, zorder=2)

    # ── Ligne principale lissée ──
    ax.plot(x_smooth, y_smooth,
            color=pal["crimson"],
            linewidth=1.5,
            solid_capstyle='round',
            zorder=4)

    # ── Marqueurs discrets sur les points réels uniquement ──
    ax.scatter(x_arr, y_arr,
               facecolor=pal["crimson"],
               edgecolor='none',
               s=18,
               zorder=5)

    # ── Surligner le point actif (highlight_idx = aujourd'hui) ──
    if highlight_idx is not None and highlight_idx < len(x_arr):
        ax.scatter([x_arr[highlight_idx]], [y_arr[highlight_idx]],
                   facecolor='none',
                   edgecolor=pal["crimson"],
                   linewidth=1.2,
                   s=50,
                   zorder=6)

    _apply_line_style(ax, x, labels, max_v, pal)


# ═══════════════════════════════════════════════════════════════
#  FONCTION 2 : generate_app_chart (vue Total)
#  show_total=False → pas de barre TOTAL rouge
# ═══════════════════════════════════════════════════════════════

def generate_app_chart(parent_frame, app_data, total_minutes,
                       sort_asc=False, show_total=True, colors=None):
    """Graphique par application + barre TOTAL conditionnelle."""
    pal = dict(_DEFAULT_COLORS)
    if colors:
        pal.update(colors)

    for widget in parent_frame.winfo_children():
        widget.destroy()

    data_sorted = sorted(app_data, key=lambda x: x[1], reverse=not sort_asc)

    if not data_sorted and total_minutes == 0:
        fig = Figure(figsize=(6.2, 3.4), dpi=100,
                     facecolor=_BG, edgecolor=_BG)
        ax = fig.add_subplot(111, facecolor=_BG)
        _draw_empty_state(ax, pal)
        fig.tight_layout(pad=1.5)
        canvas = FigureCanvasTkAgg(fig, master=parent_frame)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)
        parent_frame._current_fig = fig
        return

    if show_total:
        labels = [a[0] for a in data_sorted] + ["TOTAL"]
        valeurs = [a[1] for a in data_sorted] + [total_minutes]
    else:
        labels = [a[0] for a in data_sorted]
        valeurs = [a[1] for a in data_sorted]

    max_v = max(valeurs) if valeurs else 1
    n_apps = len(data_sorted)

    fig = Figure(figsize=(6.2, 3.4), dpi=100,
                 facecolor=_BG, edgecolor=_BG)
    ax = fig.add_subplot(111, facecolor=_BG)

    # Barres applications
    x_apps = list(range(n_apps))
    bar_colors = [APP_COLORS.get(name, pal["bar_fill"]) for name, _ in data_sorted]
    ax.bar(x_apps, [v for _, v in data_sorted], width=0.4,
           color=bar_colors, zorder=3)

    # Barre TOTAL (seulement si show_total)
    if show_total:
        ax.bar([n_apps], [total_minutes], width=0.4,
               color=theme_sumi.HANKO, zorder=3)

    # Badges texte sur les barres d'applications
    for i, (name, minutes) in enumerate(data_sorted):
        if minutes <= 0:
            continue
        y_mid = minutes / 2
        badge = APP_BADGES.get(name, f"[{name[:3].upper()}]")
        try:
            ax.text(i, y_mid, badge,
                    fontfamily=MONO_FONT, fontsize=7, fontweight="bold",
                    color=theme_sumi.INK, ha="center", va="center",
                    bbox=dict(boxstyle="round,pad=0.15",
                              facecolor=theme_sumi.SURFACE_HOVER, edgecolor=theme_sumi.MUTED,
                              linewidth=0.5))
        except Exception:
            pass

    _apply_bar_style(ax, list(range(len(labels))), valeurs, max_v, pal)
    fig.tight_layout(pad=1.5)
    canvas = FigureCanvasTkAgg(fig, master=parent_frame)
    canvas.draw()
    canvas.get_tk_widget().pack(fill="both", expand=True)
    parent_frame._current_fig = fig


# ═══════════════════════════════════════════════════════════════
#  STYLES
# ═══════════════════════════════════════════════════════════════

def _apply_bar_style(ax, x, valeurs, max_v, pal):
    """Style épuré des axes — barres."""
    for side in ("top", "right", "bottom", "left"):
        ax.spines[side].set_visible(False)
    ax.tick_params(axis="both", which="both", length=0)
    ax.tick_params(axis="x", colors=theme_sumi.MUTED, labelsize=9)
    ax.tick_params(axis="y", colors=theme_sumi.MUTED, labelsize=9)

    n = len(valeurs)
    ax.set_xticks(x)
    if n > 12:
        rotation, ha = 45, "right"
    elif n > 8:
        rotation, ha = 20, "right"
    else:
        rotation, ha = 0, "center"
    ax.set_xticklabels(valeurs, fontfamily=MONO_FONT,
                       rotation=rotation, ha=ha) if n else None

    ax.set_ylim(0, max_v * 1.35 if max_v > 0 else 1)

    if max_v < 2:
        ax.yaxis.set_major_formatter(
            ticker.FuncFormatter(lambda x_val, _: f"{int(x_val * 60)}s"))
    else:
        ax.yaxis.set_major_locator(ticker.MaxNLocator(nbins=5, integer=True))
        ax.yaxis.set_major_formatter(
            ticker.FuncFormatter(lambda x_val, _: f"{int(x_val)}m"))

    ax.grid(axis="y", color=pal["grid"],
            linestyle="-", linewidth=0.5)
    ax.set_axisbelow(True)


def _apply_line_style(ax, x, labels, max_v, pal):
    """Style néon Cyber-Bunker — courbe temporelle."""
    # Masquer les bordures haut et droite seulement
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    # Bordures bas et gauche très fines, quasi invisibles
    for side in ("bottom", "left"):
        ax.spines[side].set_color(theme_sumi.SURFACE)
        ax.spines[side].set_linewidth(0.3)

    ax.tick_params(axis="both", which="both", length=0)
    ax.tick_params(axis="x", colors=theme_sumi.MUTED, labelsize=8)
    ax.tick_params(axis="y", colors=theme_sumi.MUTED, labelsize=8)

    n = len(labels)
    ax.set_xticks(range(n))
    if n > 18:
        rotation, ha = 55, "right"
    elif n > 12:
        rotation, ha = 45, "right"
    elif n > 8:
        rotation, ha = 20, "right"
    else:
        rotation, ha = 0, "center"
    ax.set_xticklabels(labels, fontfamily=MONO_FONT,
                       rotation=rotation, ha=ha)

    # Marge haute pour que la courbe respire
    ax.set_ylim(0, max_v * 1.25 if max_v > 0 else 1)

    if max_v < 2:
        ax.yaxis.set_major_formatter(
            ticker.FuncFormatter(lambda x_val, _: f"{int(x_val * 60)}s"))
    else:
        ax.yaxis.set_major_locator(ticker.MaxNLocator(nbins=4, integer=True))
        ax.yaxis.set_major_formatter(
            ticker.FuncFormatter(lambda x_val, _: f"{int(x_val)}m"))

    # Grille pointillée très discrète
    ax.grid(axis="y", color=theme_sumi.HANKO_DEEP, linestyle=":", linewidth=0.4, alpha=0.05)
    ax.set_axisbelow(True)


def _draw_empty_state(ax, pal):
    """État vide — grille pointillée + message."""
    ax.set_xticks([])
    ax.set_yticks([])
    ax.grid(axis="both", color=theme_sumi.SURFACE, linestyle=":", linewidth=0.5)
    ax.set_axisbelow(True)
    ax.text(0.5, 0.5,
            t("chart.aucune_donnee"),
            fontfamily=MONO_FONT, fontsize=11, color=theme_sumi.MUTED,
            ha="center", va="center", transform=ax.transAxes)
    for side in ("top", "right", "bottom", "left"):
        ax.spines[side].set_visible(False)
    ax.tick_params(axis="both", which="both", length=0)
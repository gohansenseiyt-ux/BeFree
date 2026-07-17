"""
ui_elements.py — Écran Statistiques Hardcore Focus
Style Cyber-Bunker français : graphique 100%, tout en français.
"""

import json
import customtkinter as ctk
from datetime import datetime, timedelta, date
import os

from stats_manager import formater_duree, FILTER_LABELS
import chart_renderer
import theme_sumi
from i18n import t


class StatsDashboard:
    """Dashboard stats — graphique 100%, français intégral."""

    DEFAULT_COLORS = {
        "bg": theme_sumi.SUMI, "bg_panel": theme_sumi.SUMI_2,
        "border_dark": theme_sumi.RULE, "crimson": theme_sumi.HANKO_DEEP,
        "secondary": theme_sumi.SURFACE, "text": theme_sumi.INK,
        "text_dim": theme_sumi.MUTED, "text_muted": theme_sumi.MUTED,
        "border_lt": theme_sumi.RULE_LIGHT,
    }

    def __init__(self, parent, stats_manager, colors=None, on_export=None):
        # Construit ici (pas en attribut de classe) : évalué à l'instanciation,
        # après que main.py ait fixé i18n.set_language() au démarrage — un dict
        # de classe serait figé en français, évalué trop tôt à l'import du module.
        self.SEGMENT_LABELS = {
            "jour": t("stats.filtre_jour"), "semaine": t("stats.filtre_semaine"),
            "mois": t("stats.filtre_mois"), "total": t("stats.filtre_total"),
        }
        self.parent = parent
        self.stats = stats_manager
        self.c = colors if colors else self.DEFAULT_COLORS
        self.filter_active = "total"
        self.sort_asc = False
        self.on_export = on_export
        self._build_ui()

    def _build_ui(self):
        """Construit l'UI une seule fois."""
        c = self.c
        parent = self.parent

        # ── En-tête : titre + sous-titre à gauche, export à droite ──
        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.pack(fill="x", padx=32, pady=(28, 0))

        header_gauche = ctk.CTkFrame(header, fg_color="transparent")
        header_gauche.pack(side="left", anchor="w")
        self.lbl_titre = ctk.CTkLabel(header_gauche, text=t("stats.page_titre"),
                                       font=theme_sumi.serif(28), text_color=c["text"],
                                       anchor="w")
        self.lbl_titre.pack(anchor="w")
        ctk.CTkLabel(header_gauche, text=t("stats.page_sous_titre"),
                     font=("Segoe UI", 12), text_color=c["text_dim"],
                     anchor="w").pack(anchor="w", pady=(2, 0))

        lbl_export = ctk.CTkLabel(header, text=t("stats.lien_export"),
                                   font=theme_sumi.mono(10), text_color=c["text_dim"],
                                   cursor="hand2")
        lbl_export.pack(side="right", anchor="e", pady=(6, 0))
        if self.on_export:
            lbl_export.bind("<Button-1>", lambda e: self.on_export())

        # ── Filtres : contrôle segmenté joint, sans emoji, fidèle au mockup ──
        frame_filtres = ctk.CTkFrame(parent, fg_color="transparent")
        frame_filtres.pack(fill="x", padx=32, pady=(18, 8), anchor="w")

        segmente = ctk.CTkFrame(frame_filtres, fg_color="transparent",
                                 border_width=1, border_color=c["border_dark"], corner_radius=0)
        segmente.pack(side="left")

        self.filter_buttons = {}
        for i, nom_filtre in enumerate(["jour", "semaine", "mois", "total"]):
            btn = ctk.CTkButton(
                segmente, text=self.SEGMENT_LABELS[nom_filtre],
                font=theme_sumi.mono(11), fg_color="transparent",
                border_width=0,
                hover_color=c["border_dark"], text_color=c["text_dim"],
                corner_radius=0, height=30, width=90,
                command=lambda n=nom_filtre: self.update_dashboard(n),
            )
            btn.pack(side="left", padx=(1 if i > 0 else 0, 0))
            self.filter_buttons[nom_filtre] = btn

        # ── Bouton TRIER ──
        self.btn_sort = ctk.CTkButton(
            frame_filtres, text=t("stats.trier"),
            font=("JetBrains Mono", 11, "bold"),
            width=130, height=30,
            fg_color="transparent", border_width=1,
            border_color=c["border_dark"],
            hover_color=c["border_dark"], text_color=c["text_dim"],
            corner_radius=0,
            command=self._toggle_sort,
        )
        self.btn_sort.pack(side="left", padx=(6, 0))

        # Activer le filtre "total" par défaut
        self.filter_buttons["total"].configure(
            fg_color=c["text"], text_color=c["bg"])

        # ── Graphique ──
        self.chart_frame = ctk.CTkFrame(parent, fg_color=c["bg_panel"], corner_radius=0,
                                         border_color=c["border_dark"], border_width=1)
        self.chart_frame.pack(fill="both", expand=True, padx=32, pady=(0, 20))

        # Accents L-shape
        ctk.CTkFrame(self.chart_frame, height=2, width=28,
                     fg_color=c["crimson"], corner_radius=0).place(x=0, y=0)
        ctk.CTkFrame(self.chart_frame, height=28, width=2,
                     fg_color=c["crimson"], corner_radius=0).place(x=0, y=0)
        ctk.CTkLabel(self.chart_frame, text="◤", font=("JetBrains Mono", 12, "bold"),
                      text_color=c["crimson"]).place(x=5, y=22)
        ctk.CTkLabel(self.chart_frame, text="◢", font=("JetBrains Mono", 12, "bold"),
                      text_color=c["crimson"]).place(relx=1.0, rely=1.0, x=-16, y=-16)

        self.lbl_chart_title = ctk.CTkLabel(
            self.chart_frame, text=t("stats.titre_analyse_globale"),
            font=theme_sumi.serif(17), text_color=c["text"])
        self.lbl_chart_title.pack(pady=(30, 4))

        self.chart_container = ctk.CTkFrame(self.chart_frame, fg_color="transparent")
        self.chart_container.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # ── Barre stats ──
        bottom_bar = ctk.CTkFrame(self.chart_frame, fg_color="transparent")
        bottom_bar.pack(fill="x", padx=10, pady=(0, 8))

        self.lbl_stat_moyenne = ctk.CTkLabel(
            bottom_bar, text=t("stats.moyenne", v="0 min"),
            font=("JetBrains Mono", 11), text_color=c["text_dim"])
        self.lbl_stat_moyenne.pack(side="left", padx=(0, 20))

        self.lbl_stat_total = ctk.CTkLabel(
            bottom_bar, text=t("stats.total_label", v="0 min"),
            font=("JetBrains Mono", 11), text_color=c["text_dim"])
        self.lbl_stat_total.pack(side="left")

        ctk.CTkLabel(bottom_bar, text="|", font=("JetBrains Mono", 11),
                      text_color=c["border_dark"]).pack(side="left", padx=12)

        self.lbl_stat_sessions = ctk.CTkLabel(
            bottom_bar, text=t("stats.sessions_label", n=0),
            font=("JetBrains Mono", 11), text_color=c["text_dim"])
        self.lbl_stat_sessions.pack(side="left")

    # ─────────────────────────────────────────────────────────
    #  MISE À JOUR
    # ─────────────────────────────────────────────────────────

    def update_dashboard(self, periode):
        """Met à jour filtres + graphique + stats."""
        self.filter_active = periode
        c = self.c

        for name, btn in self.filter_buttons.items():
            if name == periode:
                btn.configure(fg_color=c["text"], text_color=c["bg"])
            else:
                btn.configure(fg_color="transparent", text_color=c["text_dim"])

        sort_arrow = "▲" if self.sort_asc else "▼"
        self.btn_sort.configure(text=t("stats.trier_fleche", fleche=sort_arrow))

        if periode == "total":
            self._prep_total()
        elif periode == "jour":
            self._prep_jour()
        elif periode == "semaine":
            self._prep_semaine()
        elif periode == "mois":
            self._prep_mois()

        data = self._get_data(periode)
        self._refresh_stats(data)

    def _toggle_sort(self):
        self.sort_asc = not self.sort_asc
        if self.filter_active == "total":
            self.update_dashboard("total")

    # ─────────────────────────────────────────────────────────
    #  GRAPHIQUES
    # ─────────────────────────────────────────────────────────

    def _prep_jour(self):
        labels, valeurs = self._get_hourly_data()
        chart_renderer.generate_chart(self.chart_container, labels, valeurs)
        self.lbl_chart_title.configure(text=t("stats.titre_jour"))

    def _prep_semaine(self):
        labels, valeurs = self.stats.get_7day_data()
        chart_renderer.generate_chart(
            self.chart_container, labels, valeurs,
            highlight_idx=6, chart_type='line')
        self.lbl_chart_title.configure(text=t("stats.titre_semaine"))

    def _prep_mois(self):
        labels, valeurs = self._get_weekly_data()
        chart_renderer.generate_chart(
            self.chart_container, labels, valeurs, chart_type='line')
        self.lbl_chart_title.configure(text=t("stats.titre_mois"))

    def _prep_total(self):
        data = self.stats.get_data_total()
        applis = data.get("applis", [])
        total_min = data.get("temps_total", 0)

        chart_renderer.generate_app_chart(
            self.chart_container,
            app_data=applis,
            total_minutes=total_min,
            sort_asc=self.sort_asc,
            show_total=True,
        )
        self.lbl_chart_title.configure(text=t("stats.titre_total"))

    # ─────────────────────────────────────────────────────────
    #  DONNÉES
    # ─────────────────────────────────────────────────────────

    def _get_data(self, periode):
        if periode == "jour":
            return self.stats.get_data_jour()
        elif periode == "semaine":
            return self.stats.get_data_semaine()
        elif periode == "mois":
            return self.stats.get_data_mois()
        return self.stats.get_data_total()

    def _get_hourly_data(self):
        toutes = self._load_sessions()
        today = date.today()
        valeurs = [0.0] * 24
        for s in toutes:
            try:
                ts = datetime.fromisoformat(s["timestamp"])
                if ts.date() == today:
                    valeurs[ts.hour] += s.get("duree_minutes", 0)
            except Exception:
                continue
        valeurs = [round(v, 1) for v in valeurs]
        pairs = [(f"{h:02d}h", v) for h, v in enumerate(valeurs) if v > 0]
        if not pairs:
            return [], []
        labels, vals = zip(*pairs)
        return list(labels), list(vals)

    def _get_weekly_data(self):
        toutes = self._load_sessions()
        today = date.today()
        debut_sem1 = today - timedelta(days=today.weekday())
        valeurs = [0.0, 0.0, 0.0, 0.0]
        for i in range(4):
            debut = debut_sem1 - timedelta(weeks=3 - i)
            fin = debut + timedelta(weeks=1)
            total = sum(
                s["duree_minutes"] for s in toutes
                if debut <= datetime.fromisoformat(s["timestamp"]).date() < fin
            )
            valeurs[i] = round(total, 1)
        labels = [t("stats.semaine_courte", n=3 - i + 1) for i in range(4)]
        return labels, valeurs

    @staticmethod
    def _load_sessions():
        from stats_manager import charger_sessions
        return charger_sessions()

    def _refresh_stats(self, data):
        self.lbl_stat_moyenne.configure(text=t("stats.moyenne", v=formater_duree(data['moyenne'])))
        self.lbl_stat_total.configure(text=t("stats.total_label", v=formater_duree(data['temps_total'])))
        nb = data.get("nb_jours", 0)
        self.lbl_stat_sessions.configure(text=t("stats.sessions_label", n=nb))
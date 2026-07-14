"""
stats_manager.py — Gestion des statistiques Hardcore Focus
Filtres temporels + formatage pour l'écran Statistiques.
"""

import json
import os
import sys
from datetime import datetime, timedelta, date

# Ancré sur le dossier de l'exe une fois empaqueté (PyInstaller --onefile),
# jamais sur son dossier d'extraction temporaire — sinon les stats seraient
# perdues à chaque relancement. Identique à DATA_DIR dans main.py.
_DATA_DIR = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
             else os.path.dirname(os.path.abspath(__file__)))
STATS_FILE = os.path.join(_DATA_DIR, "stats.json")


# ── Chargement / Sauvegarde ──

def charger_sessions():
    """
    Charge toutes les sessions depuis stats.json.
    Gère l'ancien format (dict date→minutes) et le nouveau (liste d'objets).
    Retourne une liste de dicts :
        [{"timestamp": "2026-06-14T15:30:00", "duree_minutes": 25.0, "app_name": None}]
    """
    if not os.path.exists(STATS_FILE):
        return []
    try:
        with open(STATS_FILE, "r") as f:
            data = json.load(f)
    except Exception:
        return []

    # Nouveau format : liste de sessions
    if isinstance(data, list):
        return data

    # Ancien format : dict {date_str: minutes_total} → conversion
    if isinstance(data, dict):
        sessions = []
        for date_str, minutes in data.items():
            if isinstance(minutes, (int, float)):
                sessions.append({
                    "timestamp": f"{date_str}T12:00:00",
                    "duree_minutes": minutes,
                    "app_name": None,
                })
        # Sauvegarde automatique vers nouveau format
        try:
            with open(STATS_FILE, "w") as f:
                json.dump(sessions, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
        return sessions

    return []


def sauvegarder_session(duree_minutes, app_name=None, objectif=None, hardcore=False,
                         abandon=False):
    """
    Ajoute une session à stats.json avec un timestamp complet.
    duree_minutes : float, durée en minutes
    app_name     : str ou None (None = session globale Focus)
    objectif     : str ou None, l'engagement pris au démarrage (écran Contrat)
    hardcore     : bool, session lancée en mode Hardcore
    abandon      : bool, session interrompue avant la fin (Tunnel de la Honte)
    """
    sessions = charger_sessions()
    now = datetime.now()
    sessions.append({
        "timestamp": now.isoformat(sep="T", timespec="seconds"),
        "duree_minutes": duree_minutes,
        "app_name": app_name,
        "objectif": objectif or "",
        "hardcore": bool(hardcore),
        "abandon": bool(abandon),
    })
    try:
        with open(STATS_FILE, "w") as f:
            json.dump(sessions, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


# ── Utilitaires ──

def total_minutes(sessions):
    """Somme des durées d'une liste de sessions."""
    return sum(s.get("duree_minutes", 0) for s in sessions)


def nombre_sessions(sessions):
    """Nombre de sessions dans la liste."""
    return len(sessions)


def get_app_names(sessions):
    """Retourne la liste des noms d'applications uniques."""
    noms = set()
    for s in sessions:
        app = s.get("app_name") or "Focus"
        noms.add(app)
    return sorted(noms)


def sessions_par_app(sessions, app_name):
    """Filtre les sessions pour une application donnée."""
    return [s for s in sessions
            if (s.get("app_name") or "Focus") == app_name]


def formater_duree(total_min):
    """Formate un total de minutes en 'Xh Ymin' ou '0 min'."""
    if total_min is None or total_min == 0:
        return "0 min"
    heures = int(total_min // 60)
    minutes = int(total_min % 60)
    parties = []
    if heures > 0:
        parties.append(f"{heures}h")
    if minutes > 0:
        parties.append(f"{minutes}min")
    return " ".join(parties) if parties else "0 min"


# ── Filtres temporels ──

def get_stats_jour(sessions):
    """Sessions d'aujourd'hui uniquement."""
    today = datetime.now().date()
    return [s for s in sessions
            if datetime.fromisoformat(s["timestamp"]).date() == today]


def get_stats_semaine(sessions):
    """Sessions de cette semaine (lundi → dimanche)."""
    today = datetime.now().date()
    debut_semaine = today - timedelta(days=today.weekday())
    return [s for s in sessions
            if datetime.fromisoformat(s["timestamp"]).date() >= debut_semaine]


def get_stats_mois(sessions):
    """Sessions de ce mois (1er → fin de mois)."""
    today = datetime.now().date()
    debut_mois = today.replace(day=1)
    return [s for s in sessions
            if datetime.fromisoformat(s["timestamp"]).date() >= debut_mois]


def get_stats_total(sessions):
    """Toutes les sessions, sans filtre temporel."""
    return sessions


# ── Mapping pour l'UI ──

FILTERS = {
    "jour":   get_stats_jour,
    "semaine": get_stats_semaine,
    "mois":   get_stats_mois,
    "total":  get_stats_total,
}

FILTER_LABELS = {
    "jour":   "📅  Aujourd'hui",
    "semaine": "📆  Cette semaine",
    "mois":   "📊  Ce mois",
    "total":  "🏆  Total",
}


# ── Classe StatsManager : API orientée objet ──

class StatsManager:
    """Gestion centralisée des données statistiques depuis stats.json."""

    def __init__(self, stats_file=STATS_FILE):
        self.stats_file = stats_file

    # ── API publique par période ──

    def get_data_jour(self):
        """Sessions d'aujourd'hui."""
        return self._filtrer("jour")

    def get_data_semaine(self):
        """Sessions de cette semaine."""
        return self._filtrer("semaine")

    def get_data_mois(self):
        """Sessions de ce mois."""
        return self._filtrer("mois")

    def get_data_total(self):
        """Toutes les sessions."""
        return self._filtrer("total")

    # ── Graphique : 7 derniers jours ──

    def get_7day_data(self):
        """Retourne (labels, valeurs) pour le graphique hebdomadaire."""
        sessions = charger_sessions()
        jours_noms = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
        today = date.today()
        labels, valeurs = [], []
        for i in range(6, -1, -1):
            j = today - timedelta(days=i)
            labels.append(jours_noms[j.weekday()])
            total = sum(
                s["duree_minutes"] for s in sessions
                if datetime.fromisoformat(s["timestamp"]).date() == j
            )
            valeurs.append(round(total, 1))
        return labels, valeurs

    # ── Winstreak : séquence de jours consécutifs ──

    def get_winstreak(self):
        """Retourne le nombre de jours consécutifs avec au moins une session."""
        sessions = charger_sessions()
        dates = set()
        for s in sessions:
            try:
                ts = datetime.fromisoformat(s["timestamp"]).date()
                dates.add(ts)
            except Exception:
                continue
        if not dates:
            return 0
        today = date.today()
        dernier_jour = max(dates)
        # Si le dernier enregistrement date de plus d'un jour → série cassée
        if (today - dernier_jour).days > 1 and today not in dates:
            return 0
        streak = 0
        jour = dernier_jour
        while jour in dates:
            streak += 1
            jour -= timedelta(days=1)
        return streak

    # ── Moteur interne ──

    def _filtrer(self, periode):
        """Applique un filtre temporel et retourne les stats agrégées."""
        sessions = charger_sessions()
        filtre_fn = FILTERS[periode]
        sessions_filtrees = filtre_fn(sessions)

        total_min = total_minutes(sessions_filtrees)
        jours_actifs = set(
            datetime.fromisoformat(s["timestamp"]).date()
            for s in sessions_filtrees
        )
        nb_jours = max(1, len(jours_actifs))
        moyenne = total_min / nb_jours

        noms_apps = get_app_names(sessions_filtrees)
        applis = []
        for app_name in noms_apps:
            app_sessions = sessions_par_app(sessions_filtrees, app_name)
            total_app = total_minutes(app_sessions)
            applis.append((app_name, total_app))
        applis.sort(key=lambda x: x[1], reverse=True)

        return {
            "temps_total": total_min,
            "moyenne": moyenne,
            "nb_jours": nb_jours,
            "applis": applis,
        }

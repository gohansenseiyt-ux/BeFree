"""app_paths.py — Détection du dossier de données persistantes.

Partagé entre main.py et stats_manager.py pour qu'il n'existe qu'une seule
version de cette formule (empaquetage PyInstaller --onefile : DATA_DIR doit
pointer vers le dossier de l'exe lui-même, jamais vers son dossier
d'extraction temporaire, qui change à chaque lancement et ferait perdre
toute donnée persistante)."""
import os
import sys


def frozen() -> bool:
    return getattr(sys, "frozen", False)


DATA_DIR = os.path.dirname(sys.executable) if frozen() else os.path.dirname(os.path.abspath(__file__))


def data_path(*parts) -> str:
    return os.path.join(DATA_DIR, *parts)

"""
BeFree Watchdog — gardien qui surveille l'app principale et la relance si elle est tuée.
Lancé EN DOUBLE (gardien A + gardien B) en Mode Hardcore (et Mode Libre). Chaque gardien
est un processus détaché indépendant : tuer l'app + un gardien laisse l'autre relancer tout.
Ne pas exécuter manuellement.

Usage interne : python watchdog.py <chemin_main> <main_pid> <id A|B>
"""
import sys
import os
import time
import subprocess
import json
import psutil
import winreg

import hc_integrity

_HC_REG_KEY   = r"Software\Microsoft\Windows\CurrentVersion\Run"
_HC_REG_VALUE = "*BeFreeHardcore"


def _cle_hardcore_existe() -> bool:
    """True si la clé de démarrage Hardcore est toujours enregistrée — preuve qu'une
    session Hardcore a été activée et n'a pas été terminée légitimement (hc_desactiver
    supprime cette clé)."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _HC_REG_KEY, 0, winreg.KEY_QUERY_VALUE)
        winreg.QueryValueEx(key, _HC_REG_VALUE)
        winreg.CloseKey(key)
        return True
    except OSError:
        return False


def est_vivant(pid: int) -> bool:
    try:
        p = psutil.Process(pid)
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def une_instance_tourne(chemin_main: str) -> bool:
    """True si une instance de l'app principale (hors gardiens) tourne déjà."""
    for proc in psutil.process_iter(['pid', 'cmdline']):
        try:
            cmd = ' '.join(proc.info.get('cmdline') or [])
            if chemin_main in cmd and 'watchdog.py' not in cmd \
                    and proc.info['pid'] != os.getpid():
                return True
        except Exception:
            pass
    return False


def relancer_main(chemin_main: str):
    """Relance l'app principale si une session active est trouvée.
    Verrou atomique partagé par tous les gardiens (A, B, tâche planifiée --check-once) :
    un SEUL appelant relance réellement, quel que soit celui qui détecte la mort en premier."""
    dir_main = os.path.dirname(chemin_main)
    lock_file = os.path.join(dir_main, "relaunch.lock")

    if une_instance_tourne(chemin_main):
        return  # déjà relancée par quelqu'un d'autre

    try:
        fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        return  # un autre gardien a déjà le verrou, il s'en occupe
    except Exception:
        pass  # verrou indisponible : on continue quand même (double relance rare > aucune relance)

    try:
        _relancer_main_impl(chemin_main, dir_main)
    finally:
        try:
            os.remove(lock_file)
        except Exception:
            pass


def _relancer_main_impl(chemin_main: str, dir_main: str):
    # Mode Hardcore
    hc_file = os.path.join(dir_main, "hardcore_state.json")
    etat = None
    if os.path.exists(hc_file):
        try:
            with open(hc_file) as f:
                etat = json.load(f)
        except Exception:
            etat = None

    if etat is not None and hc_integrity.verifier(etat):
        if etat.get("actif"):
            subprocess.Popen(
                [sys.executable, chemin_main, "--reprendre-hardcore"],
                cwd=dir_main,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        return  # signature valide (actif ou fin légitime) : rien de plus à faire ici

    if _cle_hardcore_existe():
        # Fichier absent, illisible ou signature invalide alors que la clé de démarrage
        # Hardcore existe encore → tentative de contournement (édition/suppression du
        # JSON). On refuse de laisser filer : on relance quand même, en signalant à
        # l'app que l'état est corrompu (elle appliquera une session par défaut + violation).
        subprocess.Popen(
            [sys.executable, chemin_main, "--reprendre-hardcore", "--etat-corrompu"],
            cwd=dir_main,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        return

    # Mode Libre
    session_file = os.path.join(dir_main, "session_en_cours.json")
    if os.path.exists(session_file):
        try:
            with open(session_file) as f:
                etat = json.load(f)
            if etat.get("mode") == "libre":
                subprocess.Popen(
                    [sys.executable, chemin_main],
                    cwd=dir_main,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                )
        except Exception:
            pass


def main():
    if "--check-once" in sys.argv:
        # Invoqué par la tâche planifiée Windows (schtasks) : une seule vérification
        # puis on quitte. Ne dépend d'aucun process python vivant au moment de l'appel,
        # donc survit à un kill simultané de main.py + des deux gardiens A/B.
        idx = sys.argv.index("--check-once")
        chemin_main = sys.argv[idx + 1]
        relancer_main(chemin_main)
        return

    if len(sys.argv) < 4:
        sys.exit(1)

    chemin_main = sys.argv[1]
    main_pid    = int(sys.argv[2])
    # gid       = sys.argv[3]   # "A" ou "B" — information seulement

    while True:
        time.sleep(1)
        if est_vivant(main_pid):
            continue
        time.sleep(0.5)              # courte attente pour éviter un faux positif
        if est_vivant(main_pid):
            continue

        # ── L'app principale est morte ──────────────────────────────────
        # relancer_main() gère elle-même le verrou atomique anti-doublon.
        relancer_main(chemin_main)

        # Que ce gardien ait relancé ou non, son rôle pour cette génération est fini :
        # la nouvelle instance de l'app relancera ses propres deux gardiens frais.
        break


if __name__ == "__main__":
    main()

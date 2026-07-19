import json
import os
import sys
import time
import ctypes
import ctypes.wintypes
from datetime import date, datetime, timedelta

import csv
import threading
import hashlib
import math
import secrets
import socket
import smtplib
import subprocess
import winreg
import io
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders as email_encoders

# psutil/hc_integrity seuls sont nécessaires au rôle --watchdog-role ci-dessous
# (simple surveillance de PID) — le reste de la pile GUI (customtkinter, PIL,
# pystray, matplotlib via ui_elements...) est importé plus bas, après la
# sortie anticipée de ce rôle, pour ne pas payer ce coût à chaque relance
# minute-par-minute du gardien pendant une session Hardcore.
import psutil
import hc_integrity


# =====================================================================
#   CHEMINS — sources vs empaqueté (PyInstaller --onefile)
# =====================================================================
# BASE_DIR (plus bas) reste ancré sur __file__ : correct pour les ressources
# LUES en lecture seule (polices, icônes, image) car PyInstaller --onefile
# réécrit __file__ pour pointer dans son dossier d'extraction temporaire
# (_MEIPASS), où ces ressources sont justement rassemblées via --add-data.
#
# DATA_DIR est différent : les fichiers ÉCRITS (stats, config, état Hardcore...)
# ne doivent PAS vivre dans ce dossier temporaire, qui change à chaque
# lancement — sinon toute donnée persistante serait perdue au redémarrage
# suivant. DATA_DIR pointe donc vers le dossier du .exe lui-même une fois
# empaqueté, ou vers le dossier du script en mode source (comportement
# inchangé pour les développeurs).
from app_paths import frozen as _frozen, DATA_DIR, data_path as _data_path


def _app_identity_path() -> str:
    """Chemin qui identifie « cette app » (ligne de commande, Registre) —
    l'exe lui-même une fois empaqueté, ce script sinon."""
    return sys.executable if _frozen() else os.path.abspath(__file__)


def _cmd_relancer(*extra_args) -> list:
    """Commande pour relancer cette app (empaquetée ou depuis les sources),
    avec des arguments additionnels (ex. --reprendre-hardcore, --watchdog-role)."""
    if _frozen():
        return [sys.executable, *extra_args]
    return [sys.executable, os.path.abspath(__file__), *extra_args]


# Clé de démarrage Windows utilisée par le Mode Hardcore — définie ici (avant
# le rôle watchdog ci-dessous) pour que les deux endroits qui la lisent/écrivent
# (ce rôle watchdog, et la section Mode Hardcore plus bas) partagent la même
# constante au lieu de deux copies qui pourraient diverger.
_HC_REG_KEY     = r"Software\Microsoft\Windows\CurrentVersion\Run"
_HC_REG_VALUE   = "*BeFreeHardcore"   # préfixe '*' = Windows l'exécute aussi en Mode sans échec


# ── Rôle watchdog : ce même exécutable, invoqué avec --watchdog-role, se
# comporte comme le gardien qui relance l'app si elle est tuée. Remplace
# l'ancien watchdog.py comme script séparé : sous PyInstaller --onefile,
# sys.executable est l'exe lui-même, donc « python watchdog.py » ne
# fonctionne plus une fois empaqueté — on relance ce même exécutable avec
# ce flag à la place. Doit s'exécuter et sortir AVANT toute init lourde
# (fenêtre, polices custom) : c'est un simple processus de surveillance.
if "--watchdog-role" in sys.argv:

    def _wd_cle_hardcore_existe() -> bool:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _HC_REG_KEY,
                                  0, winreg.KEY_QUERY_VALUE)
            winreg.QueryValueEx(key, _HC_REG_VALUE)
            winreg.CloseKey(key)
            return True
        except OSError:
            return False

    def _wd_est_vivant(pid: int) -> bool:
        try:
            p = psutil.Process(pid)
            return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    def _wd_une_instance_tourne() -> bool:
        """True si une instance de l'app principale (hors watchdog) tourne déjà."""
        identite = _app_identity_path()
        for proc in psutil.process_iter(['pid', 'cmdline']):
            try:
                cmd = ' '.join(proc.info.get('cmdline') or [])
                if identite in cmd and "--watchdog-role" not in cmd \
                        and proc.info['pid'] != os.getpid():
                    return True
            except Exception:
                pass
        return False

    def _wd_relancer_main(dir_app: str):
        """Relance l'app principale si une session active est trouvée.
        Verrou atomique partagé par tous les gardiens (A, B, tâche planifiée
        --check-once) : un SEUL appelant relance réellement."""
        lock_file = os.path.join(dir_app, "relaunch.lock")

        if _wd_une_instance_tourne():
            return  # déjà relancée par quelqu'un d'autre

        try:
            fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
        except FileExistsError:
            return  # un autre gardien a déjà le verrou, il s'en occupe
        except Exception:
            pass  # verrou indisponible : on continue quand même

        try:
            _wd_relancer_main_impl(dir_app)
        finally:
            try:
                os.remove(lock_file)
            except Exception:
                pass

    def _wd_relancer_main_impl(dir_app: str):
        hc_file = os.path.join(dir_app, "hardcore_state.json")
        etat = None
        if os.path.exists(hc_file):
            try:
                with open(hc_file, encoding="utf-8") as f:
                    etat = json.load(f)
            except Exception:
                etat = None

        if etat is not None and hc_integrity.verifier(etat):
            if etat.get("actif"):
                subprocess.Popen(
                    _cmd_relancer("--reprendre-hardcore"),
                    cwd=dir_app, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                )
            return  # signature valide (actif ou fin légitime) : rien de plus à faire

        if _wd_cle_hardcore_existe():
            # Fichier absent, illisible ou signature invalide alors que la clé de
            # démarrage Hardcore existe encore → tentative de contournement. On
            # relance quand même, en signalant à l'app que l'état est corrompu.
            subprocess.Popen(
                _cmd_relancer("--reprendre-hardcore", "--etat-corrompu"),
                cwd=dir_app, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            return

        session_file = os.path.join(dir_app, "session_en_cours.json")
        if os.path.exists(session_file):
            try:
                with open(session_file, encoding="utf-8") as f:
                    etat = json.load(f)
                if etat.get("mode") == "libre":
                    subprocess.Popen(
                        _cmd_relancer(),
                        cwd=dir_app, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                    )
            except Exception:
                pass

    def _watchdog_role_main():
        args = [a for a in sys.argv if a != "--watchdog-role"]

        if "--check-once" in args:
            idx = args.index("--check-once")
            _wd_relancer_main(args[idx + 1])
            return

        if len(args) < 3:
            return
        dir_app = args[1]
        main_pid = int(args[2])
        # args[3] = gid ("A"/"B"/"S") — information seulement

        while True:
            time.sleep(1)
            if _wd_est_vivant(main_pid):
                continue
            time.sleep(0.5)              # courte attente pour éviter un faux positif
            if _wd_est_vivant(main_pid):
                continue
            _wd_relancer_main(dir_app)
            break

    _watchdog_role_main()
    sys.exit(0)


# À partir d'ici : lancement réel de l'application (fenêtre, GUI) — le rôle
# watchdog ci-dessus est toujours sorti (sys.exit) avant d'atteindre ce point.
import founder_backend
import customtkinter as ctk
import pystray
from PIL import Image, ImageDraw
from plyer import notification as plyer_notification
from tkinter import filedialog, messagebox
import tkinter as tk
import win32com.client
import win32gui
import win32con

from stats_manager import (sauvegarder_session,
                           StatsManager, formater_duree)
from ui_elements import StatsDashboard
import theme_sumi
import i18n
from i18n import t

theme_sumi.register_fonts()


def _nom_utilisateur_local() -> str:
    """Nom d'affichage local (nom d'utilisateur Windows), capitalisé.
    Remplace l'ancien compte cloud — 100% local, aucune connexion requise."""
    import getpass
    try:
        nom = getpass.getuser().strip()
        return nom.capitalize() if nom else ""
    except Exception:
        return ""

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# ── Palette Hardcore Focus ──
COLOR_BG = "#0A0908"
COLOR_FRAME = "#141210"
COLOR_PRIMARY = "#E63946"
COLOR_PRIMARY_HOVER = "#A82230"
COLOR_SECONDARY = "#1F1B18"
COLOR_SECONDARY_HOVER = "#1F1B18"
COLOR_SUCCESS = "#7A9B5C"
COLOR_SUCCESS_HOVER = "#5C7A46"
COLOR_DANGER = "#E63946"
COLOR_DANGER_HOVER = "#A82230"
COLOR_TEXT = "#E8DFCE"
COLOR_TEXT_DIM = "#8A8071"
COLOR_TEXT_MUTED = "#8A8071"
COLOR_ACCENT = "#E63946"
COLOR_CRIMSON = "#A82230"

# ── Admin check ──
def _est_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False

def _relancer_en_admin():
    """Relance l'application avec les droits administrateur (UAC)."""
    try:
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable,
            " ".join(f'"{a}"' for a in sys.argv), None, 1
        )
    except Exception:
        pass
    sys.exit(0)

# ── Sidebar ──
COLOR_SIDEBAR = "#141210"
COLOR_SIDEBAR_ACTIVE = "#E63946"
COLOR_SIDEBAR_HOVER = "#1F1B18"
COLOR_SIDEBAR_TEXT = "#B8AF9E"
COLOR_SIDEBAR_SEPARATOR = "#2A2622"

FICHIER = "test.txt"
DOSSIER_QUARANTAINE = _data_path("Quarantaine")
ALWAYS_ALLOWED = {"Code.exe", "WindowsTerminal.exe", "cmd.exe", "powershell.exe",
                  "bash.exe", "WT.exe", "explorer.exe", "Explorer.EXE",
                  "Taskmgr.exe", "taskmgr.exe"}

# BeFree ne doit jamais se détecter/fermer lui-même. Empaqueté (--onefile), le
# process s'appelle BeFree.exe et Windows en lance 2 (bootloader + enfant) —
# exclure par PID seul ne couvrirait que l'un des deux, donc exclusion par nom
# une fois frozen. En dev, le process est python.exe/pythonw.exe (nom trop
# générique pour être exclu sans risque) → exclusion par PID uniquement,
# suffisant puisqu'il n'y a alors qu'un seul process.
_OWN_PID = os.getpid()
_OWN_PROCESS_NAME = "BeFree.exe" if _frozen() else None

# ── Deep Work Score — table des grades (rangs du dojo) ──
# Chaque entrée : (points_minimum, nom_affiché, kanji, couleur_hex)
# Fidèle à la refonte Claude Design : 6 rangs, kanji du sceau, palette Sumi.
GRADES = [
    (0,    "Novice",     "初", "#8A8071"),
    (60,   "Habitué",    "習", "#B8AF9E"),
    (240,  "Focalisé",   "禅", "#E63946"),
    (400,  "Discipliné", "修", "#D4A24C"),
    (800,  "Maître",     "師", "#D4A24C"),
    (1600, "BeFree",     "禅", "#E63946"),
]

# GRADES[i][1] reste la clé interne stable (comparaisons g[1] == nom) — la
# traduction n'intervient qu'à l'affichage, via cette table de correspondance.
_GRADE_KEY = {
    "Novice": "grade.novice", "Habitué": "grade.habitue",
    "Focalisé": "grade.focalise", "Discipliné": "grade.discipline",
    "Maître": "grade.maitre", "BeFree": "grade.befree",
}


def _grade_label(nom: str) -> str:
    return t(_GRADE_KEY.get(nom, nom))


# ── BLACKLIST : processus système Windows à IGNORER complètement ──
SYSTEM_BLACKLIST = {
    "system idle process", "system", "registry", "smss", "csrss", "wininit",
    "services", "lsass", "svchost", "winlogon", "fontdrvhost", "dwm",
    "conhost", "ctfmon", "spoolsv", "sihost", "taskhostw", "runtimebroker",
    "shellexperiencehost", "startmenuexperiencehost", "searchhost",
    "searchprotocolhost", "searchindexer", "widgetservice", "widgetboard",
    "filecoauth", "accountscontrolhost", "crossdeviceresume",
    "crossdeviceservice", "phoneexperiencehost", "useroobebroker",
    "gamebar", "gamebarftserver", "gamebarpresencewriter",
    "xboxgamebarwidgets", "xboxpcappft", "gamingservicesnet", "gamingservices",
    "xgamehelper", "msedge", "msedgewebview2", "microsoftedgeupdate",
    "bravecrashhandler64", "bravecrashhandler", "nissrv",
    "securityhealthservice", "smartscreen", "mpdefendercoreservice",
    "msmpeng", "wmiprvse", "wermgr",
    "atiesrxx", "atieclxx", "amdfendrsr", "amdrsserv", "amdrssrcext",
    "radeonsoftware", "rtkauduservice64",
    "audiodg", "memcompression", "gpuup",
    "gameinputsvc", "gameinputredistservice", "vgc", "vgtray",
    "python", "javaw", "openconsole", "snippingtool", "shellhost",
    "cncmd", "midisrv", "cpumetricsserver",
    "discordsystemhelper", "hermes", "wwahost", "startui",
    "applicationframehost", "systemsettings", "ntoskrnl", "securekernel",
    "lockapp", "backgroundtransferhost", "compattelrunner", "unistacksvc",
    "settingssync", "dashost", "shareduvcam", "dispbroker",
    "taskmgr", "msiexec", "dxdiag", "winver", "osk", "magnify",
    # ── Tâches de fond Windows sans fenêtre utilisateur ──
    "mousocoworker", "usocoreworker", "uso", "usoclient", "wuauclt",
    "textinputhost", "dllhost", "rundll32", "sppsvc", "trustedinstaller",
    "tiworker", "mobsync", "wsappx", "ngciso", "lsaiso", "ctmonitor",
    "audacityx", "officeclicktorun", "msoia", "msosync", "onedrivestandaloneupdater",
    "browser_broker", "browserbroker", "dllhost32", "consent", "wininit",
    "presentationfontcache", "perfwatson2", "servicehub", "vshub",
    "tabtip", "tabtip32", "inputapp", "lockappbroker", "aggregatorhost",
    "dptf", "esif_uf", "intelcphecisvc", "jhi_service", "igfxem",
    "armsvc", "adobeipcbroker", "node", "conhost", "cmd",
}

# ── Nettoyage des noms de processus ──
CLEAN_NAMES = {
    "discord":          "Discord",
    "discordcanary":    "Discord Canary",
    "discordptb":       "Discord PTB",
    "robloxstudio":     "Roblox Studio",
    "robloxplayerbeta": "Roblox",
    "code":             "VS Code",
    "code - insiders":  "VS Code Insiders",
    "winword":          "Word",
    "excel":            "Excel",
    "powerpnt":         "PowerPoint",
    "outlook":          "Outlook",
    "chrome":           "Chrome",
    "firefox":          "Firefox",
    "msedge":           "Edge",
    "brave":            "Brave",
    "opera":            "Opera",
    "spotify":          "Spotify",
    "slack":            "Slack",
    "teams":            "Teams",
    "zoom":             "Zoom",
    "obs64":            "OBS Studio",
    "obs32":            "OBS Studio",
    "steam":            "Steam",
    "epicgameslauncher":"Epic Games",
    "origin":           "Origin",
    "ubisoftconnect":   "Ubisoft Connect",
    "riotclient":       "Riot Client",
    "leagueclient":     "League of Legends",
    "leagueclientux":   "League of Legends",
    "valorant":         "Valorant",
    "minecraft":        "Minecraft",
    "eclipse":          "Eclipse",
    "intellij":         "IntelliJ",
    "pycharm":          "PyCharm",
    "androidstudio":    "Android Studio",
    "goland":           "GoLand",
    "webstorm":         "WebStorm",
    "clion":            "CLion",
    "datagrip":         "DataGrip",
    "rider":            "Rider",
    "notepad++":        "Notepad++",
    "sublime_text":     "Sublime Text",
    "atom":             "Atom",
    "vscodium":         "VSCodium",
    "windowsTerminal":  "Terminal",
    "wt":               "Terminal",
}

DOSSIERS_RACCOURCIS = [
    "C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs",
    os.path.expanduser("~\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs"),
]

STATS_FILE = _data_path("stats.json")
SESSION_FILE = _data_path("session_en_cours.json")
CONFIG_FILE = _data_path("config.json")
STARTUP_NAME = "HardcoreFocus"
STARTUP_DIR = os.path.expanduser(
    "~\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs\\Startup"
)

# --- VARIABLES GLOBALES ---
timer_active = False
temps_restant = 0
victory_printed = False
checkbox_vars = {}
cles_apps = {}
duree_heures = 0
duree_minutes = 0
ctk_icones_cache = {}
popup = None
mode_infini = False
chrono_secondes = 0
reactivation_popup_shown = False
_tunnel_honte_ouvert = False   # True pendant le Tunnel de la Honte → suspend vol de focus & scan d'apps
contrat_objectif = ""

# ── Adaptive Focus & Discipline ──
secondes_focus = 0
secondes_distraction = 0
session_start_time = None
soft_correction_active = False
soft_correction_countdown = 0
soft_correction_app = None
nb_soft_corrections = 0

# ── Tray icon ──
tray_icon = None
tray_thread = None

# ── Type de session ──
session_type = None          # "pomodoro", "normale", "infini", "quarantaine"
POMODORO_FOCUS_SECS = 25 * 60
POMODORO_BREAK_SECS = 5 * 60
pomodoro_phase = "focus"     # "focus" or "break"

# ── Nouveau flux de démarrage ──
session_cfg = {
    "mode": None,          # "libre" | "tunnel" | "hardcore"
    "type": None,          # "pomodoro" | "fixe" | "quarantaine"
    "duree_minutes": 90,
    "nb_cycles": 4,
    "nb_jours": 1,
    "objectif": "",
    "whitelist_apps": [],
    "blocked_sites": [],
    "hardcore": False,     # Mode Hardcore activé pour cette session
}
whitelist_from_recap = False   # True = recap sans modif → pas d'écran verrouillage intermédiaire
WHITELIST_FILE = _data_path("whitelist.json")
_wl_session_keys_cache: set = set()   # Calculé une seule fois au démarrage de session
HOSTS_FILE = r"C:\Windows\System32\drivers\etc\hosts"
HOSTS_MARKER_START = "# ==BeFree-block-start=="
HOSTS_MARKER_END   = "# ==BeFree-block-end=="

# ── Quarantaine ──
quarantaine_active = False
quarantaine_fin_ts = 0       # timestamp UNIX de fin

# ── Détection dynamique d'applications ──
DETECTED_APPS_FILE = _data_path("detected_apps.json")
detected_apps = []           # list of clean names ["Discord", "RobloxStudio", ...]

# ── Anti-inactivité : pause automatique ──
paused = False
INACTIVITY_LIMIT = 120  # secondes

class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.wintypes.UINT), ("dwTime", ctypes.wintypes.DWORD)]

_last_input_info = _LASTINPUTINFO()
_last_input_info.cbSize = ctypes.sizeof(_LASTINPUTINFO)

def get_idle_seconds():
    """Retourne les secondes écoulées depuis la dernière entrée clavier/souris."""
    try:
        ctypes.windll.user32.GetLastInputInfo(ctypes.byref(_last_input_info))
        millis = ctypes.windll.kernel32.GetTickCount() - _last_input_info.dwTime
        return millis / 1000.0
    except Exception:
        return 0.0

def is_youtube_active():
    """Vérifie si le titre de la fenêtre active contient 'YouTube'."""
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        buff = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
        return "youtube" in buff.value.lower()
    except Exception:
        return False

# =====================================================================
#              DÉTECTION DYNAMIQUE D'APPLICATIONS
# =====================================================================

# Répertoire système Windows — tout exe qui s'y trouve est un processus système.
_WINDIR = (os.environ.get("SystemRoot") or r"C:\Windows").lower().rstrip("\\")


def _pids_fenetres_visibles():
    """Retourne l'ensemble des PID possédant une fenêtre top-level visible AVEC un titre.
    C'est le signal le plus fiable qu'un processus est une vraie application utilisateur
    (et non une tâche de fond comme MoUsoCoreWorker, svchost, etc.)."""
    pids = set()
    try:
        user32 = ctypes.windll.user32
        WNDENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

        def _cb(hwnd, _lparam):
            try:
                if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
                    pid = ctypes.wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    if pid.value:
                        pids.add(pid.value)
            except Exception:
                pass
            return True

        user32.EnumWindows(WNDENUMPROC(_cb), 0)
    except Exception:
        pass
    return pids


def _est_processus_systeme(exe_path):
    """True si l'exe est situé dans le répertoire Windows (processus système)."""
    if not exe_path:
        return False
    try:
        return exe_path.lower().startswith(_WINDIR)
    except Exception:
        return False


def load_detected_apps():
    global detected_apps
    try:
        if os.path.exists(DETECTED_APPS_FILE):
            with open(DETECTED_APPS_FILE, "r") as f:
                raw = json.load(f)
            # Purge : ne garder que les apps utilisateur légitimes
            detected_apps = purge_detected_apps(raw)
            save_detected_apps()
        else:
            detected_apps = []
    except Exception:
        detected_apps = []

def purge_detected_apps(apps_list):
    """
    Purge les entrées indésirables de detected_apps.
    Garde uniquement les noms qui ne sont pas dans SYSTEM_BLACKLIST
    et qui ressemblent à des applications utilisateur.
    """
    cleaned = []
    for name in apps_list:
        name_lower = name.lower().strip()
        exe = os.path.splitext(name_lower)[0]
        if exe in SYSTEM_BLACKLIST:
            continue
        # Comparaison sans espaces : "Mo Uso Core Worker" → "mousocoreworker"
        despaced = exe.replace(" ", "")
        if despaced in SYSTEM_BLACKLIST:
            continue
        if len(exe) < 3 or exe.isdigit():
            continue
        # Si le nom apparaît dans CLEAN_NAMES (comme valeur), garder
        clean_values = {v.lower() for v in CLEAN_NAMES.values()}
        if name_lower in clean_values:
            cleaned.append(name)
            continue
        # Sinon, garder seulement les noms qui ressemblent à des apps connues
        # (pas des noms génériques windows)
        generic_windows = {
            "explorer", "windows explorer", "windowsexplorer",
            "applicationframehost", "systemsettings",
            "lockapp", "backgroundtransferhost",
        }
        if exe in generic_windows:
            continue
        cleaned.append(name)
    return cleaned

def save_detected_apps():
    try:
        with open(DETECTED_APPS_FILE, "w") as f:
            json.dump(detected_apps, f, indent=2)
    except Exception:
        pass

def auto_detect_app(nom_proc, exe_path=None, pid=None, pids_visibles=None):
    """
    Détection intelligente des applications utilisateur.
    Une app n'est retenue que si elle présente des SIGNAUX POSITIFS d'usage réel :
      - elle n'est PAS un processus système (blacklist + chemin hors C:\\Windows)
      - elle possède une fenêtre top-level visible avec un titre
    Cela évite de polluer la liste avec des tâches de fond (MoUsoCoreWorker, svchost…).
    """
    global detected_apps
    if nom_proc in ALWAYS_ALLOWED:
        return

    proc_lower = nom_proc.lower().strip()
    exe = os.path.splitext(proc_lower)[0]

    # ── Filtre BLACKLIST : ignorer les processus système connus ──
    if exe in SYSTEM_BLACKLIST:
        return

    # ── Filtre CHEMIN : tout exe dans C:\Windows est un processus système ──
    if _est_processus_systeme(exe_path):
        return

    # ── Filtre FENÊTRE : sans fenêtre visible, c'est une tâche de fond ──
    # (signal le plus fiable — élimine MoUsoCoreWorker et compagnie)
    if pids_visibles is not None:
        if pid is None or pid not in pids_visibles:
            return

    # ── Nettoyage intelligent du nom ──
    if exe in CLEAN_NAMES:
        clean_name = CLEAN_NAMES[exe]
    else:
        # Capitalisation propre : "discord" → "Discord", "robloxstudio" → "Roblox Studio"
        clean_name = exe.replace("_", " ").replace("-", " ").strip()
        # Découpage camelCase implicite et title()
        import re as _re
        clean_name = _re.sub(r'([a-z])([A-Z])', r'\1 \2', clean_name)
        clean_name = clean_name.title().strip()
        if not clean_name or len(clean_name) < 2:
            return

    # ── Vérifications de légitimité supplémentaires ──
    # Un processus utilisateur légitime a généralement plus de 2 caractères
    # et n'est pas un GUID/nombre
    if len(exe) < 3 or exe.isdigit():
        return

    # Déjà connu ?
    if clean_name in checkbox_vars:
        return
    if clean_name in detected_apps:
        return

    # Nouvelle application détectée !
    detected_apps.append(clean_name)
    save_detected_apps()
    # Créer une entrée checkbox pour la prochaine session
    var = ctk.BooleanVar(value=False)
    checkbox_vars[clean_name] = var
    cles_apps[clean_name] = generer_cles_recherche(clean_name)
    # Notification dans le statut
    try:
        label_statut.configure(
            text=t("detect.nouvelle_app", app=clean_name),
            text_color="orange")
        root.after(3000, lambda: label_statut.configure(text=""))
    except Exception:
        pass

# =====================================================================
#                        FONCTIONS (définies avant l'UI)
# =====================================================================

# --- ICÔNES ---
def extraire_icone_pil(chemin_exe, taille=32):
    """Extrait l'icône d'un exe via win32gui + ctypes (sans win32ui)."""
    try:
        import win32gui
        import win32con
    except ImportError:
        return None

    try:
        grands, petits = win32gui.ExtractIconEx(chemin_exe, 0)
        hicon = grands[0] if grands else (petits[0] if petits else None)
        if hicon is None:
            return None

        # Obtenir le contexte du device de l'écran
        hdc_ecran = win32gui.GetDC(0)

        # Créer un DC compatible via ctypes (remplace win32ui.CreateDCFromHandle)
        hdc = ctypes.windll.gdi32.CreateCompatibleDC(hdc_ecran)

        # Créer un bitmap compatible
        hbmp = ctypes.windll.gdi32.CreateCompatibleBitmap(hdc_ecran, taille, taille)

        # Sélectionner le bitmap dans le DC
        ctypes.windll.gdi32.SelectObject(hdc, hbmp)

        # Dessiner l'icône
        win32gui.DrawIconEx(hdc, 0, 0, hicon, taille, taille, 0,
                            None, win32con.DI_NORMAL)

        # Récupérer les bits du bitmap
        bmp_info = _get_bitmap_info(hbmp)
        buf_size = bmp_info["bmWidth"] * bmp_info["bmHeight"] * 4
        buf = ctypes.create_string_buffer(buf_size)
        ctypes.windll.gdi32.GetBitmapBits(hbmp, buf_size, buf)

        # Convertir en image PIL
        img = Image.frombuffer("RGBA",
                               (bmp_info["bmWidth"], bmp_info["bmHeight"]),
                               buf, "raw", "BGRA", 0, 1)

        # Nettoyage
        win32gui.DestroyIcon(hicon)
        ctypes.windll.gdi32.DeleteObject(hbmp)
        ctypes.windll.gdi32.DeleteDC(hdc)
        win32gui.ReleaseDC(0, hdc_ecran)

        return img
    except Exception:
        return None


def _get_bitmap_info(hbmp):
    """Récupère les infos d'un bitmap HBITMAP via ctypes."""
    class BITMAP(ctypes.Structure):
        _fields_ = [
            ("bmType", ctypes.c_long),
            ("bmWidth", ctypes.c_long),
            ("bmHeight", ctypes.c_long),
            ("bmWidthBytes", ctypes.c_long),
            ("bmPlanes", ctypes.c_ushort),
            ("bmBitsPixel", ctypes.c_ushort),
            ("bmBits", ctypes.c_void_p),
        ]
    bmp = BITMAP()
    ctypes.windll.gdi32.GetObjectW(hbmp, ctypes.sizeof(bmp), ctypes.byref(bmp))
    return {
        "bmWidth": bmp.bmWidth,
        "bmHeight": bmp.bmHeight,
    }

def charger_icone_app(lnk_path, taille=32):
    try:
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortcut(lnk_path)
        cible = shortcut.TargetPath
        if not cible or not os.path.isfile(cible) or cible.lower().endswith(".url"):
            return None
        pil_img = extraire_icone_pil(cible, taille)
        return ctk.CTkImage(pil_img, size=(taille, taille)) if pil_img else None
    except Exception:
        return None

# --- SCANNER ---
def scanner_applications():
    apps, vues = [], set()
    FILTRES = {"Administrative Tools", "File Explorer", "Magnify",
               "Narrator", "On-Screen Keyboard", "VoiceAccess", "LiveCaptions"}
    for dossier in DOSSIERS_RACCOURCIS:
        if not os.path.isdir(dossier):
            continue
        for racine, dossiers, fichiers in os.walk(dossier):
            if os.path.basename(racine) in ("Accessories", "Administrative Tools",
                "Startup", "System Tools", "Windows Accessories", "Windows System",
                "Windows PowerShell", "Windows Kits"):
                dossiers.clear()
                continue
            for f in fichiers:
                if not f.lower().endswith(".lnk"):
                    continue
                nom = os.path.splitext(f)[0]
                if nom.endswith(" - Shortcut"):
                    nom = nom[:-11]
                nom = nom.strip()
                if not nom or nom in FILTRES or nom in vues:
                    continue
                vues.add(nom)
                apps.append({"nom": nom, "lnk_path": os.path.join(racine, f)})
    apps.sort(key=lambda x: x["nom"].lower())
    return apps

def generer_cles_recherche(nom_app):
    base = nom_app.lower().replace(" ", "").replace("-", "").replace("_", "")
    mots = nom_app.lower().split()
    return {base} | set(mots)

# --- RECHERCHE D'APPLICATIONS ---
def filtrer_applications(event=None):
    texte = entry_recherche.get().lower()
    for enfant in scroll_apps.winfo_children():
        if isinstance(enfant, ctk.CTkFrame):
            nom_label = None
            for sub in enfant.winfo_children():
                if isinstance(sub, ctk.CTkLabel):
                    txt = sub.cget("text")
                    if txt and txt not in ("", "  ", " "):
                        nom_label = txt
                        break
            if nom_label:
                if texte in nom_label.lower():
                    enfant.pack(fill="x", padx=15, pady=2)
                else:
                    enfant.pack_forget()

# --- STATISTIQUES (lightweight, pour l'accueil) ---
def sauvegarder_stats(minutes, abandon=False):
    """Ajoute une session au nouveau format stats.json avec timestamp complet."""
    sauvegarder_session(minutes, app_name=None,
                         objectif=session_cfg.get("objectif") or contrat_objectif,
                         hardcore=bool(session_cfg.get("hardcore")),
                         abandon=abandon)

def mettre_a_jour_stats_accueil():
    # Supprimé : la barre winstreak a été retirée de l'UI
    pass

# --- SESSION TYPE SELECTION ---
def on_session_type(stype):
    """Handler pour les 3 boutons de type de session sur l'accueil."""
    global session_type, mode_infini, duree_heures, duree_minutes, pomodoro_phase
    session_type = stype

    if stype == "pomodoro":
        mode_infini = False
        duree_heures = 0
        duree_minutes = 25
        pomodoro_phase = "focus"
        # Skip ecran_temps, go directly to app selection
        # Changement du sous-titre pour indiquer le cycle
        sous_titre_apps.configure(
            text="Coche les applications à bloquer · Cycle 25 min focus / 5 min pause"
        )
        montrer_ecran(ecran_apps)

    elif stype == "normale":
        mode_infini = False
        montrer_ecran(ecran_temps)
        # Restaurer le sous-titre normal de ecran_apps
        sous_titre_apps.configure(
            text="Coche les applications à autoriser pendant la session"
        )

    elif stype == "infini":
        mode_infini = True
        duree_heures = 0
        duree_minutes = 0
        sous_titre_apps.configure(
            text="Coche les applications à bloquer · Session sans limite de temps"
        )
        montrer_ecran(ecran_apps)

# --- MODE INFINI (ancien switch, conservé pour compatibilité) ---
def basculer_mode_infini():
    global mode_infini
    mode_infini = not mode_infini
    if mode_infini:
        frame_entrees.pack_forget()
        btn_suivant_temps.pack_forget()
        btn_suivant_infini.pack()
        switch_infini.configure(text="♾️ Mode Infini (actif)")
        sous_titre_temps.configure(
            text="Session sans limite de temps — bosse jusqu'à ce que tu décides d'arrêter."
        )
    else:
        frame_entrees.pack()
        btn_suivant_infini.pack_forget()
        btn_suivant_temps.pack()
        switch_infini.configure(text="♾️ Mode Infini")
        sous_titre_temps.configure(text="Combien de temps veux-tu travailler ?", text_color=COLOR_TEXT_DIM)

# --- NAVIGATION ---
def montrer_ecran(ecran):
    for e in (ecran_accueil, ecran_stats, ecran_parametres, ecran_temps, ecran_apps,
              ecran_session, ecran_contrat, ecran_type_mode, ecran_type_session,
              ecran_whitelist_nouveau, ecran_whitelist_sites, ecran_verrouillage):
        try:
            e.pack_forget()
        except Exception:
            pass
    ecran.pack(fill="both", expand=True)
    if ecran == ecran_apps:
        entry_recherche.delete(0, "end")
        filtrer_applications()
    elif ecran == ecran_stats:
        stats_dashboard.update_dashboard("total")

# ── Sidebar navigation ──
_sidebar_btn_actif = None

def activer_bouton_sidebar(nom):
    """Met en surbrillance l'item de nav actif : fond surface + liseré cinabre
    a gauche (barre separee positionnee sur le bouton actif)."""
    global _sidebar_btn_actif
    _sidebar_btn_actif = nom
    for name, btn in _sidebar_boutons.items():
        icons = _NAV_ICONS.get(name)
        if name == nom:
            btn.configure(fg_color="#1F1B18", text_color="#E8DFCE",
                          image=icons["active"] if icons else None)
        else:
            btn.configure(fg_color="transparent", text_color="#B8AF9E",
                          image=icons["rest"] if icons else None)
    active_btn = _sidebar_boutons.get(nom)
    if active_btn is not None:
        active_btn.update_idletasks()
        _sidebar_nav_accent.configure(height=active_btn.winfo_height())
        _sidebar_nav_accent.place(x=0, y=active_btn.winfo_y())
        _sidebar_nav_accent.lift()

def _flash_session_active():
    """Feedback visuel quand on tente de naviguer pendant une session."""
    label_statut.configure(text=t("misc.session_active_flash"), text_color="#E63946")
    root.after(2000, lambda: label_statut.configure(
        text="", text_color=COLOR_TEXT_DIM) if timer_active else None)


def naviguer_sidebar(page):
    # Bloquer la navigation pendant une session active
    if timer_active and page in ("accueil", "stats", "parametres", "apps",
                                  "demarrer", "sites"):
        _flash_session_active()
        # Remettre le highlight sur "session" pour que le bouton actif reste correct
        activer_bouton_sidebar("session")
        return
    activer_bouton_sidebar(page)
    if page == "accueil":
        rafraichir_accueil()
        montrer_ecran(ecran_accueil)
    elif page == "demarrer":
        _ts_reset_mode()
        montrer_ecran(ecran_type_mode)
    elif page == "stats":
        montrer_ecran(ecran_stats)
    elif page == "parametres":
        _rafraichir_code_fondateur_param()
        # Physical Lock vérifié en premier
        if physical_lock_actif() and not verifier_cle_usb():
            _afficher_erreur_cle_usb()
            return
        if mot_de_passe_actif():
            ouvrir_dialog_mdp(
                lambda: montrer_ecran(ecran_parametres),
                titre=t("mdp.parametres_titre"),
                message=t("mdp.parametres_message"),
            )
        else:
            montrer_ecran(ecran_parametres)
    elif page == "apps":
        montrer_ecran(ecran_apps)
    elif page == "sites":
        _wl_sites_construire()
        montrer_ecran(ecran_whitelist_sites)


def _centrer_popup(win, w, h):
    """Centre un Toplevel sur l'écran."""
    win.update_idletasks()
    sw = win.winfo_screenwidth()
    sh = win.winfo_screenheight()
    x = (sw - w) // 2
    y = (sh - h) // 2
    win.geometry(f"{w}x{h}+{x}+{y}")


def valider_temps():
    global duree_heures, duree_minutes
    try:
        h = int(entry_heures.get() or "0")
        m = int(entry_minutes.get() or "0")
        if h < 0 or m < 0 or (h == 0 and m == 0):
            raise ValueError
        duree_heures, duree_minutes = h, m
        if mode_infini:
            # Depuis l'écran temps, mode infini skipé → retour
            montrer_ecran(ecran_apps)
            return
        montrer_ecran(ecran_apps)
    except ValueError:
        sous_titre_temps.configure(text="Entre des valeurs valides (> 0 min).", text_color="orange")
        root.after(3000, lambda: sous_titre_temps.configure(
            text="Combien de temps veux-tu travailler ?", text_color=COLOR_TEXT_DIM))

# --- POP-UP DE CONFIRMATION HARDCORE (écran 12 du design) ---
def ouvrir_confirmation():
    """Popup de confirmation avant d'entrer en session Hardcore (Quarantaine) —
    intercalée entre le choix du régime et le Contrat de travail. Ombre portée
    dure 6px 6px 0 #E63946 (même pattern .place() que le feuillet Contrat)."""
    global popup
    if popup is not None and popup.winfo_exists():
        return
    popup = ctk.CTkToplevel(root)
    popup.title(t("popup_hc.titre_fenetre"))
    popup.resizable(False, False)
    popup.transient(root)
    popup.grab_set()
    popup.configure(fg_color=theme_sumi.SUMI)

    CARD_W = 520
    holder = ctk.CTkFrame(popup, fg_color="transparent")

    # Ombre portée dure (décalée +6,+6, sans flou — box-shadow:6px 6px 0 #E63946)
    ombre = ctk.CTkFrame(holder, fg_color=theme_sumi.HANKO, corner_radius=0, width=CARD_W)
    ombre.place(x=6, y=6)

    carte = ctk.CTkFrame(holder, fg_color=theme_sumi.SUMI_2, corner_radius=0,
                          width=CARD_W, border_width=2, border_color=theme_sumi.HANKO)
    carte.place(x=0, y=0)

    # Badge coin (chevauche la bordure, top:-1 right:-1)
    ctk.CTkLabel(carte, text=t("popup_hc.badge"), font=theme_sumi.mono(10),
                 fg_color=theme_sumi.HANKO, text_color=theme_sumi.SUMI,
                 corner_radius=0, padx=12, pady=6
                 ).place(relx=1.0, x=-1, y=-1, anchor="ne")

    contenu = ctk.CTkFrame(carte, fg_color="transparent")
    contenu.pack(fill="x", padx=40, pady=40)

    # Sceau
    seal = ctk.CTkFrame(contenu, width=88, height=88, corner_radius=44,
                         fg_color=theme_sumi.HANKO, border_width=2,
                         border_color=theme_sumi.SUMI_2)
    seal.pack()
    seal.pack_propagate(False)
    ctk.CTkLabel(seal, text="禅", font=theme_sumi.serif(44),
                 text_color=theme_sumi.SUMI).place(relx=0.5, rely=0.5, anchor="center")

    ctk.CTkLabel(contenu, text=t("popup_hc.titre"),
                 font=theme_sumi.serif(32), text_color=theme_sumi.INK,
                 justify="center").pack(pady=(20, 0))

    ctk.CTkLabel(
        contenu,
        text=t("popup_hc.phrase1"),
        font=theme_sumi.ui(12), text_color=theme_sumi.INK_2,
        wraplength=440, justify="center").pack(pady=(12, 0))
    ctk.CTkLabel(
        contenu, text=t("popup_hc.phrase1b"),
        font=theme_sumi.ui(12, "bold"), text_color=theme_sumi.HANKO,
        justify="center").pack()
    _conf_type = session_cfg.get("type")
    if _conf_type == "quarantaine":
        _conf_phrase2 = t("popup_hc.phrase2_quarantaine")
    else:
        _conf_phrase2 = t("popup_hc.phrase2_defaut")
    ctk.CTkLabel(
        contenu, text=_conf_phrase2,
        font=theme_sumi.ui(12), text_color=theme_sumi.INK_2,
        wraplength=440, justify="center").pack(pady=(2, 0))

    # Bloc infos : durée verrouillée + récompense (dynamiques, session_cfg/_TS_TYPES)
    bloc = ctk.CTkFrame(contenu, fg_color=theme_sumi.SURFACE, corner_radius=0,
                         border_width=1, border_color=theme_sumi.RULE)
    bloc.pack(fill="x", pady=(20, 0))
    bloc_inner = ctk.CTkFrame(bloc, fg_color="transparent")
    bloc_inner.pack(fill="x", padx=16, pady=14)

    nb_jours = session_cfg.get("nb_jours", 1)
    if _conf_type == "quarantaine":
        _conf_duree_txt = f"{nb_jours}j 00:00:00"
    elif _conf_type == "pomodoro":
        _conf_total_min = session_cfg.get("nb_cycles", 4) * (POMODORO_FOCUS_SECS + POMODORO_BREAK_SECS) // 60
        _conf_h, _conf_m = divmod(_conf_total_min, 60)
        _conf_duree_txt = f"{_conf_h:02d}:{_conf_m:02d}:00"
    else:  # fixe
        _conf_h, _conf_m = divmod(session_cfg.get("duree_minutes", 90), 60)
        _conf_duree_txt = f"{_conf_h:02d}:{_conf_m:02d}:00"
    ligne_duree = ctk.CTkFrame(bloc_inner, fg_color="transparent")
    ligne_duree.pack(fill="x")
    ctk.CTkLabel(ligne_duree, text=t("popup_hc.duree_verrouillee"), font=theme_sumi.mono(10),
                 text_color=theme_sumi.MUTED, anchor="w").pack(side="left")
    ctk.CTkLabel(ligne_duree, text=_conf_duree_txt, font=theme_sumi.mono(20),
                 text_color=theme_sumi.INK, anchor="e").pack(side="right")

    pts_txt = _TS_TYPES.get(_conf_type, _TS_TYPES["quarantaine"])[4]
    ligne_recomp = ctk.CTkFrame(bloc_inner, fg_color="transparent")
    ligne_recomp.pack(fill="x", pady=(8, 0))
    ctk.CTkLabel(ligne_recomp, text=t("popup_hc.recompense"), font=theme_sumi.mono(10),
                 text_color=theme_sumi.MUTED, anchor="w").pack(side="left")
    ctk.CTkLabel(ligne_recomp, text=pts_txt, font=theme_sumi.mono(14),
                 text_color=theme_sumi.GOLD, anchor="e").pack(side="right")

    # Checkbox de confirmation — gate le bouton "Entrer en Hardcore"
    var_comprends = ctk.BooleanVar(value=False)

    def _toggle_check():
        btn_entrer.configure(state="normal" if var_comprends.get() else "disabled")

    ctk.CTkCheckBox(
        contenu, text=t("popup_hc.checkbox"),
        variable=var_comprends, onvalue=True, offvalue=False,
        font=theme_sumi.ui(12), text_color=theme_sumi.INK_2,
        checkbox_width=16, checkbox_height=16, corner_radius=0,
        border_width=1, border_color=theme_sumi.INK,
        fg_color=theme_sumi.INK, checkmark_color=theme_sumi.SUMI,
        hover_color=theme_sumi.INK, command=_toggle_check
    ).pack(anchor="w", pady=(12, 0))

    frame_btn = ctk.CTkFrame(contenu, fg_color="transparent")
    frame_btn.pack(fill="x", pady=(20, 0))

    def _annuler():
        popup.destroy()

    def _confirmer_hardcore():
        popup.destroy()
        slide_vers(ecran_contrat, ecran_type_session)

    ctk.CTkButton(frame_btn, text=t("common.annuler"), height=44, width=180,
                  font=theme_sumi.ui(13), corner_radius=0,
                  fg_color="transparent", hover_color=theme_sumi.SURFACE,
                  border_width=1, border_color=theme_sumi.INK, text_color=theme_sumi.INK,
                  command=_annuler).pack(side="left")

    btn_entrer = ctk.CTkButton(
        frame_btn, text=t("common.entrer_hardcore"), height=44, width=250,
        font=theme_sumi.ui(13, "bold"), corner_radius=0,
        fg_color=theme_sumi.HANKO, hover_color=theme_sumi.HANKO_DEEP,
        text_color=theme_sumi.SUMI, state="disabled",
        command=_confirmer_hardcore)
    btn_entrer.pack(side="left", padx=(10, 0))

    # La hauteur de la carte dépend du texte réellement rendu (retours à la
    # ligne) → mesurée après construction plutôt que codée en dur, pour ne
    # jamais tronquer le contenu.
    popup.update_idletasks()
    h = carte.winfo_reqheight()
    ombre.configure(height=h)
    holder.configure(width=CARD_W + 6, height=h + 6)
    holder.place(relx=0.5, rely=0.5, anchor="center")
    _centrer_popup(popup, CARD_W + 66, h + 66)

# --- FERMETURE FORCÉE ---
def fermer_application(process_obj, nom_process):
    try:
        process_obj.terminate()
        label_statut.configure(
            text=t("misc.app_interdite_fermee"),
            text_color="orange"
        )
        root.after(3000, lambda: label_statut.configure(text=""))
    except Exception:
        pass

# =====================================================================
#   PERSISTANCE SESSION — Watchdog léger pour toutes les sessions
# =====================================================================

_SW_PROC      = None   # subprocess watchdog
_SW_REG_KEY   = r"Software\Microsoft\Windows\CurrentVersion\Run"
_SW_REG_VALUE = "*BeFreeSession"   # préfixe '*' = Windows l'exécute aussi en Mode sans échec


def _session_watchdog_activer():
    """Lance le watchdog léger et la clé registre dès qu'une session démarre.
    Ignoré en session Hardcore : _hc_activer_effectif() installe son propre
    watchdog lourd (registre + tâche planifiée + 2 gardiens détachés) et les
    deux systèmes ne doivent jamais tourner simultanément pour la même session."""
    if session_cfg.get("hardcore"):
        return
    global _SW_PROC
    # Clé registre → relance au redémarrage du PC
    try:
        cmd = " ".join(f'"{a}"' for a in _cmd_relancer())
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _SW_REG_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, _SW_REG_VALUE, 0, winreg.REG_SZ, cmd)
        winreg.CloseKey(key)
    except Exception:
        pass

    # Watchdog subprocess (survit à la mort du parent grâce à DETACHED_PROCESS)
    try:
        _SW_PROC = subprocess.Popen(
            _cmd_relancer("--watchdog-role", DATA_DIR, str(os.getpid()), "S"),
            cwd=DATA_DIR,
            creationflags=0x00000008 | subprocess.CREATE_NEW_PROCESS_GROUP,  # DETACHED_PROCESS
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def _session_watchdog_desactiver():
    """Supprime la clé registre et arrête le watchdog à la fin normale de la session."""
    global _SW_PROC
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _SW_REG_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, _SW_REG_VALUE)
        winreg.CloseKey(key)
    except Exception:
        pass
    try:
        if _SW_PROC and _SW_PROC.poll() is None:
            _SW_PROC.terminate()
        _SW_PROC = None
    except Exception:
        pass


# =====================================================================
#   MODE HARDCORE — Friction psychologique et technique
# =====================================================================

_HC_ACTIF            = False
_HC_VIOLATIONS       = 0
_HC_WATCHDOG_PROCS   = []      # deux gardiens détachés [Popen, Popen]
_HC_MINI_TIMER_WIN   = None
_HC_REPRISE          = False   # True si la session a été reprise après un kill/redémarrage

_HC_STATE_FILE  = _data_path("hardcore_state.json")
_HC_LOCK_FILE   = _data_path("relaunch.lock")
# _HC_REG_KEY / _HC_REG_VALUE sont définies plus haut, avant le rôle --watchdog-role.


# ── Persistance ──────────────────────────────────────────────────────

def _hc_sauvegarder():
    etat = {
        "actif": True,
        "temps_restant_sec": temps_restant,
        "objectif":          session_cfg.get("objectif", ""),
        "whitelist_apps":    session_cfg.get("whitelist_apps", []),
        "blocked_sites":     session_cfg.get("blocked_sites", []),
        "type_session":      session_cfg.get("type", "libre"),
        "duree_minutes":     session_cfg.get("duree_minutes", 90),
        "violations":        _HC_VIOLATIONS,
    }
    etat = hc_integrity.signer(etat)
    try:
        with open(_HC_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(etat, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _hc_charger():
    """Retourne l'état signé, ou None si absent OU si la signature ne correspond pas
    (fichier édité à la main pour tenter de contourner le Mode Hardcore)."""
    try:
        if os.path.exists(_HC_STATE_FILE):
            with open(_HC_STATE_FILE, encoding="utf-8") as f:
                etat = json.load(f)
            if hc_integrity.verifier(etat):
                return etat
    except Exception:
        pass
    return None


def _hc_effacer():
    """Fin légitime du Mode Hardcore (victoire/abandon autorisé).
    On n'efface PAS le fichier : on écrit un tombstone signé actif=False. Un fichier
    manquant alors que la clé de démarrage existe encore devient ainsi un signal de
    falsification (voir le rôle --watchdog-role plus haut) plutôt qu'une fin de
    session normale indétectable."""
    try:
        tombstone = hc_integrity.signer({"actif": False, "ts_fin": time.time()})
        with open(_HC_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(tombstone, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _HC_REG_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, _HC_REG_VALUE)
        winreg.CloseKey(key)
    except Exception:
        pass


def _hc_enregistrer_redemarrage():
    try:
        cmd = " ".join(f'"{a}"' for a in _cmd_relancer("--reprendre-hardcore"))
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _HC_REG_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, _HC_REG_VALUE, 0, winreg.REG_SZ, cmd)
        winreg.CloseKey(key)
    except Exception:
        pass


def _hc_tick_sauvegarde():
    if _HC_ACTIF and timer_active:
        _hc_sauvegarder()
        root.after(10_000, _hc_tick_sauvegarde)


# ── Watchdog (double gardien mutuel) ─────────────────────────────────

def _hc_spawn_gardien(gid: str):
    """Lance un gardien détaché (A ou B) qui surveille l'app principale."""
    return subprocess.Popen(
        _cmd_relancer("--watchdog-role", DATA_DIR, str(os.getpid()), gid),
        cwd=DATA_DIR,
        creationflags=0x00000008 | subprocess.CREATE_NEW_PROCESS_GROUP,  # DETACHED_PROCESS
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _hc_lancer_watchdog():
    """Lance les DEUX gardiens. Tuer l'app + un gardien laisse l'autre relancer tout."""
    global _HC_WATCHDOG_PROCS
    # Nettoyer un éventuel verrou de relance périmé laissé par une génération précédente
    try:
        if os.path.exists(_HC_LOCK_FILE):
            os.remove(_HC_LOCK_FILE)
    except Exception:
        pass
    _HC_WATCHDOG_PROCS = []
    for gid in ("A", "B"):
        try:
            _HC_WATCHDOG_PROCS.append(_hc_spawn_gardien(gid))
        except Exception:
            _HC_WATCHDOG_PROCS.append(None)


_HC_TASK_NAME = "BeFreeHardcoreWatch"


def _hc_task_creer():
    """Crée une tâche planifiée Windows (indépendante de tout process python en cours)
    qui vérifie/relance BeFree toutes les minutes. Contrairement aux deux gardiens
    (qui sont eux-mêmes des process python.exe), elle survit à un kill simultané de
    TOUS les process python.exe — Task Scheduler la redéclenche à la minute suivante."""
    try:
        if _frozen():
            tr = f'"{sys.executable}" --watchdog-role --check-once "{DATA_DIR}"'
        else:
            pythonw = sys.executable.replace("python.exe", "pythonw.exe")
            if not os.path.exists(pythonw):
                pythonw = sys.executable
            tr = (f'"{pythonw}" "{os.path.abspath(__file__)}" '
                  f'--watchdog-role --check-once "{DATA_DIR}"')
        subprocess.run(
            ["schtasks", "/create", "/f", "/sc", "MINUTE", "/mo", "1",
             "/tn", _HC_TASK_NAME, "/tr", tr],
            creationflags=subprocess.CREATE_NO_WINDOW,
            capture_output=True,
        )
    except Exception:
        pass


def _hc_task_supprimer():
    try:
        subprocess.run(
            ["schtasks", "/delete", "/f", "/tn", _HC_TASK_NAME],
            creationflags=subprocess.CREATE_NO_WINDOW,
            capture_output=True,
        )
    except Exception:
        pass


def _hc_surveiller_watchdog():
    """Main surveille ses deux gardiens et relance celui qui meurt (poll 1 s).
    Filet de sécurité en plus de la relance de main par les gardiens."""
    def _boucle():
        while _HC_ACTIF:
            try:
                for i, gid in enumerate(("A", "B")):
                    p = _HC_WATCHDOG_PROCS[i] if i < len(_HC_WATCHDOG_PROCS) else None
                    if p is None or p.poll() is not None:
                        try:
                            _HC_WATCHDOG_PROCS[i] = _hc_spawn_gardien(gid)
                        except Exception:
                            pass
            except Exception:
                pass
            time.sleep(1)
    threading.Thread(target=_boucle, daemon=True).start()


# ── Mini timer always-on-top ──────────────────────────────────────────

def _hc_mini_timer_ouvrir():
    global _HC_MINI_TIMER_WIN
    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.attributes("-alpha", 0.88)
    win.configure(bg="#0A0908")
    win.geometry("170x46+20+20")
    win.resizable(False, False)
    win.protocol("WM_DELETE_WINDOW", lambda: None)

    lbl = tk.Label(win, text="⏱  --:--", font=("JetBrains Mono", 14, "bold"),
                   bg="#0A0908", fg="#B8AF9E")
    lbl.pack(expand=True)

    # Déplaçable à la souris
    def _debut_drag(e):
        win._dx, win._dy = e.x, e.y
    def _drag(e):
        win.geometry(f"+{win.winfo_x()+e.x-win._dx}+{win.winfo_y()+e.y-win._dy}")
    win.bind("<ButtonPress-1>", _debut_drag)
    win.bind("<B1-Motion>", _drag)

    def _maj():
        if not _HC_ACTIF:
            return
        s = max(0, temps_restant)
        h, r = divmod(s, 3600)
        m, sec = divmod(r, 60)
        txt = f"⏱  {h}:{m:02d}:{sec:02d}" if h else f"⏱  {m:02d}:{sec:02d}"
        couleur = "#EA5561" if _HC_VIOLATIONS > 0 else "#B8AF9E"
        suffix = f"  ✗{_HC_VIOLATIONS}" if _HC_VIOLATIONS > 0 else ""
        lbl.configure(text=txt + suffix, fg=couleur)
        win.lift()
        win.after(1000, _maj)

    _maj()
    _HC_MINI_TIMER_WIN = win


def _hc_mini_timer_fermer():
    global _HC_MINI_TIMER_WIN
    if _HC_MINI_TIMER_WIN:
        try:
            _HC_MINI_TIMER_WIN.destroy()
        except Exception:
            pass
    _HC_MINI_TIMER_WIN = None


# ── Activation / Désactivation ────────────────────────────────────────

_GWL_EXSTYLE       = -20
_WS_EX_APPWINDOW   = 0x00040000
_WS_EX_TOOLWINDOW  = 0x00000080
_SWP_FLAGS         = 0x0001 | 0x0002 | 0x0004 | 0x0020  # NOMOVE|NOSIZE|NOZORDER|FRAMECHANGED


def _hc_cacher_barre_taches():
    """Cache BeFree de la barre des tâches Windows → supprime le menu clic-droit taskbar."""
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        style = ctypes.windll.user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        style = (style & ~_WS_EX_APPWINDOW) | _WS_EX_TOOLWINDOW
        ctypes.windll.user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style)
        ctypes.windll.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, _SWP_FLAGS)
    except Exception:
        pass


def _hc_montrer_barre_taches():
    """Remet BeFree dans la barre des tâches à la fin du Mode Hardcore."""
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        style = ctypes.windll.user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        style = (style | _WS_EX_APPWINDOW) & ~_WS_EX_TOOLWINDOW
        ctypes.windll.user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style)
        ctypes.windll.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, _SWP_FLAGS)
    except Exception:
        pass


def _hc_activer_effectif():
    """Active réellement le Mode Hardcore (appelé après vérification premium)."""
    global _HC_ACTIF, _HC_REPRISE
    _HC_ACTIF = True
    _HC_REPRISE = False          # drapeau de reprise consommé (one-shot)
    _hc_enregistrer_redemarrage()
    _hc_sauvegarder()
    _hc_lancer_watchdog()
    _hc_surveiller_watchdog()
    _hc_task_creer()
    _hc_mini_timer_ouvrir()
    _session_verrouiller_fenetre()   # (ré)active le vol de focus maintenant que HC est actif
    _hc_cacher_barre_taches()        # supprime le bouton taskbar → pas de menu clic-droit
    root.after(10_000, _hc_tick_sauvegarde)

def hc_activer():
    """Active le Mode Hardcore pour la session en cours."""
    _hc_activer_effectif()


def hc_desactiver():
    """Désactive le Mode Hardcore (fin ou abandon de session)."""
    global _HC_ACTIF, _HC_WATCHDOG_PROCS
    _HC_ACTIF = False           # stoppe la boucle de surveillance des gardiens
    _hc_effacer()
    _hc_task_supprimer()
    _hc_mini_timer_fermer()
    _hc_montrer_barre_taches()       # remet l'app dans la barre des tâches
    root.unbind("<Alt-F4>")
    # Terminer les deux gardiens (les gardiens ne relancent main que si hardcore_state.json
    # est "actif" → _hc_effacer() l'a déjà supprimé, donc pas de relance parasite)
    for p in _HC_WATCHDOG_PROCS:
        try:
            if p:
                p.terminate()
        except Exception:
            pass
    _HC_WATCHDOG_PROCS = []
    try:
        if os.path.exists(_HC_LOCK_FILE):
            os.remove(_HC_LOCK_FILE)
    except Exception:
        pass


def hc_reprendre_apres_redemarrage():
    """Reprend une session hardcore interrompue par un redémarrage ou un kill.

    Cas normal : hardcore_state.json signé et 'actif' → on restaure l'état exact.
    Cas falsification (--etat-corrompu, posé par le rôle --watchdog-role quand le
    fichier a été trafiqué/supprimé alors que la clé de démarrage Hardcore existait encore) : on ne
    fait PAS confiance au fichier, mais on refuse quand même de laisser filer — on
    relance une session Hardcore par défaut et on compte une violation."""
    global temps_restant, _HC_VIOLATIONS, _HC_REPRISE
    etat = _hc_charger()

    if not etat or not etat.get("actif"):
        if "--etat-corrompu" in sys.argv:
            _HC_REPRISE = True
            session_cfg["objectif"]       = ""
            session_cfg["whitelist_apps"] = []
            session_cfg["blocked_sites"]  = []
            session_cfg["type"]           = "libre"
            session_cfg["hardcore"]       = True
            session_cfg["mode"]           = "hardcore"
            _HC_VIOLATIONS                = 1  # tentative de contournement détectée
            temps_restant                 = 25 * 60
            session_cfg["duree_minutes"]  = 25
            return True
        return False

    _HC_REPRISE = True   # → réactivation directe sans re-vérifier le premium (offline-safe)

    # Restaurer session_cfg
    session_cfg["objectif"]       = etat.get("objectif", "")
    session_cfg["whitelist_apps"] = etat.get("whitelist_apps", [])
    session_cfg["blocked_sites"]  = etat.get("blocked_sites", [])
    session_cfg["type"]           = etat.get("type_session", "libre")
    session_cfg["duree_minutes"]  = etat.get("duree_minutes", 90)
    session_cfg["hardcore"]       = True
    session_cfg["mode"]           = "hardcore"
    _HC_VIOLATIONS                = etat.get("violations", 0)
    secs_restants                 = etat.get("temps_restant_sec", 0)
    temps_restant                 = secs_restants
    # Synchroniser duree_minutes sur le temps restant pour que demarrer() ne le réinitialise pas
    session_cfg["duree_minutes"]  = max(1, secs_restants // 60)

    return True


# ──────────────────────────────────────────────────────────────────────
# --- VICTOIRE (mode limité) ---
def victoire():
    global timer_active, victory_printed, paused
    timer_active = False
    paused = False
    _session_deverrouiller_fenetre()
    if victory_printed:
        return
    victory_printed = True
    label_chrono.configure(text=t("misc.valide"), text_color=COLOR_SUCCESS)
    label_statut.configure(text=t("misc.session_terminee_jeu"), text_color=COLOR_SUCCESS)

    duree_total = duree_heures * 60 + duree_minutes
    sauvegarder_stats(duree_total)
    supprimer_etat()
    if _HC_ACTIF:
        hc_desactiver()
    notifier_fin_session()
    duree_secs = duree_heures * 3600 + duree_minutes * 60
    root.after(800, lambda: afficher_rapport_discipline(duree_secs))

# --- ABANDON ---
def abandonner_session():
    """Abandon effectif de la session (appelé après confirmation du Tunnel de la Honte)."""
    global timer_active, paused
    timer_active = False
    paused = False
    if _HC_ACTIF:
        hc_desactiver()
    _session_deverrouiller_fenetre()
    btn_terminer_infini.pack_forget()
    btn_abandonner.pack()
    # Enregistre la session malgré l'abandon (statut ABANDON dans "Dernières sessions")
    duree_ecoulee_min = max(1, int(secondes_focus // 60))
    sauvegarder_stats(duree_ecoulee_min, abandon=True)
    supprimer_etat()
    rafraichir_accueil()
    montrer_ecran(ecran_accueil)


# ── Sécurité barre des tâches ──
def ensure_taskbar_visible():
    """Force l'affichage de la barre des tâches Windows (si masquée)."""
    try:
        ctypes.windll.user32.ShowWindow(
            ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None), 1)
    except Exception:
        pass


# =====================================================================
#          SOFT-CORRECTION · TUNNEL · RAPPORT DE DISCIPLINE
# =====================================================================

def _surveiller_processus():
    """Scanne les processus. Gère la Soft-Correction (10 s avant fermeture forcée).
    Retourne True si une app bloquée est actuellement active."""
    global soft_correction_active, soft_correction_countdown, soft_correction_app
    global secondes_distraction, nb_soft_corrections

    # Pendant le Tunnel de la Honte : surveillance suspendue (l'utilisateur ne travaille pas,
    # et le focus bascule entre l'overlay et la session → évite les fausses sanctions).
    if _tunnel_honte_ouvert:
        return False

    # Utilise le cache pré-calculé au démarrage de session (évite O(whitelist) chaque seconde)
    _wl_keys = _wl_session_keys_cache
    _nouveau_flow = bool(_wl_keys)

    def _est_autorise(base):
        """Retourne True si le processus (base sans .exe) est dans la whitelist."""
        # Match exact ou le nom whitelisté est un préfixe du nom de processus
        # (ex: "obs" whitelisté → autorise "obs64", "spotify" → "spotifywebhelper")
        return base in _wl_keys or any(
            base.startswith(k) or k.startswith(base)
            for k in _wl_keys if len(k) >= 3
        )

    # PID possédant une fenêtre visible — calculé une seule fois par scan
    pids_visibles = _pids_fenetres_visibles()

    app_bloquee = None
    proc_bloque = None
    for proc in psutil.process_iter(["name", "pid", "exe"]):
        try:
            nom_proc = proc.info["name"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if proc.info.get("pid") == _OWN_PID or nom_proc == _OWN_PROCESS_NAME:
            continue
        if nom_proc in ALWAYS_ALLOWED:
            continue
        auto_detect_app(nom_proc, proc.info.get("exe"), proc.info.get("pid"), pids_visibles)
        base = os.path.splitext(nom_proc)[0].lower()
        # Autorisé par la whitelist du nouveau flow → skip
        if _nouveau_flow and _est_autorise(base):
            continue
        if _nouveau_flow:
            # Nouveau flow : bloquer uniquement si c'est une app connue non-autorisée
            for app_nom, cles in cles_apps.items():
                if base in cles:
                    app_bloquee = app_nom
                    proc_bloque = proc
                    break
        else:
            # Ancien flow : respecter les cases cochées de l'écran whitelist
            for app_nom, cles in cles_apps.items():
                if base in cles:
                    if not checkbox_vars[app_nom].get():
                        app_bloquee = app_nom
                        proc_bloque = proc
                        break
        if app_bloquee:
            break

    if app_bloquee:
        if not soft_correction_active:
            soft_correction_active = True
            soft_correction_countdown = 10
            soft_correction_app = app_bloquee
            nb_soft_corrections += 1
        else:
            soft_correction_countdown -= 1
            if soft_correction_countdown <= 0:
                try:
                    proc_bloque.terminate()
                except Exception:
                    pass
                secondes_distraction += 1
                soft_correction_active = False
                soft_correction_countdown = 0
                soft_correction_app = None
                label_statut.configure(
                    text=t("misc.sanction", app=app_bloquee),
                    text_color="#E63946")
                root.after(2500, lambda: label_statut.configure(
                    text="", text_color=COLOR_TEXT_DIM))
            else:
                label_statut.configure(
                    text=t("misc.soft_correction_warn", app=app_bloquee, n=soft_correction_countdown),
                    text_color="orange")
        if session_cfg.get("hardcore") and soft_correction_active:
            _afficher_violation_hardcore(app_bloquee, soft_correction_countdown)
        else:
            _fermer_violation_hardcore()
        return True
    else:
        if soft_correction_active:
            soft_correction_active = False
            soft_correction_countdown = 0
            soft_correction_app = None
            label_statut.configure(
                text=t("misc.bonne_decision"), text_color="#7A9B5C")
            root.after(2000, lambda: label_statut.configure(
                text="", text_color=COLOR_TEXT_DIM))
        _fermer_violation_hardcore()
        return False


def ouvrir_tunnel_honte():
    """Tunnel de la Honte — confirmation d'abandon en 3 étapes, plein écran
    et impossible à ignorer (réponse obligatoire)."""
    global _tunnel_honte_ouvert
    if _tunnel_honte_ouvert:
        return                       # déjà ouvert → éviter les doublons (spam Alt-F4 / clic)
    if not timer_active:
        abandonner_session()
        return

    # Physical Lock : clé USB requise pour abandonner
    if physical_lock_actif() and not verifier_cle_usb():
        _afficher_erreur_cle_usb()
        return

    _tunnel_honte_ouvert = True      # suspend le vol de focus et le scan d'apps

    dlg = ctk.CTkToplevel(root)
    dlg.title(t("tunnel.titre_fenetre"))
    dlg.overrideredirect(True)
    dlg.attributes("-topmost", True)
    dlg.configure(fg_color="#0A0908")
    sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
    dlg.geometry(f"{sw}x{sh}+0+0")
    dlg.protocol("WM_DELETE_WINDOW", lambda: None)
    for seq in ("<Alt-F4>", "<Escape>", "<Control-w>"):
        dlg.bind(seq, lambda e: "break")
    try:
        dlg.grab_set()
    except Exception:
        pass

    # Conteneur centré à l'écran
    centre = ctk.CTkFrame(dlg, fg_color="transparent")
    centre.place(relx=0.5, rely=0.5, anchor="center")

    ctk.CTkLabel(centre, text=t("tunnel.titre_grand"),
                 font=("JetBrains Mono", 30, "bold"), text_color="#E63946").pack(pady=(0, 34))

    zone = ctk.CTkFrame(centre, fg_color="transparent")
    zone.pack()

    # ── Actions communes ──────────────────────────────────────────────
    def _fermer_retour():
        global _tunnel_honte_ouvert
        _tunnel_honte_ouvert = False
        try:
            dlg.grab_release()
        except Exception:
            pass
        dlg.destroy()
        # Reprendre le focus sur la session (Hardcore) maintenant que le tunnel est fermé
        if _HC_ACTIF or session_cfg.get("mode") == "hardcore":
            try:
                root.lift()
                root.focus_force()
            except Exception:
                pass

    def _abandon_final():
        global _HC_VIOLATIONS, _tunnel_honte_ouvert
        _HC_VIOLATIONS += 1
        _tunnel_honte_ouvert = False
        try:
            dlg.grab_release()
        except Exception:
            pass
        dlg.destroy()
        abandonner_session()

    # ── Étape 3 : dernier avertissement ───────────────────────────────
    frame3 = ctk.CTkFrame(zone, fg_color="transparent")
    ctk.CTkLabel(frame3,
                 text=t("tunnel.etape3_texte"),
                 font=("Segoe UI", 18), text_color="#B8AF9E", justify="center").pack(pady=(0, 30))
    nav3 = ctk.CTkFrame(frame3, fg_color="transparent")
    nav3.pack()
    ctk.CTkButton(nav3, text=t("tunnel.je_reviens"), width=240, height=54,
                  font=("Segoe UI", 16, "bold"), corner_radius=3,
                  fg_color="#7A9B5C", hover_color="#5C7A46",
                  command=_fermer_retour).pack(side="left", padx=12)
    ctk.CTkButton(nav3, text=t("tunnel.jabandonne"), width=170, height=54,
                  font=("Segoe UI", 13), corner_radius=3,
                  fg_color="#241012", hover_color="#A82230", text_color="#E63946",
                  border_width=1, border_color="#5C3A38",
                  command=_abandon_final).pack(side="left", padx=12)

    # ── Étape 2 : saisie de friction ──────────────────────────────────
    frame2 = ctk.CTkFrame(zone, fg_color="transparent")
    ctk.CTkLabel(frame2, text=t("tunnel.etape2_consigne"),
                 font=("Segoe UI", 16), text_color="#8A8071").pack(pady=(0, 8))
    ctk.CTkLabel(frame2, text=t("tunnel.phrase_friction_affichee"),
                 font=("JetBrains Mono", 22, "bold"), text_color="#E63946").pack(pady=(0, 18))
    entry2 = ctk.CTkEntry(frame2, width=360, height=46, justify="center",
                          font=("JetBrains Mono", 16), fg_color="#160808",
                          border_color="#5C3A38", text_color="#E8DFCE")
    entry2.pack(pady=(0, 18))
    nav2 = ctk.CTkFrame(frame2, fg_color="transparent")
    nav2.pack()
    btn_cont = ctk.CTkButton(nav2, text=t("tunnel.continuer"), width=200, height=50,
                             font=("Segoe UI", 14, "bold"), corner_radius=3,
                             fg_color="#5C3A38", hover_color="#5C3A38",
                             text_color="#8A8071", state="disabled",
                             command=lambda: _montrer(frame3))
    btn_cont.pack(side="left", padx=12)
    ctk.CTkButton(nav2, text=t("tunnel.retour_travail_court"), width=240, height=50,
                  font=("Segoe UI", 14, "bold"), corner_radius=3,
                  fg_color="#7A9B5C", hover_color="#5C7A46",
                  command=_fermer_retour).pack(side="left", padx=12)

    def _maj_saisie(e=None):
        ok = entry2.get().strip().lower() == t("tunnel.phrase_friction")
        btn_cont.configure(state="normal" if ok else "disabled",
                           text_color="#EA5561" if ok else "#8A8071")
    entry2.bind("<KeyRelease>", _maj_saisie)

    # ── Étape 1 : confirmation + délai 60 s ───────────────────────────
    frame1 = ctk.CTkFrame(zone, fg_color="transparent")
    ctk.CTkLabel(frame1, text=t("tunnel.etape1_titre"),
                 font=("Segoe UI", 22, "bold"), text_color="#E8DFCE").pack(pady=(0, 10))
    ctk.CTkLabel(frame1,
                 text=t("tunnel.etape1_texte"),
                 font=("Segoe UI", 16), text_color="#8A8071", justify="center").pack(pady=(0, 26))
    nav1 = ctk.CTkFrame(frame1, fg_color="transparent")
    nav1.pack()
    ctk.CTkButton(nav1, text=t("tunnel.non_retour"), width=290, height=56,
                  font=("Segoe UI", 16, "bold"), corner_radius=3,
                  fg_color="#7A9B5C", hover_color="#5C7A46",
                  command=_fermer_retour).pack(side="left", padx=12)
    btn_oui = ctk.CTkButton(nav1, text=t("tunnel.oui_abandonner_compte", n=60), width=220, height=56,
                            font=("Segoe UI", 13), corner_radius=3,
                            fg_color="#241012", hover_color="#A82230",
                            text_color="#5C3A38", state="disabled",
                            command=lambda: _montrer(frame2))
    btn_oui.pack(side="left", padx=12)

    _secs = [60]
    def _tick_oui():
        if not dlg.winfo_exists():
            return
        _secs[0] -= 1
        if _secs[0] > 0:
            btn_oui.configure(text=t("tunnel.oui_abandonner_compte", n=_secs[0]))
            dlg.after(1000, _tick_oui)
        else:
            btn_oui.configure(text=t("tunnel.oui_abandonner"), state="normal", text_color="#E63946")

    # ── Navigation entre étapes ───────────────────────────────────────
    _frames = (frame1, frame2, frame3)
    def _montrer(f):
        for fr in _frames:
            fr.pack_forget()
        f.pack()
        if f is frame2:
            entry2.delete(0, "end")
            _maj_saisie()
            entry2.after(100, entry2.focus)

    _montrer(frame1)
    dlg.after(1000, _tick_oui)


def generer_certificat(duree_secs, ratio_pct, nb_corrections,
                       pts_gagnes, score_total, grade_nom, grade_col,
                       verdict, objectif):
    """Génère le Certificat de Discipline en PNG via PIL et propose la sauvegarde."""
    from PIL import Image, ImageDraw, ImageFont

    W, H = 1200, 700
    BG      = (8,   8,   8)
    CRIMSON = (163,  0,   0)
    DIM     = (40,  40,  40)
    MUTED   = (25,  25,  25)
    WHITE   = (210, 210, 210)
    GOLD    = (180, 140,  0)
    GREEN   = (0,  160,  0)
    RED_ERR = (180,  30, 30)

    # Couleur du grade (hex → RGB)
    def hex2rgb(h):
        h = h.lstrip("#")
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

    grade_rgb = hex2rgb(grade_col)

    # Verdict couleur
    if verdict == t("rapport.verdict_totale"):
        verdict_rgb = GREEN
    elif verdict == t("rapport.verdict_insuffisant"):
        verdict_rgb = (180, 120, 0)
    else:
        verdict_rgb = RED_ERR

    # ── Fonts ──
    FONT_DIR = r"C:\Windows\Fonts"
    def fnt(fichier, size):
        try:
            return ImageFont.truetype(os.path.join(FONT_DIR, fichier), size)
        except Exception:
            return ImageFont.load_default()

    f_title   = fnt("consolab.ttf", 38)
    f_sub     = fnt("consolab.ttf", 16)
    f_label   = fnt("consola.ttf",  13)
    f_value   = fnt("consolab.ttf", 22)
    f_grade   = fnt("consolab.ttf", 28)
    f_small   = fnt("consola.ttf",  11)
    f_obj     = fnt("consola.ttf",  15)
    f_verdict = fnt("consolab.ttf", 20)

    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    def ligne_pointillee(y, couleur=DIM, dash=6, gap=5):
        x = 60
        while x < W - 60:
            draw.line([(x, y), (min(x + dash, W - 60), y)], fill=couleur)
            x += dash + gap

    def texte_centre(y, texte, font, couleur):
        bb = draw.textbbox((0, 0), texte, font=font)
        tw = bb[2] - bb[0]
        draw.text(((W - tw) // 2, y), texte, fill=couleur, font=font)

    # ── Fond légèrement texturé (grain) ──
    import random
    rng = random.Random(7)
    for _ in range(6000):
        px = rng.randint(0, W - 1)
        py = rng.randint(0, H - 1)
        v  = rng.randint(10, 18)
        img.putpixel((px, py), (v, v, v))

    # ── Coins L-shape (accents cramoisis) ──
    sz = 28
    for x1, y1, dx, dy in [(30, 30, sz, 0), (30, 30, 0, sz),
                             (W-30-sz, 30, sz, 0), (W-30, 30, 0, sz),
                             (30, H-30, sz, 0), (30, H-30-sz, 0, sz),
                             (W-30-sz, H-30, sz, 0), (W-30, H-30-sz, 0, sz)]:
        draw.line([(x1, y1), (x1+dx, y1+dy)], fill=CRIMSON, width=2)

    # ── Bande déco gauche ──
    draw.rectangle([30, 30, 33, H-30], fill=(18, 18, 18))

    # ── En-tête ──
    texte_centre(52, "B  E  F  R  E  E", f_title, (55, 55, 55))
    texte_centre(100, t("certificat.titre"), f_sub, CRIMSON)

    ligne_pointillee(148, MUTED)

    # ── Verdict ──
    texte_centre(160, verdict, f_verdict, verdict_rgb)

    ligne_pointillee(202, MUTED)

    # ── Objectif ──
    draw.text((70, 216), t("certificat.objectif_label"), font=f_label, fill=DIM)
    obj_txt = (objectif[:80] + "…") if len(objectif) > 80 else objectif
    obj_affiche = f'"{obj_txt}"' if obj_txt.strip() else t("certificat.objectif_vide")
    texte_centre(240, obj_affiche, f_obj, (130, 130, 130))

    ligne_pointillee(282, MUTED)

    # ── Stats en 3 colonnes ──
    def fmt_dur(secs):
        m, s = divmod(int(secs), 60)
        h, m = divmod(m, 60)
        return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"

    cols = [
        (t("certificat.focus_actif"),  fmt_dur(duree_secs * ratio_pct / 100), WHITE),
        (t("certificat.compliance"),   f"{ratio_pct}%",                        verdict_rgb),
        (t("certificat.grade"),        grade_nom.upper(),                       grade_rgb),
    ]
    col_xs = [180, 600, 980]
    for (lbl, val, col), cx in zip(cols, col_xs):
        bb = draw.textbbox((0, 0), lbl, font=f_label)
        lw = bb[2] - bb[0]
        draw.text((cx - lw // 2, 302), lbl, font=f_label, fill=DIM)
        bb2 = draw.textbbox((0, 0), val, font=f_grade)
        vw = bb2[2] - bb2[0]
        draw.text((cx - vw // 2, 328), val, font=f_grade, fill=col)

    # ── Séparateur intermédiaire ──
    ligne_pointillee(390, MUTED)

    # ── Points gagnés + total ──
    pts_txt   = t("certificat.pts_gagnes", n=pts_gagnes)
    total_txt = t("certificat.pts_total", n=score_total)
    draw.text((70, 408),   pts_txt,   font=f_value, fill=GOLD)
    bb = draw.textbbox((0, 0), total_txt, font=f_value)
    draw.text((W - 70 - (bb[2]-bb[0]), 408), total_txt, font=f_value, fill=(70, 70, 70))

    # Petite info corrections
    corr_txt = t("certificat.corrections", n=nb_corrections)
    draw.text((70, 446), corr_txt, font=f_small, fill=(35, 35, 35))

    ligne_pointillee(482, MUTED)

    # ── Date ──
    now = datetime.now()
    date_txt = f"{now.day} {i18n.month_names()[now.month-1]} {now.year}"
    texte_centre(502, date_txt, f_label, (45, 45, 45))

    # ── Baseline ──
    texte_centre(560, t("certificat.baseline"), f_small, (22, 22, 22))

    # ── Filigrane diagonal (très discret) ──
    try:
        from PIL import Image as PILImg
        watermark = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        wd = ImageDraw.Draw(watermark)
        f_wm = fnt("consolab.ttf", 90)
        bb = wd.textbbox((0, 0), "BEFREE", font=f_wm)
        tw, th = bb[2]-bb[0], bb[3]-bb[1]
        wm_img = Image.new("RGBA", (tw+20, th+20), (0, 0, 0, 0))
        wmd = ImageDraw.Draw(wm_img)
        wmd.text((10, 10), "BEFREE", font=f_wm, fill=(22, 22, 22, 255))
        wm_rot = wm_img.rotate(25, expand=True)
        img_rgba = img.convert("RGBA")
        xp = (W - wm_rot.width) // 2
        yp = (H - wm_rot.height) // 2
        img_rgba.paste(wm_rot, (xp, yp), wm_rot)
        img = img_rgba.convert("RGB")
    except Exception:
        pass

    # ── Sauvegarde ──
    chemin = filedialog.asksaveasfilename(
        defaultextension=".png",
        filetypes=[(t("certificat.fichier_png"), "*.png"), (t("stats.fichier_tous"), "*.*")],
        initialfile=f"certificat_befree_{now.strftime('%Y%m%d_%H%M')}.png",
        title=t("certificat.dialog_titre"),
    )
    if chemin:
        img.save(chemin, "PNG")
        return chemin
    return None


def afficher_rapport_discipline(duree_session_secs):
    """Popup rapport de discipline + Deep Work Score affiché à la fin d'une session."""
    total_active = secondes_focus + secondes_distraction
    if total_active < 30:
        return

    # ── Adaptive Focus ──
    seuil = 0.80 if duree_session_secs < 3600 else 0.60
    seuil_pct = int(seuil * 100)
    ratio = secondes_focus / total_active if total_active > 0 else 0
    ratio_pct = int(ratio * 100)

    if ratio >= seuil:
        verdict, couleur_v = t("rapport.verdict_totale"), "#7A9B5C"
    elif ratio >= seuil * 0.75:
        verdict, couleur_v = t("rapport.verdict_insuffisant"), "#D4A24C"
    else:
        verdict, couleur_v = t("rapport.verdict_defaillant"), "#E63946"

    # ── Deep Work Score ──
    # On calcule AVANT d'ajouter pour détecter un changement de grade.
    pts_gagnes   = calculer_points_session()
    score_avant  = get_score_total()
    score_apres  = ajouter_score(pts_gagnes)
    grade_avant  = get_grade(score_avant)[0]
    grade_nom, grade_kanji, grade_col = get_grade(score_apres)
    nouveau_grade = grade_nom != grade_avant
    prochain_grade, pts_manquants = get_grade_suivant(score_apres)
    # Mettre à jour le badge sur l'accueil (il sera visible quand on fermera le popup)
    root.after(100, update_grade_accueil)

    def _fmt(secs):
        m, s = divmod(int(secs), 60)
        h, m = divmod(m, 60)
        return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"

    pop = ctk.CTkToplevel(root)
    pop.title(t("rapport.titre_fenetre"))
    pop.resizable(False, False)
    pop.transient(root)
    pop.grab_set()
    pop.configure(fg_color="#0A0908")
    _centrer_popup(pop, 460, 470)

    ctk.CTkLabel(pop, text=t("rapport.entete"),
                 font=("JetBrains Mono", 11, "bold"), text_color="#1F1B18").pack(pady=(18, 2))
    ctk.CTkLabel(pop, text=verdict,
                 font=("JetBrains Mono", 20, "bold"), text_color=couleur_v).pack(pady=(4, 14))

    # ── Tableau focus/distraction ──
    frame_rows = ctk.CTkFrame(pop, fg_color="#141210", corner_radius=3,
                               border_color="#2A2622", border_width=1)
    frame_rows.pack(fill="x", padx=28, pady=(0, 10))

    for label, valeur, col in [
        (t("rapport.temps_focus"),       _fmt(secondes_focus),    "#7A9B5C"),
        (t("rapport.temps_distraction"), _fmt(secondes_distraction), "#E63946"),
        (t("rapport.compliance_cible", seuil=seuil_pct), f"{ratio_pct}%",  couleur_v),
        (t("rapport.corrections_soft"),  str(nb_soft_corrections), "#8A8071"),
    ]:
        row = ctk.CTkFrame(frame_rows, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=3)
        ctk.CTkLabel(row, text=label, font=("JetBrains Mono", 11),
                     text_color="#5C574C", anchor="w").pack(side="left")
        ctk.CTkLabel(row, text=valeur, font=("JetBrains Mono", 11, "bold"),
                     text_color=col, anchor="e").pack(side="right")

    # Barre de progression compliance
    bar_bg = ctk.CTkFrame(pop, height=6, fg_color="#1F1B18", corner_radius=3)
    bar_bg.pack(fill="x", padx=28, pady=(0, 14))
    bar_bg.update_idletasks()
    fill_w = max(4, int(ratio * bar_bg.winfo_width()))
    ctk.CTkFrame(bar_bg, height=6, width=fill_w,
                 fg_color=couleur_v, corner_radius=3).place(x=0, y=0)

    # ── Section Deep Work Score ──
    frame_score = ctk.CTkFrame(pop, fg_color="#0A0908", corner_radius=3,
                                border_color="#2A1A00", border_width=1)
    frame_score.pack(fill="x", padx=28, pady=(0, 14))

    row_pts = ctk.CTkFrame(frame_score, fg_color="transparent")
    row_pts.pack(fill="x", padx=14, pady=(10, 4))
    ctk.CTkLabel(row_pts, text=t("rapport.points_gagnes"), font=("JetBrains Mono", 11),
                 text_color="#8A8071", anchor="w").pack(side="left")
    ctk.CTkLabel(row_pts, text=t("rapport.pts_suffixe", n=pts_gagnes), font=("JetBrains Mono", 12, "bold"),
                 text_color="#D4A24C", anchor="e").pack(side="right")

    row_grade = ctk.CTkFrame(frame_score, fg_color="transparent")
    row_grade.pack(fill="x", padx=14, pady=(0, 4))
    ctk.CTkLabel(row_grade, text=t("rapport.grade_actuel"), font=("JetBrains Mono", 11),
                 text_color="#8A8071", anchor="w").pack(side="left")
    ctk.CTkLabel(row_grade, text=t("rapport.grade_pts", nom=_grade_label(grade_nom), n=score_apres),
                 font=("JetBrains Mono", 12, "bold"),
                 text_color=grade_col, anchor="e").pack(side="right")

    if prochain_grade:
        row_next = ctk.CTkFrame(frame_score, fg_color="transparent")
        row_next.pack(fill="x", padx=14, pady=(0, 10))
        ctk.CTkLabel(row_next, text=t("rapport.prochain_grade"), font=("JetBrains Mono", 11),
                     text_color="#5C574C", anchor="w").pack(side="left")
        ctk.CTkLabel(row_next, text=t("rapport.prochain_grade_pts", nom=_grade_label(prochain_grade), n=pts_manquants),
                     font=("JetBrains Mono", 10), text_color="#5C574C", anchor="e").pack(side="right")
    else:
        ctk.CTkLabel(frame_score, text=t("rapport.grade_max"),
                     font=("JetBrains Mono", 10), text_color="#A82230").pack(pady=(0, 10))

    if nouveau_grade:
        ctk.CTkLabel(pop, text=t("rapport.nouveau_grade", nom=_grade_label(grade_nom).upper()),
                     font=("JetBrains Mono", 13, "bold"), text_color=grade_col).pack(pady=(0, 6))

    frame_btns_rapport = ctk.CTkFrame(pop, fg_color="transparent")
    frame_btns_rapport.pack(pady=(6, 14))

    def _exporter():
        duree_focus = secondes_focus
        generer_certificat(
            duree_secs=duree_focus,
            ratio_pct=ratio_pct,
            nb_corrections=nb_soft_corrections,
            pts_gagnes=pts_gagnes,
            score_total=score_apres,
            grade_nom=grade_nom,
            grade_col=grade_col,
            verdict=verdict,
            objectif=contrat_objectif.strip() if contrat_objectif else "",
        )

    ctk.CTkButton(frame_btns_rapport, text=t("rapport.btn_certificat"), width=160, height=40,
                  font=("JetBrains Mono", 12, "bold"),
                  fg_color="#16210F", hover_color="#1C2913",
                  border_width=1, border_color="#5C7A46",
                  text_color="#7A9B5C",
                  corner_radius=3, command=_exporter).pack(side="left", padx=(0, 8))

    ctk.CTkButton(frame_btns_rapport, text=t("rapport.btn_fermer"), width=160, height=40,
                  font=("JetBrains Mono", 12, "bold"),
                  fg_color=COLOR_PRIMARY, hover_color=COLOR_PRIMARY_HOVER,
                  corner_radius=3, command=pop.destroy).pack(side="left")


# --- BOUCLE DE SURVEILLANCE (mode limité) ---
def tick():
    global temps_restant, timer_active, victory_printed, paused, secondes_focus

    if not timer_active:
        return

    # ── Anti-inactivité : pause si 120s sans clavier/souris (sauf YouTube) ──
    if not is_youtube_active():
        idle_secs = get_idle_seconds()
        if idle_secs > INACTIVITY_LIMIT and not paused:
            paused = True
            label_statut.configure(
                text=t("tick.pause_inactivite"),
                text_color="red")
        elif idle_secs <= 3 and paused:
            paused = False
            label_statut.configure(
                text=t("tick.reprise_auto"),
                text_color="lightblue")
            root.after(3000, lambda: label_statut.configure(
                text=label_statut.cget("text") if root.winfo_exists() else ""))
    elif paused:
        paused = False
        label_statut.configure(
            text=t("tick.youtube_neutralise"),
            text_color="lightblue")
        root.after(3000, lambda: label_statut.configure(
            text=label_statut.cget("text") if root.winfo_exists() else ""))

    distraction_active = _surveiller_processus()

    if paused:
        root.after(1000, tick)
        return

    if not distraction_active:
        secondes_focus += 1

    temps_restant -= 1

    if temps_restant <= 0:
        temps_restant = 0
        victoire()
        return

    mins, secs = divmod(temps_restant, 60)
    label_chrono.configure(text=f"{mins:02d}:{secs:02d}")
    root.after(1000, tick)

# --- CHRONO INFINI ---
def tick_infini():
    global chrono_secondes, timer_active, paused, secondes_focus
    if not timer_active:
        return

    # ── Anti-inactivité : pause si 120s sans clavier/souris (sauf YouTube) ──
    if not is_youtube_active():
        idle_secs = get_idle_seconds()
        if idle_secs > INACTIVITY_LIMIT and not paused:
            paused = True
            label_statut.configure(
                text=t("tick.pause_inactivite"),
                text_color="red")
        elif idle_secs <= 3 and paused:
            paused = False
            label_statut.configure(
                text=t("tick.reprise_auto"),
                text_color="lightblue")
            root.after(3000, lambda: label_statut.configure(
                text=label_statut.cget("text") if root.winfo_exists() else ""))
    elif paused:
        paused = False
        label_statut.configure(
            text=t("tick.youtube_neutralise"),
            text_color="lightblue")
        root.after(3000, lambda: label_statut.configure(
            text=label_statut.cget("text") if root.winfo_exists() else ""))

    distraction_active = _surveiller_processus()

    if paused:
        root.after(1000, tick_infini)
        return

    if not distraction_active:
        secondes_focus += 1

    chrono_secondes += 1
    heures, reste = divmod(chrono_secondes, 3600)
    mins, secs = divmod(reste, 60)
    label_chrono.configure(text=f"{heures:02d}:{mins:02d}:{secs:02d}")
    root.after(1000, tick_infini)

# --- POMODORO ---
def demarrer_pomodoro():
    global timer_active, temps_restant, victory_printed, paused, pomodoro_phase
    global secondes_focus, secondes_distraction, session_start_time
    global soft_correction_active, soft_correction_countdown, nb_soft_corrections
    if timer_active:
        return
    timer_active = True
    paused = False
    victory_printed = False
    pomodoro_phase = "focus"
    temps_restant = POMODORO_FOCUS_SECS
    secondes_focus = 0
    secondes_distraction = 0
    session_start_time = time.time()
    soft_correction_active = False
    soft_correction_countdown = 0
    nb_soft_corrections = 0
    label_chrono.configure(text="25:00", text_color=COLOR_ACCENT)
    label_statut.configure(text=t("misc.pomo_focus"), text_color="lightblue")

    btn_terminer_infini.configure(text=t("misc.pomo_terminer_btn"),
                                   command=terminer_pomodoro)
    btn_terminer_infini.pack(pady=(15, 0))
    btn_abandonner.pack_forget()
    montrer_ecran(ecran_session)
    ensure_taskbar_visible()
    _session_verrouiller_fenetre()
    sauvegarder_etat()
    root.after(1000, tick_pomodoro)

def tick_pomodoro():
    global temps_restant, timer_active, victory_printed, paused, pomodoro_phase

    if not timer_active:
        return

    try:
        _tick_pomodoro_corps()
    except Exception as e:
        print(f"[ERREUR tick_pomodoro] {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()
        root.after(1000, tick_pomodoro)  # on relance quand même


def _tick_pomodoro_corps():
    global temps_restant, timer_active, victory_printed, paused, pomodoro_phase

    # ── Anti-inactivité ──
    if not is_youtube_active():
        idle_secs = get_idle_seconds()
        if idle_secs > INACTIVITY_LIMIT and not paused:
            paused = True
            label_statut.configure(
                text=t("tick.pause_inactivite"),
                text_color="red")
        elif idle_secs <= 3 and paused:
            paused = False
            label_statut.configure(
                text=t("tick.reprise_auto"),
                text_color="lightblue")
            root.after(3000, lambda: label_statut.configure(
                text=label_statut.cget("text") if root.winfo_exists() else ""))
    elif paused:
        paused = False
        label_statut.configure(
            text=t("tick.youtube_neutralise"),
            text_color="lightblue")
        root.after(3000, lambda: label_statut.configure(
            text=label_statut.cget("text") if root.winfo_exists() else ""))

    distraction_active = _surveiller_processus()

    if paused:
        root.after(1000, tick_pomodoro)
        return

    if not distraction_active and pomodoro_phase == "focus":
        secondes_focus += 1

    temps_restant -= 1

    if temps_restant <= 0:
        if pomodoro_phase == "focus":
            # Sauvegarder la session focus
            sauvegarder_stats(POMODORO_FOCUS_SECS / 60.0)
            # Notification de fin de focus
            notifier_fin_session()
            # Passer en pause
            pomodoro_phase = "break"
            temps_restant = POMODORO_BREAK_SECS
            label_chrono.configure(text="05:00", text_color="#00AAFF")
            label_statut.configure(text=t("misc.pomo_pause"), text_color="#00AAFF")
        else:
            # Pause terminée → retour au focus
            pomodoro_phase = "focus"
            temps_restant = POMODORO_FOCUS_SECS
            label_chrono.configure(text="25:00", text_color=COLOR_ACCENT)
            label_statut.configure(text=t("misc.pomo_focus"), text_color="lightblue")
        root.after(1000, tick_pomodoro)
        return

    mins, secs = divmod(temps_restant, 60)
    label_chrono.configure(text=f"{mins:02d}:{secs:02d}")
    root.after(1000, tick_pomodoro)

def terminer_pomodoro():
    """Termine manuellement une session pomodoro."""
    global timer_active, paused
    if session_cfg.get("hardcore"):
        _bloquer_sortie_hardcore()
        return
    timer_active = False
    paused = False
    _session_deverrouiller_fenetre()
    btn_terminer_infini.pack_forget()
    btn_abandonner.pack()
    supprimer_etat()

    # Sauvegarder le temps focus accumulé (si on était en phase focus)
    if pomodoro_phase == "focus":
        duree_min = (POMODORO_FOCUS_SECS - temps_restant) / 60.0
        if duree_min > 0.5:  # Au moins 30 secondes de focus utile
            sauvegarder_stats(duree_min)

    label_chrono.configure(text=t("misc.pomo_session_terminee"), text_color=COLOR_SUCCESS)
    label_statut.configure(text=t("misc.pomo_complete"), text_color=COLOR_SUCCESS)
    elapsed_pomo = int(time.time() - session_start_time) if session_start_time else POMODORO_FOCUS_SECS
    root.after(800, lambda: afficher_rapport_discipline(elapsed_pomo))

# --- TERMINER INFINI ---
def btn_terminer_session_callback():
    """Handler unifié pour le bouton 'Terminer la session'."""
    if session_type == "pomodoro":
        terminer_pomodoro()
    else:
        terminer_session_infini()

def terminer_session_infini():
    global timer_active, chrono_secondes, paused
    timer_active = False
    paused = False
    _session_deverrouiller_fenetre()
    btn_terminer_infini.pack_forget()
    btn_abandonner.pack()

    duree_min = chrono_secondes / 60.0
    sauvegarder_stats(duree_min)
    supprimer_etat()

    heures = chrono_secondes // 3600
    mins = (chrono_secondes % 3600) // 60
    secs = chrono_secondes % 60
    label_chrono.configure(text=t("misc.infini_validee"), text_color=COLOR_SUCCESS)
    label_statut.configure(
        text=t("session.bosse", heures=heures, mins=mins, secs=secs),
        text_color=COLOR_SUCCESS
    )
    root.after(800, lambda: afficher_rapport_discipline(chrono_secondes))

def demarrer_infini():
    global timer_active, chrono_secondes, victory_printed, paused
    global secondes_focus, secondes_distraction, session_start_time
    global soft_correction_active, soft_correction_countdown, nb_soft_corrections
    if timer_active:
        return
    timer_active = True
    paused = False
    victory_printed = False
    chrono_secondes = 0
    secondes_focus = 0
    secondes_distraction = 0
    session_start_time = time.time()
    soft_correction_active = False
    soft_correction_countdown = 0
    nb_soft_corrections = 0
    label_chrono.configure(text="00:00:00", text_color=("white", "white"))
    label_statut.configure(text="Focus actif — chrono infini", text_color="lightblue")
    btn_terminer_infini.configure(text=t("misc.pomo_terminer_btn"),
                                   command=terminer_session_infini)
    btn_terminer_infini.pack(pady=(15, 0))
    btn_abandonner.pack_forget()
    montrer_ecran(ecran_session)
    ensure_taskbar_visible()
    _session_verrouiller_fenetre()
    sauvegarder_etat()
    root.after(1000, tick_infini)

# =====================================================================
#   VERROUILLAGE FENÊTRE PENDANT SESSION
# =====================================================================

def _session_verrouiller_fenetre():
    """Empêche la fermeture de BeFree pendant une session (bouton Abandonner +
    Alt-F4 selon le mode). Ne force ni topmost ni reprise de focus : le blocage
    des apps interdites est déjà assuré par _surveiller_processus() indépendamment
    de la fenêtre active — l'utilisateur doit pouvoir travailler librement dans
    ses applications autorisées (whitelist) sans que BeFree ne reprenne la main."""
    _config_sortie_session()


def _bloquer_sortie_hardcore(event=None):
    """Mode Hardcore : Alt-F4 inopérant — flash d'avertissement, aucune sortie."""
    try:
        label_statut.configure(text="⛔  Mode Hardcore — aucune sortie, tiens jusqu'au bout !",
                               text_color="#E63946")
        root.after(2000, lambda: label_statut.configure(
            text="", text_color=COLOR_TEXT_DIM) if timer_active else None)
    except Exception:
        pass
    return "break"


def _config_sortie_session():
    """Configure le bouton Abandonner et Alt-F4 selon le mode de session.
    - hardcore : aucune sortie (bouton retiré, Alt-F4 inopérant)
    - libre    : confirmation simple
    - tunnel   : Tunnel de la Honte (3 étapes)"""
    mode = session_cfg.get("mode")
    if mode == "hardcore":
        try:
            btn_abandonner.pack_forget()
            btn_terminer_infini.pack_forget()
        except Exception:
            pass
        root.bind("<Alt-F4>", _bloquer_sortie_hardcore)
    elif mode == "libre":
        btn_abandonner.configure(command=_abandon_libre_confirmer)
        root.bind("<Alt-F4>", lambda e: _abandon_libre_confirmer())
    else:  # tunnel (et autres) : Tunnel de la Honte
        btn_abandonner.configure(command=ouvrir_tunnel_honte)
        root.bind("<Alt-F4>", lambda e: ouvrir_tunnel_honte())


def _abandon_libre_confirmer():
    """Mode Libre : popup de confirmation simple avant d'abandonner."""
    if not timer_active:
        abandonner_session()
        return
    dlg = ctk.CTkToplevel(root)
    dlg.title(t("confirm_arret.titre_fenetre"))
    dlg.configure(fg_color="#0A0908")
    dlg.resizable(False, False)
    dlg.transient(root)
    dlg.grab_set()
    _centrer_popup(dlg, 380, 200)

    ctk.CTkLabel(dlg, text=t("confirm_arret.titre"),
                 font=("Segoe UI", 16, "bold"), text_color="#E8DFCE").pack(pady=(34, 6))
    ctk.CTkLabel(dlg, text=t("confirm_arret.texte"),
                 font=("Segoe UI", 11), text_color="#8A8071").pack(pady=(0, 24))

    nav = ctk.CTkFrame(dlg, fg_color="transparent")
    nav.pack()

    def _oui():
        dlg.destroy()
        abandonner_session()

    ctk.CTkButton(nav, text=t("confirm_arret.non"), width=160, height=42,
                  font=("Segoe UI", 13, "bold"), corner_radius=3,
                  fg_color="#7A9B5C", hover_color="#5C7A46",
                  command=dlg.destroy).pack(side="left", padx=8)
    ctk.CTkButton(nav, text=t("confirm_arret.oui"), width=130, height=42,
                  font=("Segoe UI", 12), corner_radius=3,
                  fg_color="#241012", hover_color="#A82230", text_color="#E63946",
                  command=_oui).pack(side="left", padx=8)


def _session_deverrouiller_fenetre():
    """Relâche les blocages de sortie à la fin de la session."""
    root.attributes("-topmost", False)
    root.unbind("<Alt-F4>")


# --- DÉMARRAGE ---
def demarrer():
    global timer_active, temps_restant, victory_printed, duree_heures, duree_minutes, paused
    global secondes_focus, secondes_distraction, session_start_time
    global soft_correction_active, soft_correction_countdown, nb_soft_corrections

    if timer_active:
        return

    if mode_infini or session_type == "infini":
        demarrer_infini()
        return
    paused = False
    secondes_focus = 0
    secondes_distraction = 0
    session_start_time = time.time()
    soft_correction_active = False
    soft_correction_countdown = 0
    nb_soft_corrections = 0

    total_secondes = duree_heures * 3600 + duree_minutes * 60
    if total_secondes <= 0:
        return

    timer_active = True
    victory_printed = False
    temps_restant = total_secondes

    mins, secs = divmod(temps_restant, 60)
    label_chrono.configure(text=f"{mins:02d}:{secs:02d}", text_color=("white", "white"))

    heure_txt = f"{duree_heures}h " if duree_heures > 0 else ""
    label_statut.configure(text=f"Focus actif — {heure_txt}{duree_minutes} min",
                            text_color="lightblue")

    montrer_ecran(ecran_session)
    ensure_taskbar_visible()
    _session_verrouiller_fenetre()
    sauvegarder_etat()
    root.after(1000, tick)

# =====================================================================
#           SESSION PERSISTANCE & DÉMARRAGE AUTOMATIQUE
# =====================================================================

def sauvegarder_etat():
    """Sauvegarde l'état de la session dans session_en_cours.json."""
    apps_a_bloquer = [nom for nom, var in checkbox_vars.items() if not var.get()]
    now = time.time()

    etat = {
        "session_type": session_type,
        "mode_infini": mode_infini,
        "temps_restant": temps_restant,
        "chrono_secondes": chrono_secondes,
        "duree_heures": duree_heures,
        "duree_minutes": duree_minutes,
        "pomodoro_phase": pomodoro_phase,
        "apps_a_bloquer": apps_a_bloquer,
        "heure_fin": now + temps_restant if not mode_infini and session_type != "pomodoro" else None,
        "timestamp": now,
        "mode": session_cfg.get("mode"),
    }
    try:
        with open(SESSION_FILE, "w") as f:
            json.dump(etat, f, indent=2)
    except Exception:
        pass

def supprimer_etat():
    """Supprime le fichier de session et débloque les sites (session terminée ou abandonnée)."""
    _session_watchdog_desactiver()
    debloquer_sites()
    try:
        if os.path.exists(SESSION_FILE):
            os.remove(SESSION_FILE)
    except Exception:
        pass

def fichier_demarrage_existe():
    """Vérifie si le raccourci de démarrage Windows existe."""
    return os.path.exists(os.path.join(STARTUP_DIR, f"{STARTUP_NAME}.lnk"))

def basculer_demarrage_auto(activer):
    """Ajoute ou supprime le raccourci dans le dossier Démarrage Windows."""
    chemin_lnk = os.path.join(STARTUP_DIR, f"{STARTUP_NAME}.lnk")

    if activer:
        os.makedirs(STARTUP_DIR, exist_ok=True)
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortcut(chemin_lnk)
        if _frozen():
            shortcut.TargetPath = sys.executable
            shortcut.Arguments = ""
        else:
            shortcut.TargetPath = sys.executable
            shortcut.Arguments = f'"{os.path.abspath(__file__)}"'
        shortcut.WorkingDirectory = DATA_DIR
        shortcut.Description = "Hardcore Focus - Productivité sans distraction"
        shortcut.Save()
    else:
        try:
            if os.path.exists(chemin_lnk):
                os.remove(chemin_lnk)
        except Exception:
            pass

def afficher_popup_reactivation(temps_arret_sec=0):
    """Popup de réanimation — montrée après restauration d'une session."""
    global reactivation_popup_shown
    if reactivation_popup_shown:
        return
    # Mode Libre avec relaunch instantané (< 15 s) : pas de popup intrusif
    if session_cfg.get("mode") == "libre" and temps_arret_sec < 15:
        return
    reactivation_popup_shown = True

    popup_react = ctk.CTkToplevel(root)
    popup_react.title(t("bunker.titre_fenetre"))
    popup_react.resizable(False, False)
    popup_react.transient(root)
    popup_react.grab_set()
    popup_react.configure(fg_color=COLOR_BG)
    _centrer_popup(popup_react, 520, 280)

    lbl_titre = ctk.CTkLabel(
        popup_react, text=t("bunker.titre"),
        font=("Arial", 24, "bold"), text_color=COLOR_ACCENT
    )
    lbl_titre.pack(pady=(30, 12))

    if temps_arret_sec >= 30:
        h, reste = divmod(int(temps_arret_sec), 3600)
        m = reste // 60
        if h > 0:
            duree_txt = f"{h}h{m:02d}" if m else f"{h}h"
        else:
            duree_txt = f"{m} min"
        msg = t("bunker.msg_avec_duree", duree=duree_txt)
    else:
        msg = t("bunker.msg_defaut")

    lbl_msg = ctk.CTkLabel(
        popup_react, text=msg,
        font=("Arial", 15), text_color=COLOR_TEXT_DIM, justify="center"
    )
    lbl_msg.pack(pady=(0, 25))

    btn_go = ctk.CTkButton(
        popup_react, text=t("bunker.btn_go"), width=200, height=45,
        font=("Arial", 16, "bold"),
        fg_color=COLOR_PRIMARY, hover_color=COLOR_PRIMARY_HOVER,
        command=popup_react.destroy
    )
    btn_go.pack()

def charger_etat():
    """Lit session_en_cours.json et retourne le dict, ou None si absent/corrompu."""
    if not os.path.exists(SESSION_FILE):
        return None
    try:
        with open(SESSION_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return None

def restaurer_session(etat):
    """Restaure le timer et le blocage à partir d'un état sauvegardé."""
    global timer_active, temps_restant, mode_infini, chrono_secondes, \
           duree_heures, duree_minutes, session_type, pomodoro_phase

    session_type = etat.get("session_type", None)
    mode_infini = etat.get("mode_infini", False)
    chrono_secondes = etat.get("chrono_secondes", 0)
    duree_heures = etat.get("duree_heures", 0)
    duree_minutes = etat.get("duree_minutes", 0)
    pomodoro_phase = etat.get("pomodoro_phase", "focus")
    apps_a_bloquer = etat.get("apps_a_bloquer", [])

    # Restaurer le mode dans session_cfg pour que on_fermeture() le connaisse
    session_cfg["mode"] = etat.get("mode", "tunnel")
    # Restaurer aussi type + whitelist_apps, sinon _preparer_ecran_session()
    # affiche un libellé générique et "0 apps surveillées" (session_type et
    # apps_a_bloquer sont déjà disponibles à ce point, pas besoin d'attendre
    # la boucle de restauration des checkboxes plus bas).
    session_cfg["type"] = {"pomodoro": "pomodoro", "infini": "infini",
                            "normale": "fixe"}.get(session_type, "fixe")
    session_cfg["whitelist_apps"] = [nom for nom in checkbox_vars if nom not in apps_a_bloquer]
    _preparer_ecran_session()

    # Durée pendant laquelle le PC était éteint (affiché dans le popup)
    saved_ts = etat.get("timestamp", 0)
    temps_arret_sec = max(0, int(time.time() - saved_ts)) if saved_ts else 0

    # Restaurer les états des checkboxes (apps à bloquer = décochées)
    for nom in apps_a_bloquer:
        if nom in checkbox_vars:
            checkbox_vars[nom].set(False)

    timestamp = etat.get("timestamp", 0)

    # ── Restauration Pomodoro ──
    if session_type == "pomodoro":
        temps_restant = etat.get("temps_restant", POMODORO_FOCUS_SECS)
        if temps_restant <= 0:
            temps_restant = POMODORO_FOCUS_SECS

        timer_active = True
        victory_printed = False

        if pomodoro_phase == "focus":
            label_chrono.configure(text=f"{temps_restant // 60:02d}:{temps_restant % 60:02d}",
                                    text_color=COLOR_ACCENT)
            label_statut.configure(text=t("misc.pomo_focus_repris"), text_color="lightblue")
        else:
            label_chrono.configure(text=f"{temps_restant // 60:02d}:{temps_restant % 60:02d}",
                                    text_color="#00AAFF")
            label_statut.configure(text=t("misc.pomo_pause_repris"), text_color="#00AAFF")

        btn_terminer_infini.configure(text=t("misc.pomo_terminer_btn"), command=terminer_pomodoro)
        btn_terminer_infini.pack(pady=(15, 0))
        btn_abandonner.pack_forget()
        montrer_ecran(ecran_session)
        _session_verrouiller_fenetre()
        root.after(500, lambda: afficher_popup_reactivation(temps_arret_sec))
        root.after(1000, tick_pomodoro)
        return

    # ── Restauration Infini ──
    if mode_infini:
        # On N'ajoute PAS le temps d'arrêt PC au chrono — le timer était "en pause" pendant l'extinction
        timer_active = True
        victory_printed = False

        heures, reste = divmod(chrono_secondes, 3600)
        mins, secs = divmod(reste, 60)
        label_chrono.configure(text=f"{heures:02d}:{mins:02d}:{secs:02d}",
                                text_color=("white", "white"))
        label_statut.configure(text="Focus actif — chrono infini (repris)",
                                text_color="lightblue")
        btn_terminer_infini.configure(text=t("misc.pomo_terminer_btn"), command=terminer_session_infini)
        btn_terminer_infini.pack(pady=(15, 0))
        btn_abandonner.pack_forget()

        montrer_ecran(ecran_session)
        _session_verrouiller_fenetre()
        root.after(500, lambda: afficher_popup_reactivation(temps_arret_sec))
        root.after(1000, tick_infini)
        return

    # ── Restauration Normale ──
    # On utilise le temps_restant sauvegardé, pas heure_fin - now(),
    # pour que l'arrêt PC ne consomme pas du temps de session.
    temps_restant = etat.get("temps_restant", 0)

    if temps_restant <= 0:
        victoire()
        return

    timer_active = True
    victory_printed = False

    mins, secs = divmod(temps_restant, 60)
    label_chrono.configure(text=f"{mins:02d}:{secs:02d}",
                            text_color=("white", "white"))
    heure_txt = f"{duree_heures}h " if duree_heures > 0 else ""
    label_statut.configure(text=f"Focus actif — {heure_txt}{duree_minutes} min (repris)",
                            text_color="lightblue")

    montrer_ecran(ecran_session)
    _session_verrouiller_fenetre()
    root.after(500, lambda: afficher_popup_reactivation(temps_arret_sec))
    root.after(1000, tick)

# =====================================================================
#        SYSTÈME TRAY + NOTIFICATIONS
# =====================================================================

def notifier_fin_session():
    """Notification Windows toast : session terminée."""
    try:
        plyer_notification.notify(
            title="Hardcore Focus",
            message="Session terminée ! Prends 5 minutes de pause.",
            timeout=5,
            app_name="Hardcore Focus",
        )
    except Exception:
        pass  # silencieux si la notification échoue


def _creer_image_tray():
    """Génère une icône 64x64 pour la barre système (carré rouge)."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([2, 2, 62, 62], radius=8, fill="#E63946")
    draw.text((14, 12), "HF", fill="white", font=None)
    return img


def ouvrir_depuis_tray(icon):
    """Menu tray : réaffiche la fenêtre."""
    global tray_icon, tray_thread
    icon.stop()
    tray_icon = None
    tray_thread = None
    root.after(0, _restaurer_fenetre)


def _restaurer_fenetre():
    """Restaure la fenêtre après un retrait du tray."""
    root.deiconify()
    root.state("zoomed")
    root.lift()


def quitter_depuis_tray(icon):
    """Menu tray : arrêt complet de l'application."""
    global tray_icon, tray_thread
    icon.stop()
    tray_icon = None
    tray_thread = None
    if timer_active and session_cfg.get("mode") == "libre":
        root.after(0, _relancer_depuis_libre)
        return
    root.after(0, lambda: (root.destroy(), os._exit(0)))


def cacher_dans_tray():
    """Cache la fenêtre dans la barre système (systray)."""
    global tray_icon, tray_thread
    if tray_icon is not None:
        return  # déjà dans la barre

    root.withdraw()

    img = _creer_image_tray()
    menu = pystray.Menu(
        pystray.MenuItem("Ouvrir", ouvrir_depuis_tray, default=True),
        pystray.MenuItem("Quitter", quitter_depuis_tray),
    )
    tray_icon = pystray.Icon("hardcore_focus", img, "Hardcore Focus", menu)

    def _lancer():
        global tray_icon, tray_thread
        tray_icon.run()
        # Quand run() se termine (stop appelé), la thread se referme
        tray_icon = None
        tray_thread = None

    tray_thread = threading.Thread(target=_lancer, daemon=True)
    tray_thread.start()


# --- INTERCEPTION FERMETURE ---
def _relancer_depuis_libre():
    """Mode Libre : sauvegarde l'état et relance instantanément l'app."""
    sauvegarder_etat()
    subprocess.Popen(
        _cmd_relancer(),
        cwd=DATA_DIR,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os._exit(0)


def on_fermeture():
    """Clique X pendant une session → bloqué (tous modes). Sinon → quitter."""
    if timer_active:
        # Mode Libre : relancer l'app immédiatement
        if session_cfg.get("mode") == "libre":
            _relancer_depuis_libre()
            return
        # Tous les autres modes (Hardcore, Tunnel, Pomodoro…) :
        # ramener la fenêtre au premier plan et afficher un flash — impossible à fermer.
        try:
            root.deiconify()
            root.state("zoomed")
            root.attributes("-topmost", True)
            root.lift()
            if _HC_ACTIF or session_cfg.get("mode") == "hardcore":
                root.focus_force()
        except Exception:
            pass
        _flash_session_active()
        return
        cacher_dans_tray()
    else:
        root.destroy()
        os._exit(0)

# =====================================================================
#              MOT DE PASSE — CONFIG
# =====================================================================

_KEYRING_SERVICE = "BeFree"
_KEYRING_SMTP    = "smtp_pass"

def _smtp_pass_lire() -> str:
    """Lit le mot de passe SMTP depuis le trousseau Windows (Credential Manager)."""
    try:
        import keyring
        val = keyring.get_password(_KEYRING_SERVICE, _KEYRING_SMTP)
        return val or ""
    except Exception:
        # Fallback: chercher dans config.json (compatibilité ancienne)
        cfg = charger_config()
        return cfg.get("smtp_pass", "")

def _smtp_pass_ecrire(passwd: str):
    """Stocke le mot de passe SMTP dans le trousseau Windows (jamais en clair sur disque)."""
    try:
        import keyring
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_SMTP, passwd)
        # Supprimer l'ancienne version en clair si elle existe
        cfg = charger_config()
        if "smtp_pass" in cfg:
            del cfg["smtp_pass"]
            sauvegarder_config(cfg)
    except Exception:
        # Fallback si keyring non disponible
        cfg = charger_config()
        cfg["smtp_pass"] = passwd
        sauvegarder_config(cfg)

def charger_config():
    if not os.path.exists(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def sauvegarder_config(data):
    # Ne jamais écrire smtp_pass en clair — utiliser _smtp_pass_ecrire()
    data_safe = {k: v for k, v in data.items() if k != "smtp_pass"}
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(data_safe, f, indent=2)
    except Exception:
        pass


_FOUNDER_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # exclut 0/O, 1/I/L (ambigus à recopier)


def _generer_code_fondateur():
    return "BEFREE-" + "".join(secrets.choice(_FOUNDER_ALPHABET) for _ in range(6))


def hash_mdp(mdp):
    return hashlib.sha256(mdp.encode("utf-8")).hexdigest()


def mot_de_passe_actif():
    return bool(charger_config().get("mdp_hash"))


def verifier_mdp(mdp):
    stored = charger_config().get("mdp_hash")
    if not stored:
        return True
    return hash_mdp(mdp) == stored


def ouvrir_dialog_mdp(callback_ok, titre="Accès protégé",
                      message="Entrez le mot de passe :"):
    """Popup de saisie de mot de passe. Appelle callback_ok() si correct.
    Défauts de titre/message volontairement laissés en français : les 3
    appelants passent toujours titre/message explicitement (jamais exercés),
    donc pas de t() ici — la valeur serait figée en français de toute façon
    (évaluée à la définition de la fonction, avant que la langue ne soit lue)."""
    dialog = ctk.CTkToplevel(root)
    dialog.title(titre)
    dialog.resizable(False, False)
    dialog.transient(root)
    dialog.grab_set()
    dialog.configure(fg_color=COLOR_BG)
    _centrer_popup(dialog, 380, 230)

    ctk.CTkLabel(dialog, text=f"🔒  {titre}",
                 font=("Segoe UI", 15, "bold"),
                 text_color=COLOR_ACCENT).pack(pady=(28, 4))
    ctk.CTkLabel(dialog, text=message,
                 font=("Segoe UI", 11),
                 text_color=COLOR_TEXT_DIM).pack(pady=(0, 12))

    entry_mdp = ctk.CTkEntry(dialog, width=260, show="•",
                              fg_color="#141210", border_color="#2A2622",
                              text_color=COLOR_TEXT, justify="center",
                              font=("Segoe UI", 14))
    entry_mdp.pack(pady=(0, 6))
    entry_mdp.focus()

    lbl_err = ctk.CTkLabel(dialog, text="", font=("Segoe UI", 10),
                            text_color="#E63946")
    lbl_err.pack()

    def _valider(event=None):
        if verifier_mdp(entry_mdp.get()):
            dialog.destroy()
            callback_ok()
        else:
            lbl_err.configure(text=t("mdp.incorrect"))
            entry_mdp.delete(0, "end")

    entry_mdp.bind("<Return>", _valider)
    ctk.CTkButton(dialog, text=t("mdp.btn_valider"), width=200, height=38,
                  font=("Segoe UI", 13, "bold"),
                  fg_color=COLOR_PRIMARY, hover_color=COLOR_PRIMARY_HOVER,
                  corner_radius=3, command=_valider).pack(pady=(8, 0))


def _dialog_nouveau_mdp(titre, intro, callback_success):
    """Dialog création / changement de mot de passe (avec confirmation)."""
    dialog = ctk.CTkToplevel(root)
    dialog.title(titre)
    dialog.resizable(False, False)
    dialog.transient(root)
    dialog.grab_set()
    dialog.configure(fg_color=COLOR_BG)
    _centrer_popup(dialog, 400, 310)

    ctk.CTkLabel(dialog, text=f"🔒  {titre}",
                 font=("Segoe UI", 15, "bold"),
                 text_color=COLOR_ACCENT).pack(pady=(28, 4))
    ctk.CTkLabel(dialog, text=intro,
                 font=("Segoe UI", 11),
                 text_color=COLOR_TEXT_DIM).pack(pady=(0, 14))

    entry1 = ctk.CTkEntry(dialog, width=280, show="•",
                           placeholder_text=t("mdp.nouveau_placeholder"),
                           fg_color="#141210", border_color="#2A2622",
                           text_color=COLOR_TEXT, font=("Segoe UI", 13))
    entry1.pack(pady=(0, 8))
    entry2 = ctk.CTkEntry(dialog, width=280, show="•",
                           placeholder_text=t("mdp.confirmer_placeholder"),
                           fg_color="#141210", border_color="#2A2622",
                           text_color=COLOR_TEXT, font=("Segoe UI", 13))
    entry2.pack(pady=(0, 8))
    entry1.focus()

    lbl_err = ctk.CTkLabel(dialog, text="", font=("Segoe UI", 10),
                            text_color="#E63946")
    lbl_err.pack()

    def _confirmer():
        p1, p2 = entry1.get().strip(), entry2.get().strip()
        if not p1:
            lbl_err.configure(text=t("mdp.vide"))
            return
        if p1 != p2:
            lbl_err.configure(text=t("mdp.ne_correspond_pas"))
            entry1.delete(0, "end")
            entry2.delete(0, "end")
            return
        cfg = charger_config()
        cfg["mdp_hash"] = hash_mdp(p1)
        sauvegarder_config(cfg)
        dialog.destroy()
        callback_success()

    ctk.CTkButton(dialog, text="CONFIRMER", width=220, height=38,
                  font=("Segoe UI", 13, "bold"),
                  fg_color=COLOR_PRIMARY, hover_color=COLOR_PRIMARY_HOVER,
                  corner_radius=3, command=_confirmer).pack(pady=(8, 0))


def action_definir_mdp():
    _dialog_nouveau_mdp(
        "Définir un mot de passe",
        "Ce mot de passe protégera l'accès aux paramètres.",
        _refresh_mdp_section,
    )


def action_changer_mdp():
    def _apres_verif():
        _dialog_nouveau_mdp(
            t("mdp.changer_titre"),
            t("mdp.changer_intro"),
            _refresh_mdp_section,
        )
    ouvrir_dialog_mdp(_apres_verif,
                      titre=t("mdp.verification_titre"),
                      message=t("mdp.verification_message"))


def action_supprimer_mdp():
    def _supprimer():
        cfg = charger_config()
        cfg.pop("mdp_hash", None)
        sauvegarder_config(cfg)
        _refresh_mdp_section()
    if mot_de_passe_actif():
        ouvrir_dialog_mdp(_supprimer,
                          titre=t("mdp.suppression_titre"),
                          message=t("mdp.suppression_message"))
    else:
        _supprimer()


def _refresh_mdp_section():
    """Met à jour l'affichage de la section mot de passe selon l'état actuel."""
    if mot_de_passe_actif():
        lbl_mdp_statut.configure(text=t("mdp.actif"), text_color="#7A9B5C")
        btn_definir_mdp.pack_forget()
        btn_changer_mdp.pack(side="left", padx=5)
        btn_supprimer_mdp.pack(side="left", padx=5)
    else:
        lbl_mdp_statut.configure(text=t("mdp.aucun"), text_color=COLOR_TEXT_DIM)
        btn_changer_mdp.pack_forget()
        btn_supprimer_mdp.pack_forget()
        btn_definir_mdp.pack(side="left", padx=5)


# =====================================================================
#                    DEEP WORK SCORE
# =====================================================================
# Le score est stocké dans config.json sous la clé "deep_work_score".
# Il est cumulatif : il ne repart jamais à zéro entre les sessions.

def get_score_total():
    """Lit le score cumulé depuis config.json."""
    return charger_config().get("deep_work_score", 0)


def ajouter_score(points):
    """Ajoute des points au total et sauvegarde. Retourne le nouveau total."""
    cfg = charger_config()
    nouveau = max(0, cfg.get("deep_work_score", 0) + points)
    cfg["deep_work_score"] = nouveau
    sauvegarder_config(cfg)
    return nouveau


def get_grade(score):
    """Retourne (nom, kanji, couleur) du grade correspondant au score.
    On parcourt GRADES du bas vers le haut : le dernier seuil atteint gagne."""
    resultat = (GRADES[0][1], GRADES[0][2], GRADES[0][3])
    for seuil, nom, kanji, couleur in GRADES:
        if score >= seuil:
            resultat = (nom, kanji, couleur)
    return resultat                   # (nom, kanji, couleur)


def get_grade_suivant(score):
    """Retourne (nom_prochain_grade, points_manquants), ou (None, 0) si grade max."""
    for seuil, nom, kanji, couleur in GRADES:
        if score < seuil:
            return nom, seuil - score
    return None, 0


def calculer_points_session():
    """Formule du score de session :
        points = (minutes_focus) × (ratio_compliance) × (bonus_propreté)

    - minutes_focus   : secondes_focus / 60
    - ratio_compliance: secondes_focus / (focus + distraction)  → entre 0 et 1
    - bonus_propreté  : 1.2 si 0 soft-corrections, 1.0 sinon  (+20% si aucune app)

    Exemples :
        25 min focus parfait, 0 corrections  → 25 × 1.0 × 1.2 = 30 pts
        60 min focus à 80%,   0 corrections  → 60 × 0.8 × 1.2 = 57 pts
        60 min focus à 60%,   2 corrections  → 60 × 0.6 × 1.0 = 36 pts
    """
    total_active = secondes_focus + secondes_distraction
    if total_active < 30 or secondes_focus <= 0:
        return 0
    ratio = secondes_focus / total_active
    bonus = 1.2 if nb_soft_corrections == 0 else 1.0
    return max(0, int((secondes_focus / 60.0) * ratio * bonus))


def update_grade_accueil():
    """Rafraîchit le badge grade dans la sidebar.
    Appelé au démarrage et après chaque session."""
    score = get_score_total()
    nom, kanji, couleur = get_grade(score)
    prochain, manque = get_grade_suivant(score)
    sous = (t("grade.progression", score=score, manque=manque, prochain=_grade_label(prochain))
            if prochain else t("grade.max", score=score))
    lbl_sidebar_grade_nom.configure(text=_grade_label(nom))
    lbl_sidebar_grade_pts.configure(text=sous)
    sidebar_grade_seal.configure(fg_color=couleur)
    lbl_sidebar_grade_icon.configure(text=kanji, text_color="#0A0908")


# =====================================================================
#              NOUVEAU FLUX — HELPERS
# =====================================================================

def is_first_session() -> bool:
    """True si l'utilisateur n'a encore complété aucune session."""
    try:
        if not os.path.exists(STATS_FILE):
            return True
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            sessions = json.load(f)
        return len(sessions) == 0
    except Exception:
        return True


def charger_whitelist_sauvegardee() -> dict:
    """Retourne {"apps": [...], "blocked": [...]} ou valeurs vides."""
    try:
        if os.path.exists(WHITELIST_FILE):
            with open(WHITELIST_FILE, encoding="utf-8") as _f:
                data = json.load(_f)
            # Migration: ancienne clé "sites" → "blocked"
            if "sites" in data and "blocked" not in data:
                data["blocked"] = data.pop("sites")
            return data
    except Exception:
        pass
    return {"apps": [], "blocked": []}


def sauvegarder_whitelist_session(apps: list, sites: list):
    try:
        with open(WHITELIST_FILE, "w", encoding="utf-8") as f:
            json.dump({"apps": apps, "blocked": sites}, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def get_seuil_adaptive(minutes: int) -> int:
    """80% si session < 60 min, 60% sinon."""
    return 80 if minutes < 60 else 60


import re as _re_hosts
_DOMAINE_RE = _re_hosts.compile(
    r'^(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$'
)
_HOSTS_LOCK = threading.Lock()  # Sérialise les accès concurrents au fichier hosts

def _valider_domaine(d: str) -> str | None:
    """Retourne le domaine nettoyé s'il est valide, None sinon."""
    d = d.strip().lower()
    d = d.replace("https://", "").replace("http://", "").strip("/")
    d = d.split("/")[0]   # ignorer tout chemin après le domaine
    if d.startswith("www."):
        d = d[4:]
    if not d or not _DOMAINE_RE.match(d):
        return None
    if len(d) > 253:       # limite RFC
        return None
    return d

def bloquer_sites(domaines: list) -> bool:
    """Ajoute les domaines dans le fichier hosts. Retourne False si droits insuffisants."""
    if not domaines:
        return True
    valides = [v for d in domaines if (v := _valider_domaine(d)) is not None]
    if not valides:
        return True
    try:
        with _HOSTS_LOCK:
            with open(HOSTS_FILE, "r", encoding="utf-8", errors="ignore") as f:
                contenu = f.read()
            propre = _nettoyer_hosts(contenu)
            bloc = f"\n{HOSTS_MARKER_START}\n"
            for d in valides:
                bloc += f"127.0.0.1 {d}\n127.0.0.1 www.{d}\n"
            bloc += f"{HOSTS_MARKER_END}\n"
            with open(HOSTS_FILE, "w", encoding="utf-8") as f:
                f.write(propre + bloc)
        return True
    except PermissionError:
        return False
    except Exception:
        return False


def debloquer_sites():
    """Supprime le bloc BeFree du fichier hosts."""
    try:
        with _HOSTS_LOCK:
            with open(HOSTS_FILE, "r", encoding="utf-8", errors="ignore") as f:
                contenu = f.read()
            propre = _nettoyer_hosts(contenu)
            with open(HOSTS_FILE, "w", encoding="utf-8") as f:
                f.write(propre)
    except Exception:
        pass


def _nettoyer_hosts(contenu: str) -> str:
    lignes = contenu.split("\n")
    dans_bloc = False
    propres = []
    for ligne in lignes:
        if HOSTS_MARKER_START in ligne:
            dans_bloc = True
            continue
        if HOSTS_MARKER_END in ligne:
            dans_bloc = False
            continue
        if not dans_bloc:
            propres.append(ligne)
    return "\n".join(propres)


_SLIDE_EN_COURS = False

def slide_vers(ecran_suivant, ecran_courant=None):
    """Transition slide horizontal 200ms entre deux écrans du content_frame."""
    global _SLIDE_EN_COURS
    if _SLIDE_EN_COURS:
        return
    _SLIDE_EN_COURS = True

    tous = (ecran_accueil, ecran_stats, ecran_parametres, ecran_temps, ecran_apps,
            ecran_session, ecran_contrat, ecran_type_mode, ecran_type_session,
            ecran_whitelist_nouveau, ecran_whitelist_sites, ecran_verrouillage)

    for e in tous:
        if e is not ecran_courant and e is not ecran_suivant:
            e.pack_forget()

    w = content_frame.winfo_width() or 1000

    if ecran_courant is None:
        _SLIDE_EN_COURS = False
        ecran_suivant.pack(fill="both", expand=True)
        return

    ecran_courant.place(x=0, y=0, relwidth=1, relheight=1)
    ecran_suivant.place(x=w, y=0, relwidth=1, relheight=1)
    FRAMES = 12

    def _step(i=0):
        global _SLIDE_EN_COURS
        if i > FRAMES:
            ecran_courant.place_forget()
            ecran_courant.pack_forget()
            ecran_suivant.place_forget()
            ecran_suivant.pack(fill="both", expand=True)
            _SLIDE_EN_COURS = False
            return
        t = i / FRAMES
        ease = 1 - (1 - t) ** 2
        ecran_courant.place(x=-int(w * ease), y=0, relwidth=1, relheight=1)
        ecran_suivant.place(x=w - int(w * ease), y=0, relwidth=1, relheight=1)
        root.after(17, lambda: _step(i + 1))

    _step()


# ── Quarantaine ──────────────────────────────────────────────────────

def demarrer_quarantaine():
    """Lance une session Quarantaine (blocage multi-jours)."""
    global timer_active, session_type, quarantaine_active, quarantaine_fin_ts
    global secondes_focus, secondes_distraction, session_start_time
    global soft_correction_active, soft_correction_countdown, nb_soft_corrections

    nb_jours = session_cfg["nb_jours"]
    quarantaine_fin_ts = time.time() + nb_jours * 86400
    quarantaine_active = True
    session_type = "quarantaine"
    timer_active = True
    global paused
    paused = False
    secondes_focus = 0
    secondes_distraction = 0
    session_start_time = time.time()
    soft_correction_active = False
    soft_correction_countdown = 0
    nb_soft_corrections = 0

    cfg = charger_config()
    cfg["quarantaine_fin_ts"] = quarantaine_fin_ts
    cfg["quarantaine_actif"] = True
    sauvegarder_config(cfg)

    restant_secs = int(quarantaine_fin_ts - time.time())
    j, r = divmod(restant_secs, 86400)
    h, r2 = divmod(r, 3600)
    label_chrono.configure(
        text=f"{j}j {h:02d}h", text_color=COLOR_ACCENT)
    label_statut.configure(
        text=f"QUARANTAINE — {nb_jours} jour(s) · Pas de porte de sortie facile",
        text_color="#B3822F")

    btn_terminer_infini.pack_forget()
    btn_abandonner.pack()
    montrer_ecran(ecran_session)
    sauvegarder_etat()
    root.after(1000, tick_quarantaine)


def tick_quarantaine():
    global timer_active, quarantaine_active, quarantaine_fin_ts

    if not timer_active or not quarantaine_active:
        return

    distraction_active = _surveiller_processus()
    if not distraction_active:
        global secondes_focus
        secondes_focus += 1

    restant = int(quarantaine_fin_ts - time.time())
    if restant <= 0:
        _terminer_quarantaine()
        return

    j, r = divmod(restant, 86400)
    h, r2 = divmod(r, 3600)
    m = r2 // 60
    if j > 0:
        label_chrono.configure(text=f"{j}j {h:02d}h")
    else:
        label_chrono.configure(text=f"{h:02d}h{m:02d}m")

    root.after(1000, tick_quarantaine)


def _terminer_quarantaine():
    global timer_active, quarantaine_active
    timer_active = False
    quarantaine_active = False
    _session_deverrouiller_fenetre()
    cfg = charger_config()
    cfg["quarantaine_actif"] = False
    cfg["quarantaine_fin_ts"] = 0
    sauvegarder_config(cfg)
    supprimer_etat()
    debloquer_sites()
    duree = int(time.time() - session_start_time) if session_start_time else 0
    label_chrono.configure(text=t("quarantaine.terminee"), text_color=COLOR_SUCCESS)
    root.after(800, lambda: afficher_rapport_discipline(duree))


def ouvrir_popup_grade():
    """Popup — les 6 rangs du dojo, fidèle au mockup Claude Design.
    Chaque rang : sceau kanji, RANG NN, nom, plage de points, statut."""
    score = get_score_total()
    nom_actuel, _kanji_actuel, _coul_actuel = get_grade(score)

    pop = ctk.CTkToplevel(root)
    pop.title(t("grade.fenetre_titre"))
    pop.resizable(False, False)
    pop.transient(root)
    pop.grab_set()
    pop.configure(fg_color="#0A0908")
    _centrer_popup(pop, 560, 620)

    cadre = ctk.CTkFrame(pop, fg_color="#0A0908", corner_radius=0)
    cadre.pack(fill="both", expand=True, padx=28, pady=28)

    # ── En-tête ──
    ctk.CTkLabel(cadre, text=t("grade.popup_titre"),
                 font=theme_sumi.serif(28), text_color="#E8DFCE",
                 anchor="w").pack(fill="x")
    ctk.CTkLabel(cadre,
                 text=t("grade.popup_desc"),
                 font=("Segoe UI", 12), text_color="#B8AF9E", anchor="w",
                 justify="left", wraplength=500).pack(fill="x", pady=(4, 20))

    idx_actuel = next((i for i, g in enumerate(GRADES) if g[1] == nom_actuel), 0)

    for i, (seuil, nom, kanji, couleur) in enumerate(GRADES):
        seuil_haut = GRADES[i + 1][0] if i + 1 < len(GRADES) else None
        plage = (t("grade.plage_bornee", bas=seuil, haut=seuil_haut) if seuil_haut
                 else t("grade.plage_ouverte", bas=seuil))

        est_actuel = (i == idx_actuel)
        est_ultime = (i == len(GRADES) - 1)
        atteint = (i < idx_actuel)

        if est_ultime and est_actuel:
            row_bg, row_border, seal_bg, seal_fg, seal_txt = "#E63946", "#E63946", "#0A0908", "#E63946", "#0A0908"
        elif est_actuel:
            row_bg, row_border, seal_bg, seal_fg, seal_txt = "#1F1B18", "#E63946", "#E63946", "#0A0908", "#E8DFCE"
        else:
            row_bg, row_border, seal_bg, seal_fg, seal_txt = "#141210", "#2A2622", "#1F1B18", "#8A8071", "#E8DFCE"

        row = ctk.CTkFrame(cadre, fg_color=row_bg, corner_radius=0,
                           border_width=2 if est_actuel else 0,
                           border_color=row_border)
        row.pack(fill="x", pady=6)
        # liseré gauche pour les rangs inactifs (border-left du mockup)
        if not est_actuel and not est_ultime:
            ctk.CTkFrame(row, width=2, fg_color="#2A2622", corner_radius=0).place(x=0, y=0, relheight=1)

        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=12)

        # Sceau kanji
        seal = ctk.CTkFrame(inner, width=36, height=36, corner_radius=18,
                            fg_color=seal_bg,
                            border_width=0 if est_actuel or est_ultime else 2,
                            border_color="#2A2622")
        seal.pack(side="left")
        seal.pack_propagate(False)
        ctk.CTkLabel(seal, text=kanji, font=theme_sumi.serif(16),
                     text_color=seal_fg).place(relx=0.5, rely=0.5, anchor="center")

        # RANG NN
        ctk.CTkLabel(inner, text=t("grade.rang_num", n=i + 1), font=theme_sumi.mono(10),
                     text_color=seal_txt if est_ultime else ("#E63946" if est_actuel else "#8A8071"),
                     width=70, anchor="w").pack(side="left", padx=(16, 0))

        # Statut à droite
        if est_ultime:
            statut, statut_col = t("grade.statut_ultime"), "#0A0908"
        elif est_actuel:
            statut, statut_col = t("grade.statut_en_cours"), "#D4A24C"
        elif atteint:
            statut, statut_col = t("grade.statut_termine"), "#7A9B5C"
        else:
            statut, statut_col = t("grade.statut_verrouille"), "#8A8071"
        ctk.CTkLabel(inner, text=statut, font=theme_sumi.mono(10),
                     text_color=statut_col, width=90, anchor="e").pack(side="right")

        # Plage de points
        ctk.CTkLabel(inner, text=plage, font=theme_sumi.mono(11),
                     text_color=seal_txt if est_ultime else "#8A8071",
                     anchor="e").pack(side="right", padx=(0, 14))

        # Nom (+ barre de progression si actuel)
        centre = ctk.CTkFrame(inner, fg_color="transparent")
        centre.pack(side="left", fill="x", expand=True, padx=(14, 0))
        nom_col = "#0A0908" if est_ultime else ("#E8DFCE" if (est_actuel or atteint) else "#B8AF9E")
        ligne_nom = ctk.CTkFrame(centre, fg_color="transparent")
        ligne_nom.pack(fill="x", anchor="w")
        ctk.CTkLabel(ligne_nom, text=_grade_label(nom), font=theme_sumi.serif(20 if est_actuel else 18),
                     text_color=nom_col).pack(side="left")
        if est_actuel:
            ctk.CTkLabel(ligne_nom, text=t("grade.actuel_suffixe"), font=theme_sumi.mono(10),
                         text_color="#8A8071").pack(side="left")
            # barre de progression vers le rang suivant
            if seuil_haut:
                pct = min(1.0, max(0.0, (score - seuil) / max(1, seuil_haut - seuil)))
                barre_bg = ctk.CTkFrame(centre, height=2, fg_color="#2A2622", corner_radius=0)
                barre_bg.pack(fill="x", pady=(6, 0))
                barre_bg.pack_propagate(False)
                barre_fill = ctk.CTkFrame(barre_bg, height=2, fg_color="#D4A24C", corner_radius=0)
                barre_fill.place(relx=0, rely=0, relwidth=pct, relheight=1)

    ctk.CTkButton(cadre, text="Fermer", height=36, width=140,
                  font=theme_sumi.ui(12, "bold"),
                  fg_color="transparent", hover_color="#1F1B18",
                  border_width=1, border_color="#E8DFCE", text_color="#E8DFCE",
                  corner_radius=3,
                  command=pop.destroy).pack(pady=(18, 0))


# =====================================================================
#              MODE MORNING-ZERO
# =====================================================================

MORNING_ZERO_DUREE = 30 * 60   # 30 minutes
MORNING_ZERO_H_MIN = 4         # 04:00
MORNING_ZERO_H_MAX = 12        # 11:59 inclus

CITATIONS_MATIN = [
    "Le silence du matin\nappartient aux forts.",
    "Chaque matin est une chance\nde tout reconstruire.",
    "Discipline le matin.\nLiberté le soir.",
    "Pendant que tu hésites,\nquelqu'un travaille déjà.",
    "Le matin décide\ndu reste de la journée.",
]


def morning_zero_est_actif():
    return bool(charger_config().get("morning_zero_actif", False))


def _draw_foret(canvas, w, h):
    """Forêt silhouette dessinée au Canvas — style nuit pré-aube."""
    import random
    rng = random.Random(42)

    ciel_h = int(h * 0.70)

    # Ciel gradient sombre
    nb = 32
    for i in range(nb):
        y1 = int(i * ciel_h / nb)
        y2 = int((i + 1) * ciel_h / nb)
        r = min(4 + i, 28)
        g = min(4 + i, 24)
        b = min(12 + i * 2, 55)
        canvas.create_rectangle(0, y1, w, y2, fill=f"#{r:02x}{g:02x}{b:02x}", outline="")

    # Sol noir
    canvas.create_rectangle(0, ciel_h, w, h, fill="#0A0908", outline="")

    # Lune (halo + disque)
    lx, ly = int(w * 0.78), 50
    canvas.create_oval(lx - 12, ly - 12, lx + 52, ly + 52,
                       fill="#1A1820", outline="")
    canvas.create_oval(lx, ly, lx + 40, ly + 40, fill="#D4CCBA", outline="")

    # Étoiles
    for _ in range(90):
        sx = rng.randint(0, w)
        sy = rng.randint(0, int(ciel_h * 0.65))
        sr = rng.choice([1, 1, 1, 2])
        b_val = rng.randint(100, 210)
        canvas.create_oval(sx, sy, sx + sr, sy + sr,
                           fill=f"#{b_val:02x}{b_val:02x}{min(b_val+25,255):02x}",
                           outline="")

    def pin(cx, base, haut, larg, col):
        for tier in range(3):
            ty = base - haut + tier * int(haut * 0.27)
            by = ty + int(haut * 0.48)
            lw = int(larg * (0.32 + tier * 0.34))
            canvas.create_polygon(cx, ty, cx - lw, by, cx + lw, by,
                                  fill=col, outline="")
        canvas.create_rectangle(cx - 3, base - int(haut * 0.10), cx + 3, base,
                                fill=col, outline="")

    # Rangée arrière — petits, gris-vert très sombre
    for i in range(13):
        cx = int(w * (i + 0.5) / 13) + rng.randint(-18, 18)
        pin(cx, ciel_h + rng.randint(-5, 10),
            rng.randint(int(h * 0.24), int(h * 0.35)),
            rng.randint(22, 38), "#080D0A")

    # Rangée avant — grands, quasi noirs
    for i in range(8):
        cx = int(w * (i + 0.5) / 8) + rng.randint(-28, 28)
        pin(cx, ciel_h + rng.randint(8, 28),
            rng.randint(int(h * 0.40), int(h * 0.55)),
            rng.randint(38, 62), "#050807")

    # Brume au sol
    for i in range(10):
        yf = ciel_h - 25 + i * 10
        v = max(4, 16 - i * 1)
        canvas.create_rectangle(0, yf, w, yf + 12,
                                fill=f"#{v:02x}{v:02x}{v+2:02x}", outline="")

    # Vignette (bords sombres)
    marge = max(w, h) // 3
    canvas.create_oval(-marge, -marge, w + marge, h + marge,
                       outline="#0A0908", width=marge)


def lancer_morning_zero(secondes_restantes):
    """Overlay plein-écran bloquant — Morning-Zero."""
    import random
    citation = random.choice(CITATIONS_MATIN)

    overlay = tk.Toplevel(root)
    overlay.overrideredirect(True)
    overlay.attributes("-topmost", True)
    sw = overlay.winfo_screenwidth()
    sh = overlay.winfo_screenheight()
    overlay.geometry(f"{sw}x{sh}+0+0")
    overlay.configure(bg="#0A0908")

    # Bloquer toute tentative de fermeture clavier
    for seq in ("<Alt-F4>", "<Escape>", "<Control-w>", "<Control-F4>"):
        overlay.bind(seq, lambda e: "break")
    overlay.protocol("WM_DELETE_WINDOW", lambda: None)

    canvas = tk.Canvas(overlay, width=sw, height=sh, bg="#0A0908",
                       highlightthickness=0, bd=0)
    canvas.pack(fill="both", expand=True)

    _draw_foret(canvas, sw, sh)


    # Compteur — ombre puis texte
    cx_t, cy_t = sw // 2, int(sh * 0.43)
    canvas.create_text(cx_t + 2, cy_t + 2,
                       text="30:00", font=("JetBrains Mono", 80, "bold"), fill="#0A0908",
                       tags="timer_shadow")
    canvas.create_text(cx_t, cy_t,
                       text="30:00", font=("JetBrains Mono", 80, "bold"), fill="#B8AF9E",
                       tags="timer_text")

    # Barre de progression fine
    bar_w = int(sw * 0.32)
    bx1 = sw // 2 - bar_w // 2
    bx2 = sw // 2 + bar_w // 2
    by = int(sh * 0.575)
    canvas.create_rectangle(bx1, by, bx2, by + 3, fill="#141210", outline="")
    canvas.create_rectangle(bx1, by, bx2, by + 3, fill="#222240",
                            outline="", tags="barre_fill")

    # Citation — juste sous la barre de progression
    canvas.create_text(sw // 2, by + 28,
                       text=citation, font=("JetBrains Mono", 13),
                       fill="#B8AF9E", justify="center")


    restant = [int(secondes_restantes)]

    def _tick():
        if not overlay.winfo_exists():
            return
        restant[0] -= 1
        if restant[0] <= 0:
            _terminer_morning_zero(overlay)
            return
        mm = restant[0] // 60
        ss = restant[0] % 60
        txt = f"{mm:02d}:{ss:02d}"
        canvas.itemconfig("timer_text", text=txt)
        canvas.itemconfig("timer_shadow", text=txt)
        ratio_fait = 1 - (restant[0] / MORNING_ZERO_DUREE)
        fill_x2 = bx1 + int(bar_w * ratio_fait)
        canvas.coords("barre_fill", bx1, by, max(bx1 + 2, fill_x2), by + 3)
        overlay.after(1000, _tick)

    # Affichage initial correct
    mm0 = restant[0] // 60
    ss0 = restant[0] % 60
    txt0 = f"{mm0:02d}:{ss0:02d}"
    canvas.itemconfig("timer_text", text=txt0)
    canvas.itemconfig("timer_shadow", text=txt0)

    overlay.after(1000, _tick)
    overlay.focus_force()


def _terminer_morning_zero(overlay):
    """Ferme l'overlay et réinitialise la session."""
    try:
        import winsound
        threading.Thread(target=lambda: (
            winsound.Beep(660, 120), winsound.Beep(880, 200)
        ), daemon=True).start()
    except Exception:
        pass
    cfg = charger_config()
    today_str = datetime.now().date().isoformat()
    cfg["morning_zero_session"] = {"date": today_str, "done": True}
    sauvegarder_config(cfg)
    try:
        overlay.destroy()
    except Exception:
        pass


def verifier_morning_zero():
    """Appelé au démarrage — déclenche Morning-Zero si nécessaire."""
    if not morning_zero_est_actif():
        return
    maintenant = datetime.now()
    heure = maintenant.hour
    if not (MORNING_ZERO_H_MIN <= heure < MORNING_ZERO_H_MAX):
        return

    cfg = charger_config()
    session = cfg.get("morning_zero_session")
    today_str = maintenant.date().isoformat()

    if session and session.get("date") == today_str:
        if session.get("done"):
            return  # Déjà terminé aujourd'hui
        # Session en cours — calculer le temps restant
        elapsed = int(time.time() - session.get("start_ts", 0))
        restant = MORNING_ZERO_DUREE - elapsed
        if restant > 0:
            root.after(300, lambda: lancer_morning_zero(restant))
        return

    # Nouvelle session aujourd'hui
    cfg["morning_zero_session"] = {
        "date": today_str,
        "start_ts": time.time(),
    }
    sauvegarder_config(cfg)
    root.after(300, lambda: lancer_morning_zero(MORNING_ZERO_DUREE))


# =====================================================================
#              PHYSICAL LOCK — CLÉ USB
# =====================================================================

def lister_usb_connectes():
    """Retourne [(mountpoint, serial_int, label)] pour chaque USB amovible branché."""
    resultats = []
    try:
        for p in psutil.disk_partitions():
            if "removable" not in p.opts:
                continue
            try:
                info = win32api.GetVolumeInformation(p.mountpoint)
                label  = info[0] if info[0] else "USB"
                serial = info[1]   # int unique au volume
                resultats.append((p.mountpoint, serial, label))
            except Exception:
                continue
    except Exception:
        pass
    return resultats


def cle_usb_enregistree():
    """Retourne {"serial": int, "label": str} ou None."""
    cfg = charger_config()
    s = cfg.get("usb_serial")
    l = cfg.get("usb_label", "Clé USB")
    return {"serial": s, "label": l} if s is not None else None


def physical_lock_actif():
    return bool(charger_config().get("physical_lock_actif", False))


def verifier_cle_usb():
    """True si la clé enregistrée est actuellement branchée."""
    cle = cle_usb_enregistree()
    if not cle:
        return False
    for _, serial, _ in lister_usb_connectes():
        if serial == cle["serial"]:
            return True
    return False


def _afficher_erreur_cle_usb(parent=None):
    """Popup courte : clé USB absente."""
    win = ctk.CTkToplevel(parent or root)
    win.title(t("usb.requise_titre"))
    win.resizable(False, False)
    win.transient(root)
    win.grab_set()
    win.configure(fg_color="#0A0908")
    _centrer_popup(win, 380, 190)

    cle = cle_usb_enregistree()
    nom = cle["label"] if cle else t("usb.ta_cle_defaut")

    ctk.CTkLabel(win, text=t("usb.absente"),
                 font=("JetBrains Mono", 14, "bold"), text_color="#E63946").pack(pady=(24, 6))
    ctk.CTkLabel(win, text=t("usb.insere", nom=nom),
                 font=("JetBrains Mono", 11), text_color="#5C574C").pack()
    ctk.CTkButton(win, text=t("usb.ok"), width=120, height=34,
                  font=("JetBrains Mono", 11, "bold"),
                  fg_color="#141210", hover_color="#28231F", text_color="#8A8071",
                  corner_radius=4, command=win.destroy).pack(pady=(20, 0))


def ouvrir_dialog_enregistrer_cle():
    """Popup de sélection de la clé USB à enregistrer."""
    usbs = lister_usb_connectes()

    win = ctk.CTkToplevel(root)
    win.title(t("usb.enregistrer_titre_fenetre"))
    win.resizable(False, False)
    win.transient(root)
    win.grab_set()
    win.configure(fg_color="#0A0908")

    ctk.CTkLabel(win, text=t("usb.choisir"),
                 font=("JetBrains Mono", 13, "bold"), text_color="#8A8071").pack(pady=(22, 6))
    ctk.CTkLabel(win, text=t("usb.branche"),
                 font=("JetBrains Mono", 10), text_color="#3A352E").pack(pady=(0, 14))

    if not usbs:
        ctk.CTkLabel(win, text=t("usb.aucun_detecte"),
                     font=("JetBrains Mono", 11), text_color="#5C3A38").pack(pady=(0, 14))
        win.geometry("360x160")
        win.update_idletasks()
        x = root.winfo_x() + (root.winfo_width()  - 360) // 2
        y = root.winfo_y() + (root.winfo_height() - 160) // 2
        win.geometry(f"+{x}+{y}")
        ctk.CTkButton(win, text=t("usb.fermer"), width=120, height=32,
                      font=("JetBrains Mono", 10), fg_color="#141210",
                      hover_color="#28231F", text_color="#8A8071",
                      corner_radius=4, command=win.destroy).pack(pady=(0, 16))
        return

    frame_liste = ctk.CTkFrame(win, fg_color="transparent")
    frame_liste.pack(padx=24, pady=(0, 10), fill="x")

    def _choisir(serial, label):
        cfg = charger_config()
        cfg["usb_serial"] = serial
        cfg["usb_label"]  = label
        sauvegarder_config(cfg)
        _refresh_physical_lock_section()
        win.destroy()

    for mountpoint, serial, label in usbs:
        txt = f"  {label}  ({mountpoint})  —  S/N {serial}"
        ctk.CTkButton(frame_liste, text=txt, anchor="w",
                      font=("JetBrains Mono", 11),
                      fg_color="#141210", hover_color="#1F1B18",
                      border_width=1, border_color="#1F1B18",
                      text_color="#8A8071", corner_radius=3, height=38,
                      command=lambda s=serial, l=label: _choisir(s, l)
                      ).pack(fill="x", pady=3)

    h_win = 130 + len(usbs) * 50
    win.geometry(f"440x{h_win}")
    win.update_idletasks()
    x = root.winfo_x() + (root.winfo_width()  - 440) // 2
    y = root.winfo_y() + (root.winfo_height() - h_win) // 2
    win.geometry(f"+{x}+{y}")


def _refresh_physical_lock_section():
    """Met à jour le statut affiché dans la section Physical Lock des Réglages."""
    cle = cle_usb_enregistree()
    if cle:
        lbl_usb_statut.configure(
            text=t("usb.cle_enregistree", label=cle['label'], serial=cle['serial']),
            text_color="#7A9B5C")
        btn_supprimer_cle.pack(side="left", padx=4)
        btn_enregistrer_cle.configure(text=t("usb.changer_cle"))
    else:
        lbl_usb_statut.configure(text=t("parametres.usb_aucune_cle"), text_color="#8A8071")
        btn_supprimer_cle.pack_forget()
        btn_enregistrer_cle.configure(text=t("parametres.usb_enregistrer"))


def action_supprimer_cle():
    cfg = charger_config()
    cfg.pop("usb_serial", None)
    cfg.pop("usb_label",  None)
    cfg["physical_lock_actif"] = False
    sauvegarder_config(cfg)
    var_physical_lock.set(False)
    _refresh_physical_lock_section()


# =====================================================================
#              CONTRAT DE TRAVAIL
# =====================================================================

def ouvrir_contrat():
    """Navigue vers l'écran Contrat avant de lancer la session."""
    entry_objectif.delete("1.0", "end")
    entry_signature.delete(0, "end")
    lbl_contrat_err.configure(text="")
    # Rafraîchir l'en-tête du feuillet : n° de session + date
    try:
        from stats_manager import charger_sessions
        num_session = len(charger_sessions()) + 1
    except Exception:
        num_session = 1
    session_cfg["num_session"] = num_session
    lbl_contrat_meta.configure(text=t("contrat.meta", n=num_session))
    now = datetime.now()
    lbl_contrat_date.configure(text=now.strftime("%d·%m·%Y · %H:%M"))
    # Pré-remplir la signature avec le nom d'utilisateur Windows
    prenom = _nom_utilisateur_local()
    if prenom:
        entry_signature.insert(0, prenom)
    montrer_ecran(ecran_contrat)


def valider_contrat():
    """Vérifie objectif + signature, puis déclenche l'animation et navigue vers la whitelist."""
    global contrat_objectif
    objectif = entry_objectif.get("1.0", "end").strip()
    signature = entry_signature.get().strip()

    if len(objectif) < 10:
        lbl_contrat_err.configure(text=t("contrat.err_objectif"))
        return
    if len(signature) < 2:
        lbl_contrat_err.configure(text=t("contrat.err_signature"))
        return

    contrat_objectif = objectif
    session_cfg["objectif"] = objectif
    short = objectif[:80] + ("…" if len(objectif) > 80 else "")
    label_objectif_session.configure(text=f"« {short} »")
    _wl_afficher(ecran_contrat)


def _apres_animation_whitelist():
    """Après l'animation du cadenas depuis l'écran sites : lance la session directement."""
    global whitelist_from_recap
    whitelist_from_recap = False
    _lancer_session_finale()


def _animer_serrure_et_lancer(parent=None, on_done=None):
    """Overlay fullscreen : le cadenas grossit, l'anse se ferme avec un SFX, puis appelle on_done."""
    if parent is None:
        parent = ecran_whitelist_nouveau
    if on_done is None:
        on_done = _apres_animation_whitelist
    overlay = tk.Canvas(parent, bg="#0A0908", highlightthickness=0)
    overlay.place(x=0, y=0, relwidth=1, relheight=1)
    overlay.update_idletasks()

    w = overlay.winfo_width() or 900
    h = overlay.winfo_height() or 600

    # Départ depuis le centre du bandeau, arrivée au centre de l'écran
    start_cx, start_cy = w // 2, 85
    end_cx,   end_cy   = w // 2, h // 2

    def _ease_out(t):
        return 1 - (1 - t) ** 3

    # SFX clé en thread (winsound non-bloquant)
    def _son_cle():
        try:
            import winsound
            winsound.Beep(1100, 35)
            time.sleep(0.07)
            winsound.Beep(750,  55)
            time.sleep(0.05)
            winsound.Beep(480, 100)
        except Exception:
            pass

    def _son_clac():
        try:
            import winsound
            winsound.Beep(520, 45)
        except Exception:
            pass

    threading.Thread(target=_son_cle, daemon=True).start()

    TOTAL = 28
    step = [0]
    clac_done = [False]

    def _frame():
        overlay.delete("all")
        t = step[0] / TOTAL
        e = _ease_out(t)

        cx = int(start_cx + (end_cx - start_cx) * e)
        cy = int(start_cy + (end_cy - start_cy) * e)
        scale = 1.0 + e * 2.2   # grossit de 1× à 3.2×

        # Fond qui s'assombrit légèrement en fin
        if t > 0.75:
            fade = (t - 0.75) / 0.25
            darkness = int(fade * 30)
            overlay.create_rectangle(0, 0, w, h,
                                     fill=f"#{darkness:02x}0000", outline="")

        # Octogone extérieur
        r1 = int(47 * scale)
        r2 = int(36 * scale)
        bw = max(1, int(2 * scale))
        overlay.create_polygon(_octagon_pts(cx, cy, r1),
                               outline="#A82230", fill="#0A0908", width=bw)
        overlay.create_polygon(_octagon_pts(cx, cy, r2),
                               outline="#A82230", fill="", width=1)

        # Tirets déco entre les deux octogones
        for i in range(8):
            a = math.pi / 8 + i * math.pi / 4
            x1 = cx + (r2 + 1) * math.cos(a)
            y1 = cy + (r2 + 1) * math.sin(a)
            x2 = cx + (r1 - 1) * math.cos(a)
            y2 = cy + (r1 - 1) * math.sin(a)
            overlay.create_line(x1, y1, x2, y2, fill="#A82230", width=1)

        # Anse : se referme progressivement après t=0.6
        if t < 0.65:
            shackle_off = int(8 * scale * (1 - t / 0.65))
        else:
            shackle_off = 0
            if not clac_done[0]:
                clac_done[0] = True
                threading.Thread(target=_son_clac, daemon=True).start()

        _draw_cadenas(overlay, cx, cy - int(6 * scale), scale=scale,
                      shackle_offset=shackle_off)

        if step[0] < TOTAL:
            step[0] += 1
            overlay.after(30, _frame)
        else:
            # Fade noir → lancement
            overlay.create_rectangle(0, 0, w, h, fill="#0A0908", outline="")
            overlay.after(120, lambda: (overlay.destroy(), on_done()))

    _frame()


# =====================================================================
#                 INTERFACE GRAPHIQUE — BeFree
# =====================================================================

# --- COULEURS BeFree ---
BF_COLOR_BG          = "#0A0908"
BF_COLOR_SIDEBAR     = "#141210"
BF_COLOR_SIDEBAR_H   = "#1F1B18"
BF_COLOR_SIDEBAR_T   = "#8A8071"
BF_COLOR_SEPARATOR   = "#2A2622"
BF_COLOR_BTN_BG      = "#141210"
BF_COLOR_BTN_HOVER   = "#28231F"
BF_COLOR_BTN_TEXT    = "#B8AF9E"
BF_COLOR_BTN_SHADOW  = "#0A0908"
BF_COLOR_LINK        = "#8A8071"
BF_COLOR_LINK_HOVER  = "#8A8071"
BF_COLOR_ACCENT_CRIMSON = "#A82230"
BF_FONT_TITLE        = ("Segoe UI", 20, "bold")
BF_FONT_MENU         = ("Segoe UI", 12)
BF_FONT_BTN          = ("Segoe UI", 18)
BF_FONT_LINK         = ("Segoe UI", 11)

# --- FENÊTRE PRINCIPALE ---
root = ctk.CTk()
root.title("BeFree")
root.iconbitmap(os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", "befree.ico"))
root.configure(fg_color=BF_COLOR_BG)

# Langue : décidée ICI, avant la construction de tout widget en dessous, pour
# qu'aucun texte ne soit jamais construit dans la mauvaise langue. Premier
# lancement (pas de clé "lang") → reste en français par défaut le temps de
# cette session, l'écran de choix (fin du fichier, bloc LANCEMENT) relance
# l'app juste après le choix pour repartir propre dès le premier widget.
_cfg_langue_initiale = charger_config()
if "lang" in _cfg_langue_initiale:
    i18n.set_language(_cfg_langue_initiale["lang"])
_PREMIER_LANCEMENT_LANGUE = "lang" not in _cfg_langue_initiale

# ======================= SIDEBAR =======================
sidebar_frame = ctk.CTkFrame(root, width=200, fg_color=BF_COLOR_SIDEBAR, corner_radius=0)
sidebar_frame.pack(side="left", fill="y")
sidebar_frame.pack_propagate(False)

# Liseré cinabre (2px) qui suit l'item de navigation actif — voir activer_bouton_sidebar
_sidebar_nav_accent = ctk.CTkFrame(sidebar_frame, width=2, fg_color="#E63946", corner_radius=0)

# Espacement haut
ctk.CTkLabel(sidebar_frame, text="", height=22).pack()

# ── Wordmark "BeFree." (serif + point cinabre) ──
logo_frame = ctk.CTkFrame(sidebar_frame, fg_color="transparent")
logo_frame.pack(pady=(0, 20))

# Chemin relatif : fonctionne sur n'importe quelle machine
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
chemin_logo = os.path.join(BASE_DIR, "image", "Cage Oiseau.png")

if not os.path.exists(chemin_logo):
    raise FileNotFoundError(
        f"Impossible de trouver le logo à l'emplacement : {chemin_logo}"
    )

pil_logo = Image.open(chemin_logo)
ctk_logo = ctk.CTkImage(pil_logo, size=(48, 48))
_logo_ctk_image = ctk_logo  # garder la référence pour éviter le GC
lbl_logo = ctk.CTkLabel(logo_frame, image=ctk_logo, text="")
lbl_logo.pack(pady=(0, 4))

_wordmark_row = ctk.CTkFrame(logo_frame, fg_color="transparent")
_wordmark_row.pack()
ctk.CTkLabel(_wordmark_row, text="BeFree", font=theme_sumi.serif(22),
             text_color="#E8DFCE").pack(side="left")
ctk.CTkLabel(_wordmark_row, text=".", font=theme_sumi.serif(22),
             text_color="#E63946").pack(side="left")

ctk.CTkLabel(logo_frame, text=t("sidebar.version"), font=theme_sumi.mono(8),
             text_color="#8A8071").pack(pady=(2, 0))

# ── Sections de navigation (icônes trait fin dessinées au runtime, fidèle au design) ──
_sidebar_boutons = {}
_NAV_ICONS = theme_sumi.build_nav_icons()


def _sidebar_section_label(texte):
    ctk.CTkLabel(sidebar_frame, text=texte, font=theme_sumi.mono(9),
                 text_color="#8A8071", anchor="w"
                 ).pack(fill="x", padx=20, pady=(14, 6))


def _sidebar_nav_item(page_id, libelle):
    btn = ctk.CTkButton(
        sidebar_frame,
        text=libelle,
        font=theme_sumi.ui(13),
        anchor="w",
        image=_NAV_ICONS[page_id]["rest"],
        compound="left",
        fg_color="transparent",
        text_color=BF_COLOR_SIDEBAR_T,
        hover_color=BF_COLOR_SIDEBAR_H,
        corner_radius=2,
        height=32,
        command=lambda p=page_id: naviguer_sidebar(p),
    )
    btn.pack(fill="x", padx=8, pady=1)
    _sidebar_boutons[page_id] = btn
    return btn


_sidebar_section_label(t("sidebar.section_session"))
_sidebar_nav_item("accueil", t("sidebar.nav_accueil"))
_sidebar_nav_item("demarrer", t("sidebar.nav_demarrer"))
_sidebar_nav_item("stats", t("sidebar.nav_stats"))

_sidebar_section_label(t("sidebar.section_regles"))
_sidebar_nav_item("apps", t("sidebar.nav_apps"))
_sidebar_nav_item("sites", t("sidebar.nav_sites"))
_sidebar_nav_item("parametres", t("sidebar.nav_parametres"))

# Pousse la carte grade + carte utilisateur tout en bas (mockup : le badge
# de grade est le tout dernier élément de la sidebar)
ctk.CTkLabel(sidebar_frame, text="", font=("Segoe UI", 1)).pack(side="top", fill="both", expand=True)

# ── Badge Grade — tout en bas, ancré côté "bottom" en premier ──
sidebar_grade_card = ctk.CTkFrame(sidebar_frame, fg_color="#1F1B18", corner_radius=3,
                                   border_width=1, border_color="#2A2622")
sidebar_grade_card.pack(fill="x", padx=10, pady=(0, 8), side="bottom")
sidebar_grade_card.pack_propagate(False)
sidebar_grade_card.configure(height=56)

# Sceau (hanko) circulaire avec kanji
sidebar_grade_seal = ctk.CTkFrame(sidebar_grade_card, width=30, height=30,
                                   fg_color="#E63946", corner_radius=15)
sidebar_grade_seal.place(x=10, y=10)
sidebar_grade_seal.pack_propagate(False)
lbl_sidebar_grade_icon = ctk.CTkLabel(
    sidebar_grade_seal, text="禅",
    font=theme_sumi.serif(14), text_color="#0A0908",
)
lbl_sidebar_grade_icon.place(relx=0.5, rely=0.5, anchor="center")

# Nom du grade (serif)
lbl_sidebar_grade_nom = ctk.CTkLabel(
    sidebar_grade_card, text=t("grade.novice"),
    font=theme_sumi.serif(15), text_color="#E8DFCE",
    anchor="w",
)
lbl_sidebar_grade_nom.place(x=48, y=8)

# Rang / points (mono) — écrasé immédiatement par update_grade_accueil() au démarrage
lbl_sidebar_grade_pts = ctk.CTkLabel(
    sidebar_grade_card, text="",
    font=theme_sumi.mono(8), text_color="#8A8071",
    anchor="w",
)
lbl_sidebar_grade_pts.place(x=48, y=28)

# ── Rendre la carte grade cliquable ──
_popup_grade_ouvert = False

def _grade_card_click(event=None):
    global _popup_grade_ouvert
    if _popup_grade_ouvert:
        return "break"
    _popup_grade_ouvert = True
    ouvrir_popup_grade()
    def _reset():
        global _popup_grade_ouvert
        _popup_grade_ouvert = False
    root.after(400, _reset)
    return "break"  # stoppe la propagation de l'event

for _w in (sidebar_grade_card, sidebar_grade_seal, lbl_sidebar_grade_nom,
           lbl_sidebar_grade_pts, lbl_sidebar_grade_icon):
    _w.bind("<Button-1>", _grade_card_click)
    _w.configure(cursor="hand2")

# ======================= CONTENT =======================
content_frame = ctk.CTkFrame(root, fg_color="transparent")
content_frame.pack(side="right", fill="both", expand=True)

# ======================= ÉCRAN 1 — ACCUEIL (dashboard, fidèle au mockup) ==
ecran_accueil = ctk.CTkFrame(content_frame, fg_color="transparent")

# ── Zone de contenu, calée en haut-gauche comme le mockup (padding 36/44) ──
conteneur_accueil = ctk.CTkFrame(ecran_accueil, fg_color="transparent")
conteneur_accueil.pack(fill="both", expand=True, padx=44, pady=(32, 20))

_accueil_top_row = ctk.CTkFrame(conteneur_accueil, fg_color="transparent")
_accueil_top_row.pack(fill="x")

lbl_accueil_date = ctk.CTkLabel(_accueil_top_row, text="",
                                 font=theme_sumi.mono(10), text_color="#8A8071",
                                 anchor="w")
lbl_accueil_date.pack(side="left")

_accueil_fondateur_chip = ctk.CTkFrame(_accueil_top_row, fg_color="transparent",
                                        cursor="hand2")
_accueil_fondateur_chip.pack(side="right")
ctk.CTkLabel(_accueil_fondateur_chip, text=t("accueil.fondateur_label"),
             font=theme_sumi.mono(9), text_color="#8A8071").pack(side="left", padx=(0, 6))
lbl_accueil_fondateur_code = ctk.CTkLabel(_accueil_fondateur_chip, text="",
                                           font=theme_sumi.mono(10, "bold"),
                                           text_color="#E63946")
lbl_accueil_fondateur_code.pack(side="left")


def _copier_code_fondateur_accueil(event=None):
    code = charger_config().get("founder_code", "")
    if not code:
        return
    root.clipboard_clear()
    root.clipboard_append(code)
    lbl_accueil_fondateur_code.configure(text=t("fondateur.copie"))
    root.after(1200, lambda: lbl_accueil_fondateur_code.configure(text=code))


_accueil_fondateur_chip.bind("<Button-1>", _copier_code_fondateur_accueil)
for _w in _accueil_fondateur_chip.winfo_children():
    _w.bind("<Button-1>", _copier_code_fondateur_accueil)

lbl_accueil_bonjour = ctk.CTkLabel(conteneur_accueil, text=t("accueil.bonjour"),
                                    font=theme_sumi.serif(40, weight="semibold"),
                                    text_color="#E8DFCE", anchor="w")
lbl_accueil_bonjour.pack(fill="x", pady=(6, 0))

lbl_accueil_sous_titre = ctk.CTkLabel(conteneur_accueil, text="",
                                       font=theme_sumi.ui(14), text_color="#B8AF9E",
                                       anchor="w")
lbl_accueil_sous_titre.pack(fill="x", pady=(4, 0))

# ── Eyebrow de section unique (fidèle au mockup : un seul "CETTE SEMAINE"
# au-dessus de la grille, pas un label répété par carte) ──
ctk.CTkLabel(conteneur_accueil, text=t("accueil.eyebrow_semaine"), font=theme_sumi.mono(9),
             text_color="#8A8071", anchor="w"
             ).pack(fill="x", pady=(26, 0))

# ── Grille de 3 statistiques — colonnes 1.6fr / 1fr / 1fr (carte "Temps
# focus" élargie, comme le mockup), bordures 1px = grille, cellules = surface ──
_accueil_grid_bg = ctk.CTkFrame(conteneur_accueil, fg_color="#2A2622", corner_radius=0)
_accueil_grid_bg.pack(fill="x", pady=(8, 0))

_ACCUEIL_CARTES = (
    # (clé, titre, taille_police_valeur, largeur_relative)
    ("semaine", t("accueil.carte_temps_focus"), 40, 16),
    ("sessions", t("accueil.carte_sessions"), 32, 10),
    ("serie", t("accueil.carte_serie"), 32, 10),
)

_accueil_stat_cells = {}
for _i, (_key, _titre, _taille, _poids) in enumerate(_ACCUEIL_CARTES):
    _cell = ctk.CTkFrame(_accueil_grid_bg, fg_color="#141210", corner_radius=0)
    _cell.grid(row=0, column=_i, sticky="nsew", padx=(1 if _i > 0 else 0, 0), pady=0)
    _accueil_grid_bg.grid_columnconfigure(_i, weight=_poids)
    ctk.CTkLabel(_cell, text=_titre, font=theme_sumi.mono(10), text_color="#8A8071",
                 anchor="w").pack(fill="x", padx=18, pady=(16, 0))
    _lbl_valeur = ctk.CTkLabel(_cell, text="—", font=theme_sumi.mono(_taille),
                                text_color="#E8DFCE", anchor="w")
    _lbl_valeur.pack(fill="x", padx=18, pady=(4, 0))
    _lbl_legende = ctk.CTkLabel(_cell, text="", font=theme_sumi.ui(11),
                                 text_color="#B8AF9E", anchor="w")
    _lbl_legende.pack(fill="x", padx=18, pady=(0, 16))
    _accueil_stat_cells[_key] = {"cellule": _cell, "valeur": _lbl_valeur, "legende": _lbl_legende}

# ── Boutons d'action ──
_accueil_actions = ctk.CTkFrame(conteneur_accueil, fg_color="transparent")
_accueil_actions.pack(fill="x", pady=(26, 0))

btn_preparer = ctk.CTkButton(
    _accueil_actions,
    text=t("accueil.btn_demarrer"),
    font=theme_sumi.ui(14, "bold"),
    fg_color="#E8DFCE",
    hover_color="#D8CFC0",
    text_color="#0A0908",
    corner_radius=3,
    height=46,
    command=lambda: (_ts_reset_mode(), slide_vers(ecran_type_mode, ecran_accueil)),
)
btn_preparer.pack(side="left")

btn_reprendre = ctk.CTkButton(
    _accueil_actions,
    text=t("accueil.btn_reprendre"),
    font=theme_sumi.ui(13),
    fg_color="transparent",
    hover_color="#1F1B18",
    text_color="#E8DFCE",
    border_width=1, border_color="#E8DFCE",
    corner_radius=3,
    height=46,
    command=lambda: ouvrir_contrat(),
)
btn_reprendre.pack(side="left", padx=(14, 0))

# ── Dernières sessions ──
_accueil_recent_header = ctk.CTkFrame(conteneur_accueil, fg_color="transparent")
_accueil_recent_header.pack(fill="x", pady=(34, 10))
ctk.CTkLabel(_accueil_recent_header, text=t("accueil.dernieres_sessions"),
             font=theme_sumi.serif(18), text_color="#E8DFCE").pack(side="left")
_lbl_accueil_voir_tout = ctk.CTkLabel(_accueil_recent_header, text=t("accueil.voir_tout"),
                                       font=theme_sumi.mono(10), text_color="#8A8071",
                                       cursor="hand2")
_lbl_accueil_voir_tout.pack(side="right")
_lbl_accueil_voir_tout.bind("<Button-1>", lambda e: naviguer_sidebar("stats"))

ctk.CTkFrame(conteneur_accueil, height=1, fg_color="#2A2622").pack(fill="x")

_accueil_recent_list = ctk.CTkFrame(conteneur_accueil, fg_color="transparent")
_accueil_recent_list.pack(fill="x")


def _accueil_jour_label(dt):
    """Formate le libellé de date façon mockup : AUJOURD'HUI / HIER / LUN · 09:30."""
    aujourdhui = datetime.now().date()
    delta = (aujourdhui - dt.date()).days
    heure = dt.strftime("%H:%M")
    if delta == 0:
        return t("accueil.jour_aujourdhui", heure=heure)
    if delta == 1:
        return t("accueil.jour_hier", heure=heure)
    return f"{i18n.weekday_names()[dt.weekday()][:3].upper()} · {heure}"


def rafraichir_accueil():
    """Recalcule la date, le message, les 3 stats et la liste des dernières
    sessions à partir des vraies données (stats_manager + auth)."""
    now = datetime.now()
    lbl_accueil_date.configure(
        text=f"{i18n.weekday_names()[now.weekday()].upper()} · {now.day} {i18n.month_names()[now.month - 1].upper()} · {now.strftime('%H:%M')}")
    lbl_accueil_fondateur_code.configure(text=charger_config().get("founder_code", "—"))

    prenom = _nom_utilisateur_local()
    lbl_accueil_bonjour.configure(text=t("accueil.bonjour_prenom", prenom=prenom) if prenom else t("accueil.bonjour"))

    from stats_manager import charger_sessions
    toutes = charger_sessions()

    data_semaine = stats_manager.get_data_semaine()
    heures_semaine = formater_duree(data_semaine["temps_total"])
    nb_sessions_semaine = len({
        datetime.fromisoformat(s["timestamp"]).date()
        for s in toutes
        if datetime.fromisoformat(s["timestamp"]) >= now - timedelta(days=now.weekday())
    }) if toutes else 0
    nb_sessions_reel = sum(
        1 for s in toutes
        if not s.get("abandon")
        and datetime.fromisoformat(s["timestamp"]).date() >= (now - timedelta(days=now.weekday())).date()
    )
    serie = stats_manager.get_winstreak()

    if nb_sessions_reel == 0:
        sous_titre = t("accueil.sous_titre_zero")
    elif nb_sessions_reel == 1:
        sous_titre = t("accueil.sous_titre_une")
    else:
        sous_titre = t("accueil.sous_titre_plusieurs", n=nb_sessions_reel)
    lbl_accueil_sous_titre.configure(text=sous_titre)

    _accueil_stat_cells["semaine"]["valeur"].configure(text=heures_semaine)
    _accueil_stat_cells["semaine"]["legende"].configure(text=t("accueil.legende_heures"))
    _accueil_stat_cells["sessions"]["valeur"].configure(text=f"{nb_sessions_reel:02d}")
    _accueil_stat_cells["sessions"]["legende"].configure(text=t("accueil.legende_sessions"))
    _accueil_stat_cells["serie"]["valeur"].configure(text=f"{serie:02d}", text_color="#E63946")
    _accueil_stat_cells["serie"]["legende"].configure(text=t("accueil.legende_serie"))

    for w in _accueil_recent_list.winfo_children():
        w.destroy()

    dernieres = sorted(toutes, key=lambda s: s.get("timestamp", ""), reverse=True)[:4]
    if not dernieres:
        ctk.CTkLabel(_accueil_recent_list, text=t("accueil.aucune_session"),
                     font=theme_sumi.ui(12), text_color="#8A8071",
                     anchor="w").pack(fill="x", pady=14)
        return

    for s in dernieres:
        try:
            dt = datetime.fromisoformat(s["timestamp"])
        except Exception:
            continue
        objectif = (s.get("objectif") or "").strip() or t("accueil.objectif_defaut")
        duree = formater_duree(s.get("duree_minutes", 0))
        hardcore = bool(s.get("hardcore"))
        abandon = bool(s.get("abandon"))

        if abandon:
            statut_texte, statut_couleur = t("accueil.statut_abandon"), "#D4A24C"
        elif hardcore:
            statut_texte, statut_couleur = t("accueil.statut_hardcore"), "#E63946"
        else:
            statut_texte, statut_couleur = t("accueil.statut_terminee"), "#7A9B5C"

        row = ctk.CTkFrame(_accueil_recent_list, fg_color="transparent")
        row.pack(fill="x")
        ctk.CTkFrame(row, height=1, fg_color="#2A2622").pack(fill="x", side="bottom")

        ctk.CTkLabel(row, text=_accueil_jour_label(dt), font=theme_sumi.mono(11),
                     text_color="#8A8071", width=110, anchor="w").pack(side="left", pady=14)
        ctk.CTkLabel(row, text=objectif, font=theme_sumi.ui(13), text_color="#E8DFCE",
                     anchor="w").pack(side="left", fill="x", expand=True, pady=14)
        ctk.CTkLabel(row, text=duree, font=theme_sumi.mono(11),
                     text_color="#E8DFCE", anchor="e").pack(side="left", padx=(10, 16), pady=14)
        ctk.CTkLabel(row, text=statut_texte,
                     font=theme_sumi.mono(10),
                     text_color=statut_couleur,
                     anchor="e").pack(side="left", pady=14)

# ======================= ÉCRAN 2 — STATS =======================
ecran_stats = ctk.CTkFrame(content_frame, fg_color="transparent")

stats_manager = StatsManager()
stats_dashboard = StatsDashboard(ecran_stats, stats_manager,
                                  on_export=lambda: exporter_statistiques())

# =====================================================================
#                FONCTIONS : RÉINITIALISATION + EXPORT
# =====================================================================

def reinitialiser_donnees():
    """Vide stats.json et rafraîchit les graphiques."""
    reponse = messagebox.askyesno(
        title=t("stats.confirm_titre"),
        message=t("stats.confirm_reset"),
        icon="warning",
        parent=root,
    )
    if not reponse:
        return

    try:
        with open(STATS_FILE, "w") as f:
            json.dump([], f, indent=2)
    except Exception:
        messagebox.showerror(t("stats.erreur_titre"),
                             t("stats.erreur_ecriture"),
                             parent=root)
        return

    stats_dashboard.update_dashboard(stats_dashboard.filter_active)

    messagebox.showinfo(
        t("stats.succes_titre"),
        t("stats.succes_reset"),
        parent=root,
    )


def exporter_statistiques():
    """Exporte stats.json ou un CSV via une boîte de dialogue Windows."""
    from stats_manager import charger_sessions

    sessions = charger_sessions()
    if not sessions:
        messagebox.showinfo(t("stats.export_titre"),
                            t("stats.export_aucune_donnee"),
                            parent=root)
        return

    chemin = filedialog.asksaveasfilename(
        title=t("stats.export_dialog_titre"),
        defaultextension=".json",
        filetypes=[
            (t("stats.fichier_json"), "*.json"),
            (t("stats.fichier_csv"), "*.csv"),
            (t("stats.fichier_tous"), "*.*"),
        ],
        initialfile="hardcore_focus_stats",
        parent=root,
    )
    if not chemin:
        return

    try:
        ext = os.path.splitext(chemin)[1].lower()
        if ext == ".csv":
            with open(chemin, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp", "duree_minutes", "app_name"])
                for s in sessions:
                    writer.writerow([
                        s.get("timestamp", ""),
                        s.get("duree_minutes", 0),
                        s.get("app_name", ""),
                    ])
        else:
            with open(chemin, "w", encoding="utf-8") as f:
                json.dump(sessions, f, indent=2, ensure_ascii=False)

        messagebox.showinfo(
            "Export réussi",
            f"Statistiques exportées vers :\n{chemin}",
            parent=root,
        )
    except Exception as e:
        messagebox.showerror(
            "Erreur d'export",
            f"Impossible d'exporter les données :\n{e}",
            parent=root,
        )


# =====================================================================
#                        ÉCRAN PARAMÈTRES
# =====================================================================

ecran_parametres = ctk.CTkFrame(content_frame, fg_color="transparent")

conteneur_param = ctk.CTkScrollableFrame(ecran_parametres, fg_color="transparent",
                                          scrollbar_button_color="#2A2622",
                                          scrollbar_button_hover_color="#3A352E")
conteneur_param.pack(fill="both", expand=True, padx=44, pady=(32, 20))

titre_param = ctk.CTkLabel(conteneur_param, text=t("parametres.titre"),
                            font=theme_sumi.serif(30), text_color=COLOR_TEXT, anchor="w")
titre_param.pack(fill="x")


def _param_section(texte):
    ctk.CTkFrame(conteneur_param, height=1, fg_color="#2A2622").pack(fill="x", pady=(26, 0))
    ctk.CTkLabel(conteneur_param, text=texte, font=theme_sumi.mono(10),
                 text_color="#8A8071", anchor="w").pack(fill="x", pady=(8, 6))


def _param_row(titre, description=None):
    """Ligne à plat façon mockup : libellé+description à gauche, bordure basse,
    et retourne le frame de droite où placer le contrôle (bouton/switch)."""
    row = ctk.CTkFrame(conteneur_param, fg_color="transparent")
    row.pack(fill="x")
    ctk.CTkFrame(conteneur_param, height=1, fg_color="#2A2622").pack(fill="x")

    gauche = ctk.CTkFrame(row, fg_color="transparent")
    gauche.pack(side="left", fill="x", expand=True, pady=14)
    ctk.CTkLabel(gauche, text=titre, font=("Segoe UI", 13, "bold"),
                 text_color=COLOR_TEXT, anchor="w").pack(fill="x", anchor="w")
    if description:
        ctk.CTkLabel(gauche, text=description, font=("Segoe UI", 11),
                     text_color=COLOR_TEXT_DIM, anchor="w", justify="left",
                     wraplength=440).pack(fill="x", anchor="w", pady=(2, 0))

    droite = ctk.CTkFrame(row, fg_color="transparent")
    droite.pack(side="right", pady=14)
    return droite


# ═══════════════════════ COMPTE FONDATEUR ═══════════════════════
_param_section(t("parametres.section_fondateur"))

_ctrl_fondateur = _param_row(t("parametres.fondateur_titre"), t("parametres.fondateur_desc"))


def _rafraichir_code_fondateur_param():
    for _w in _ctrl_fondateur.winfo_children():
        _w.destroy()

    code = charger_config().get("founder_code")
    if not code:
        ctk.CTkButton(
            _ctrl_fondateur, text=t("parametres.fondateur_connexion"),
            font=("Segoe UI", 12, "bold"), height=34, corner_radius=3,
            fg_color="#E8DFCE", hover_color="#D8CFC0", text_color="#0A0908",
            border_width=0,
            command=lambda: _afficher_ecran_compte_fondateur(then_langue=False, depuis_parametres=True)
        ).pack(side="left")
        return

    lbl_code_fondateur_param = ctk.CTkLabel(
        _ctrl_fondateur, text=code, font=theme_sumi.mono(13, "bold"), text_color="#E63946")
    lbl_code_fondateur_param.pack(side="left", padx=(0, 10))

    def _copier_code_fondateur_param():
        root.clipboard_clear()
        root.clipboard_append(code)
        btn_copier_fondateur.configure(text=t("fondateur.copie"))
        root.after(1500, lambda: btn_copier_fondateur.configure(text=t("fondateur.copier")))

    btn_copier_fondateur = ctk.CTkButton(
        _ctrl_fondateur, text=t("fondateur.copier"),
        font=("Segoe UI", 12, "bold"), height=34, corner_radius=3,
        fg_color="transparent", hover_color="#1F1B18",
        border_width=1, border_color="#E8DFCE", text_color="#E8DFCE",
        command=_copier_code_fondateur_param)
    btn_copier_fondateur.pack(side="left")


_rafraichir_code_fondateur_param()


# ═══════════════════════ LANGUE ═══════════════════════
_param_section(t("parametres.section_langue"))

_ctrl_langue = _param_row(t("parametres.langue_titre"), t("parametres.langue_desc"))
frame_langue_btns = ctk.CTkFrame(_ctrl_langue, fg_color="transparent")
frame_langue_btns.pack()


def _changer_langue(code):
    cfg = charger_config()
    cfg["lang"] = code
    sauvegarder_config(cfg)
    messagebox.showinfo(t("parametres.langue_titre"), t("parametres.langue_redemarrer"))


for _code, _label in i18n.LANGUAGES:
    ctk.CTkButton(
        frame_langue_btns, text=_label,
        font=("Segoe UI", 12, "bold"), height=34, corner_radius=3,
        fg_color="#1F1B18" if _code != i18n.get_language() else "#E8DFCE",
        hover_color="#28231F",
        text_color="#8A8071" if _code != i18n.get_language() else "#0A0908",
        border_width=0,
        command=lambda c=_code: _changer_langue(c)
    ).pack(side="left", padx=(0, 6))


# ═══════════════════════ COMPTE ═══════════════════════
_param_section(t("parametres.section_compte"))

_ctrl_mdp = _param_row(t("parametres.mdp_titre"), t("parametres.mdp_desc"))
lbl_mdp_statut = ctk.CTkLabel(_ctrl_mdp, text="",
                                font=("Segoe UI", 11, "bold"), text_color=COLOR_TEXT_DIM)
lbl_mdp_statut.pack(anchor="e", pady=(0, 4))

frame_mdp_btns = ctk.CTkFrame(_ctrl_mdp, fg_color="transparent")
frame_mdp_btns.pack()

btn_definir_mdp = ctk.CTkButton(
    frame_mdp_btns, text=t("parametres.mdp_definir"),
    font=("Segoe UI", 12, "bold"), height=34, corner_radius=3,
    fg_color="transparent", hover_color="#1F1B18",
    border_width=1, border_color="#E8DFCE", text_color="#E8DFCE",
    command=action_definir_mdp)

btn_changer_mdp = ctk.CTkButton(
    frame_mdp_btns, text=t("parametres.btn_changer"),
    font=("Segoe UI", 12, "bold"), height=34, corner_radius=3, width=110,
    fg_color="transparent", hover_color="#1F1B18",
    border_width=1, border_color="#E8DFCE", text_color="#E8DFCE",
    command=action_changer_mdp)

btn_supprimer_mdp = ctk.CTkButton(
    frame_mdp_btns, text=t("parametres.btn_supprimer"),
    font=("Segoe UI", 12, "bold"), height=34, corner_radius=3, width=100,
    fg_color="#241012", hover_color="#A82230",
    border_width=1, border_color="#A82230",
    command=action_supprimer_mdp)

_refresh_mdp_section()

# ═══════════════════════ APPLICATION ═══════════════════════
_param_section(t("parametres.section_application"))

_ctrl_demarrage = _param_row(t("parametres.demarrage_titre"),
                              t("parametres.demarrage_desc"))
var_demarrage_auto = ctk.BooleanVar(value=fichier_demarrage_existe())

def on_switch_demarrage():
    basculer_demarrage_auto(var_demarrage_auto.get())

switch_demarrage = ctk.CTkSwitch(_ctrl_demarrage, text="",
                                   variable=var_demarrage_auto,
                                   progress_color="#E63946",
                                   command=on_switch_demarrage)
switch_demarrage.pack()

_ctrl_mz = _param_row(t("parametres.mz_titre"), t("parametres.mz_desc"))
var_mz = ctk.BooleanVar(value=morning_zero_est_actif())

def on_switch_mz():
    cfg = charger_config()
    cfg["morning_zero_actif"] = var_mz.get()
    sauvegarder_config(cfg)

switch_mz = ctk.CTkSwitch(_ctrl_mz, text="",
                           variable=var_mz,
                           progress_color="#E63946",
                           command=on_switch_mz)
switch_mz.pack()

_ctrl_pl = _param_row(t("parametres.usb_titre"), t("parametres.usb_desc"))
lbl_usb_statut = ctk.CTkLabel(_ctrl_pl, text=t("parametres.usb_aucune_cle"),
                                font=theme_sumi.mono(10), text_color="#8A8071")
lbl_usb_statut.pack(anchor="e", pady=(0, 4))

frame_pl_btns = ctk.CTkFrame(_ctrl_pl, fg_color="transparent")
frame_pl_btns.pack(anchor="e", pady=(0, 6))

btn_enregistrer_cle = ctk.CTkButton(
    frame_pl_btns, text=t("parametres.usb_enregistrer"),
    font=("Segoe UI", 12, "bold"), height=34, corner_radius=3,
    fg_color="transparent", hover_color="#1F1B18",
    border_width=1, border_color="#E8DFCE", text_color="#E8DFCE",
    command=ouvrir_dialog_enregistrer_cle)
btn_enregistrer_cle.pack(side="left", padx=4)

btn_supprimer_cle = ctk.CTkButton(
    frame_pl_btns, text=t("parametres.btn_supprimer"),
    font=("Segoe UI", 12, "bold"), height=34, corner_radius=3, width=100,
    fg_color="#241012", hover_color="#A82230",
    border_width=1, border_color="#A82230",
    command=action_supprimer_cle)
# affiché seulement si clé enregistrée (via _refresh_physical_lock_section)

var_physical_lock = ctk.BooleanVar(value=physical_lock_actif())

def on_switch_pl():
    if var_physical_lock.get() and not cle_usb_enregistree():
        var_physical_lock.set(False)
        lbl_usb_statut.configure(
            text=t("parametres.usb_enregistre_dabord"), text_color="#B3822F")
        return
    cfg = charger_config()
    cfg["physical_lock_actif"] = var_physical_lock.get()
    sauvegarder_config(cfg)

switch_pl = ctk.CTkSwitch(_ctrl_pl, text=t("parametres.usb_activer"),
                           font=("Segoe UI", 11), text_color=COLOR_TEXT_DIM,
                           variable=var_physical_lock,
                           progress_color="#E63946",
                           command=on_switch_pl)
switch_pl.pack(anchor="e")

_refresh_physical_lock_section()

_ctrl_resume = _param_row(t("parametres.resume_titre"), t("parametres.resume_desc"))
ctk.CTkLabel(_ctrl_resume, text=t("parametres.resume_valeur"), font=theme_sumi.mono(10),
             text_color="#7A9B5C").pack()

# ═══════════════════════ DONNÉES ═══════════════════════
_param_section(t("parametres.section_donnees"))

_ctrl_export = _param_row(t("parametres.export_titre"), t("parametres.export_desc"))
btn_exporter = ctk.CTkButton(
    _ctrl_export, text=t("parametres.btn_exporter"),
    font=("Segoe UI", 12, "bold"),
    fg_color="transparent", hover_color="#1F1B18",
    border_width=1, border_color="#E8DFCE", text_color="#E8DFCE",
    corner_radius=3, height=34, width=120,
    command=exporter_statistiques,
)
btn_exporter.pack()

_ctrl_reset = _param_row(t("parametres.reset_titre"), t("parametres.reset_desc"))
btn_reinitialiser = ctk.CTkButton(
    _ctrl_reset, text=t("parametres.btn_reinitialiser"),
    font=("Segoe UI", 12, "bold"),
    fg_color="transparent", hover_color="#241012",
    border_width=1, border_color="#E63946", text_color="#E63946",
    corner_radius=3, height=34, width=120,
    command=reinitialiser_donnees,
)
btn_reinitialiser.pack()

# Version info
v_info = ctk.CTkLabel(conteneur_param, text=t("parametres.version_footer"),
                        font=theme_sumi.mono(9), text_color="#5C574C")
v_info.pack(pady=(24, 0))

# ======================= ÉCRAN — TEMPS =======================
ecran_temps = ctk.CTkFrame(content_frame, fg_color="transparent")

conteneur_temps = ctk.CTkFrame(ecran_temps, fg_color="transparent")
conteneur_temps.place(relx=0.5, rely=0.45, anchor="center")

titre_temps = ctk.CTkLabel(conteneur_temps, text="Durée de la session",
                             font=theme_sumi.serif(30), text_color=COLOR_TEXT)
titre_temps.pack(pady=(0, 10))

sous_titre_temps = ctk.CTkLabel(conteneur_temps, text="Choisis la durée",
                                  font=("Segoe UI", 12), text_color="#8A8071")
sous_titre_temps.pack(pady=(0, 15))

# Switch Mode Infini
switch_infini = ctk.CTkSwitch(conteneur_temps, text="\u267E\uFE0F Mode Infini",
                                font=("Segoe UI", 12),
                                progress_color=BF_COLOR_ACCENT_CRIMSON,
                                command=basculer_mode_infini)
switch_infini.pack(pady=(0, 20))

# Entrées Heures / Minutes
frame_entrees = ctk.CTkFrame(conteneur_temps, fg_color="transparent")
frame_entrees.pack()

# Heures
frame_heures = ctk.CTkFrame(frame_entrees, fg_color="transparent")
frame_heures.pack(side="left", padx=15)

label_heures = ctk.CTkLabel(frame_heures, text="Heures", font=("Segoe UI", 11, "bold"),
                              text_color=COLOR_TEXT_DIM)
label_heures.pack()

entry_heures = ctk.CTkEntry(frame_heures, width=100, justify="center", font=("Segoe UI", 20),
                              fg_color="#141210", border_color="#2A2622",
                              text_color=COLOR_TEXT)
entry_heures.insert(0, "0")
entry_heures.pack(pady=(3, 0))

# Minutes
frame_minutes = ctk.CTkFrame(frame_entrees, fg_color="transparent")
frame_minutes.pack(side="left", padx=15)

label_minutes = ctk.CTkLabel(frame_minutes, text="Minutes", font=("Segoe UI", 11, "bold"),
                               text_color=COLOR_TEXT_DIM)
label_minutes.pack()

entry_minutes = ctk.CTkEntry(frame_minutes, width=100, justify="center", font=("Segoe UI", 20),
                               fg_color="#141210", border_color="#2A2622",
                               text_color=COLOR_TEXT)
entry_minutes.insert(0, "25")
entry_minutes.pack(pady=(3, 0))

# Boutons navigation
frame_boutons_temps = ctk.CTkFrame(conteneur_temps, fg_color="transparent")
frame_boutons_temps.pack(pady=(40, 0))

btn_retour_accueil = ctk.CTkButton(frame_boutons_temps, text="\u2190 Retour", width=120,
                                     font=("Segoe UI", 12),
                                     fg_color="#141210",
                                     hover_color="#28231F",
                                     text_color=COLOR_TEXT_DIM, corner_radius=3,
                                     command=lambda: montrer_ecran(ecran_accueil))
btn_retour_accueil.pack(side="left", padx=10)

btn_suivant_temps = ctk.CTkButton(frame_boutons_temps, text="Suivant \u2192", width=160,
                                    font=("Segoe UI", 13, "bold"), corner_radius=3,
                                    fg_color="#A82230",
                                    hover_color="#A82230",
                                    command=valider_temps)
btn_suivant_temps.pack(side="left", padx=10)

# Bouton Suivant pour mode infini (caché par défaut)
btn_suivant_infini = ctk.CTkButton(frame_boutons_temps, text="Suivant \u2192", width=160,
                                     font=("Segoe UI", 13, "bold"), corner_radius=3,
                                     fg_color="#A82230",
                                     hover_color="#A82230",
                                     command=valider_temps)

# ======================= ÉCRAN — APPLICATIONS =======================
ecran_apps = ctk.CTkFrame(content_frame, fg_color="transparent")

conteneur_apps_titre = ctk.CTkFrame(ecran_apps, fg_color="transparent")
conteneur_apps_titre.pack(pady=(25, 5))

titre_apps = ctk.CTkLabel(conteneur_apps_titre, text=t("whitelist_legacy.titre"),
                            font=theme_sumi.serif(28), text_color=COLOR_TEXT)
titre_apps.pack()

sous_titre_apps = ctk.CTkLabel(conteneur_apps_titre,
                                 text=t("whitelist_legacy.sous_titre"),
                                 font=("Segoe UI", 11), text_color=COLOR_TEXT_DIM)
sous_titre_apps.pack(pady=(0, 5))

# Barre de recherche
entry_recherche = ctk.CTkEntry(ecran_apps, placeholder_text=t("whitelist_legacy.recherche_placeholder"),
                                 font=("Segoe UI", 12),
                                 fg_color="#141210", border_color="#2A2622",
                                 text_color=COLOR_TEXT,
                                 placeholder_text_color=COLOR_TEXT_MUTED)
entry_recherche.pack(fill="x", padx=60, pady=(0, 8))
entry_recherche.bind("<KeyRelease>", filtrer_applications)

# Scrollable frame pour les apps
scroll_apps = ctk.CTkScrollableFrame(ecran_apps, corner_radius=3,
                                       fg_color="#141210",
                                       border_color="#2A2622",
                                       border_width=1)
scroll_apps.pack(fill="both", expand=True, padx=60, pady=(0, 5))

# Barre d'infos et boutons en bas
frame_bas_apps = ctk.CTkFrame(ecran_apps, fg_color="transparent")
frame_bas_apps.pack(pady=(5, 20))

btn_retour_temps = ctk.CTkButton(frame_bas_apps, text=t("common.retour"), width=120,
                                   font=("Segoe UI", 12), corner_radius=3,
                                   fg_color="#141210",
                                   hover_color="#28231F",
                                   text_color=COLOR_TEXT_DIM,
                                   command=lambda: montrer_ecran(ecran_temps))
btn_retour_temps.pack(side="left", padx=10)

btn_demarrer_focus = ctk.CTkButton(frame_bas_apps, text=t("whitelist_legacy.btn_demarrer"), width=280,
                                     font=("Segoe UI", 14, "bold"), corner_radius=3,
                                     fg_color="#A82230",
                                     hover_color="#A82230",
                                     command=ouvrir_contrat)
btn_demarrer_focus.pack(side="left", padx=10)

def _creer_ligne_app(nom, ctkim=None):
    """Construit une ligne cochable façon mockup : icône, nom, tag AUTORISÉE/—
    à droite, bordure basse, réagit en direct au clic de la case."""
    var = ctk.BooleanVar(value=False)
    checkbox_vars[nom] = var
    cles_apps[nom] = generer_cles_recherche(nom)

    ligne = ctk.CTkFrame(scroll_apps, fg_color="transparent")
    ligne.pack(fill="x", padx=4, pady=0)
    ctk.CTkFrame(scroll_apps, height=1, fg_color="#141210").pack(fill="x", padx=4)

    if ctkim:
        ctk.CTkLabel(ligne, image=ctkim, text="", width=32).pack(side="left", padx=(8, 10), pady=10)
    else:
        ctk.CTkLabel(ligne, text="  ", width=32).pack(side="left", padx=(8, 10), pady=10)

    lbl_tag = ctk.CTkLabel(ligne, text="—", font=theme_sumi.mono(10),
                            text_color="#8A8071", width=80, anchor="e")
    lbl_tag.pack(side="right", padx=(0, 4), pady=10)

    cb = ctk.CTkCheckBox(ligne, text="", variable=var, width=20,
                           corner_radius=2,
                           fg_color="#E63946",
                           hover_color="#A82230",
                           checkmark_color="#0A0908")
    cb.pack(side="right", padx=(5, 10), pady=10)

    lbl_nom = ctk.CTkLabel(ligne, text=nom, font=("Segoe UI", 12), anchor="w",
                            text_color=COLOR_TEXT)
    lbl_nom.pack(side="left", fill="x", expand=True, pady=10)

    def _sync_tag(*_):
        if var.get():
            lbl_tag.configure(text=t("whitelist_legacy.tag_autorisee"), text_color="#E63946")
            lbl_nom.configure(font=("Segoe UI", 12, "bold"))
        else:
            lbl_tag.configure(text="—", text_color="#8A8071")
            lbl_nom.configure(font=("Segoe UI", 12))
    var.trace_add("write", _sync_tag)


# --- REMPLIR LA LISTE DES APPLIS ---
apps_data = scanner_applications()
for data in apps_data:
    nom = data["nom"]
    if nom not in ctk_icones_cache:
        ctk_icones_cache[nom] = charger_icone_app(data["lnk_path"])
    _creer_ligne_app(nom, ctk_icones_cache[nom])

# --- AJOUTER LES APPLICATIONS DÉTECTÉES DYNAMIQUEMENT ---
for app_name in detected_apps:
    if app_name in checkbox_vars:
        continue
    _creer_ligne_app(app_name)
    cb.pack(side="right", padx=(5, 0))

rappel_apps = ctk.CTkLabel(scroll_apps, text=t("whitelist_legacy.rappel"),
                              font=("Segoe UI", 10), text_color="#5C574C")
rappel_apps.pack(pady=(6, 5))

# ======================= ÉCRAN — CONTRAT DE TRAVAIL =======================
# Feuillet de papier (washi) sur fond sumi, avec ombre portée — fidèle au mockup.
ecran_contrat = ctk.CTkFrame(content_frame, fg_color="#0A0908")

def _octagon_pts(cx, cy, r):
    """Retourne les points d'un octogone centré."""
    pts = []
    for i in range(8):
        a = math.pi / 8 + i * math.pi / 4
        pts += [cx + r * math.cos(a), cy + r * math.sin(a)]
    return pts


def _draw_cadenas(c, cx, cy, scale=1.0, shackle_offset=0):
    """Dessine un cadenas angulaire sur le canvas c.
    shackle_offset > 0 = anse relevée (ouverte), 0 = fermée.
    """
    lw = int(12 * scale)
    lh = int(19 * scale)
    sw = int(10 * scale)
    sh = int(15 * scale)

    # Corps (rectangle plein)
    c.create_rectangle(cx - lw, cy - 1, cx + lw, cy + lh,
                       fill="#E63946", outline="#A82230", width=max(1, int(scale)))
    # Anse U angulaire
    oy = int(shackle_offset)
    c.create_line(cx - sw, cy - 1, cx - sw, cy - 1 - sh - oy,
                  fill="#A82230", width=max(2, int(2 * scale)))
    c.create_line(cx + sw, cy - 1, cx + sw, cy - 1 - sh - oy,
                  fill="#A82230", width=max(2, int(2 * scale)))
    c.create_line(cx - sw, cy - 1 - sh - oy, cx + sw, cy - 1 - sh - oy,
                  fill="#A82230", width=max(2, int(2 * scale)))
    # Trou de serrure (diamant)
    kw = max(3, int(3 * scale))
    kh = max(6, int(7 * scale))
    c.create_polygon([cx, cy + 2,
                      cx + kw, cy + 2 + kw,
                      cx, cy + 2 + kw + kw,
                      cx - kw, cy + 2 + kw],
                     fill="#0A0908", outline="")
    c.create_rectangle(cx - kw + 1, cy + 2 + kw, cx + kw - 1, cy + kh + 2,
                       fill="#0A0908", outline="")


# ── Feuillet papier centré (avec ombre portée offset) ──
_contrat_holder = ctk.CTkFrame(ecran_contrat, fg_color="transparent",
                                width=700, height=620)
_contrat_holder.place(relx=0.5, rely=0.5, anchor="center")
_contrat_holder.pack_propagate(False)

# Ombre portée (décalée +20,+20 exact comme le box-shadow du mockup : 20px 20px 0 #141210)
_contrat_shadow = ctk.CTkFrame(_contrat_holder, fg_color="#141210", corner_radius=0,
                                width=640, height=584)
_contrat_shadow.place(x=20, y=20)

# Feuillet
_contrat_paper = ctk.CTkFrame(_contrat_holder, fg_color="#F2E8D3", corner_radius=0,
                               width=640, height=584, border_width=1, border_color="#2A2622")
_contrat_paper.place(x=0, y=0)
_contrat_paper.pack_propagate(False)

conteneur_contrat = ctk.CTkFrame(_contrat_paper, fg_color="transparent")
conteneur_contrat.pack(fill="both", expand=True, padx=40, pady=36)

# En-tête mono + titre serif
lbl_contrat_meta = ctk.CTkLabel(conteneur_contrat,
                                 text=t("contrat.meta", n=1),
                                 font=theme_sumi.mono(10), text_color="#8A8071",
                                 anchor="w")
lbl_contrat_meta.pack(fill="x")

ctk.CTkLabel(conteneur_contrat, text=t("contrat.titre"),
             font=theme_sumi.serif(40), text_color="#1A1613",
             anchor="w").pack(fill="x", pady=(4, 0))

lbl_contrat_intro = ctk.CTkLabel(
    conteneur_contrat,
    text=t("contrat.intro"),
    font=("Segoe UI", 13), text_color="#4A4239", anchor="w",
    justify="left", wraplength=560)
lbl_contrat_intro.pack(fill="x", pady=(10, 0))

# Objectif
ctk.CTkLabel(conteneur_contrat, text=t("contrat.objectif_label"),
             font=theme_sumi.mono(10), text_color="#8A8071",
             anchor="w").pack(fill="x", pady=(20, 6))
entry_objectif = ctk.CTkTextbox(
    conteneur_contrat, height=90,
    font=theme_sumi.serif(17),
    fg_color="#FBF8F1", border_color="#1A1613", border_width=1,
    text_color="#1A1613", corner_radius=0)
entry_objectif.pack(fill="x")

# Signature + sceau
_contrat_sign_row = ctk.CTkFrame(conteneur_contrat, fg_color="transparent")
_contrat_sign_row.pack(fill="x", pady=(20, 0))

_contrat_sign_left = ctk.CTkFrame(_contrat_sign_row, fg_color="transparent")
_contrat_sign_left.pack(side="left", fill="x", expand=True)
ctk.CTkLabel(_contrat_sign_left, text=t("contrat.signature_label"), font=theme_sumi.mono(10),
             text_color="#8A8071", anchor="w").pack(fill="x")
entry_signature = ctk.CTkEntry(
    _contrat_sign_left, height=42, width=240,
    font=theme_sumi.serif(20, italic=True),
    fg_color="#FBF8F1", border_color="#1A1613", border_width=1,
    text_color="#1A1613", placeholder_text=t("contrat.signature_placeholder"),
    placeholder_text_color="#8A8071", corner_radius=0)
entry_signature.pack(anchor="w", pady=(6, 0))
lbl_contrat_date = ctk.CTkLabel(_contrat_sign_left, text="",
                                 font=theme_sumi.mono(10), text_color="#8A8071",
                                 anchor="w")
lbl_contrat_date.pack(fill="x", pady=(6, 0))

# Sceau hanko (cercle rouge, kanji)
_contrat_seal = ctk.CTkFrame(_contrat_sign_row, width=88, height=88, corner_radius=44,
                              fg_color="#E63946")
_contrat_seal.pack(side="right", padx=(0, 10))
_contrat_seal.pack_propagate(False)
ctk.CTkLabel(_contrat_seal, text="禅", font=theme_sumi.serif(44),
             text_color="#F2E8D3").place(relx=0.5, rely=0.5, anchor="center")

# Message d'erreur
lbl_contrat_err = ctk.CTkLabel(conteneur_contrat, text="",
                                font=("Segoe UI", 11), text_color="#A82230",
                                anchor="w")
lbl_contrat_err.pack(fill="x", pady=(10, 0))

# Boutons
frame_contrat_btns = ctk.CTkFrame(conteneur_contrat, fg_color="transparent")
frame_contrat_btns.pack(fill="x", side="bottom")

ctk.CTkButton(frame_contrat_btns, text=t("common.retour"), width=120, height=40,
              font=theme_sumi.ui(12), corner_radius=0,
              fg_color="transparent", hover_color="#E3D6B8",
              border_width=1, border_color="#1A1613", text_color="#1A1613",
              command=lambda: (_ts_reset(), slide_vers(ecran_type_session, ecran_contrat))
              ).pack(side="left")

ctk.CTkButton(frame_contrat_btns, text=t("contrat.btn_engager"), width=200, height=44,
              font=theme_sumi.ui(14, "bold"), corner_radius=0,
              fg_color="#1A1613", hover_color="#0A0908", text_color="#F2E8D3",
              command=valider_contrat).pack(side="right")

# ======================= ÉCRAN — SESSION (fidèle au mockup : barre haut,
# zone centrale, barre bas) =======================
ecran_session = ctk.CTkFrame(content_frame, fg_color="#0A0908")

conteneur_session = ctk.CTkFrame(ecran_session, fg_color="transparent")
conteneur_session.pack(fill="both", expand=True, padx=60, pady=40)

# ── Zone centrale (expand, centrée) ──
_session_centre = ctk.CTkFrame(conteneur_session, fg_color="transparent")
_session_centre.pack(fill="both", expand=True)

label_objectif_session = ctk.CTkLabel(
    _session_centre, text="", font=theme_sumi.serif(20, italic=True),
    text_color="#B8AF9E", wraplength=640, justify="center")
label_objectif_session.pack(pady=(0, 8))

label_chrono = ctk.CTkLabel(_session_centre, text="--:--",
                              font=theme_sumi.mono(128), text_color=BF_COLOR_ACCENT_CRIMSON)
label_chrono.pack(pady=(28, 0))

label_statut = ctk.CTkLabel(_session_centre, text="", font=("Segoe UI", 13),
                              text_color=COLOR_TEXT_DIM)
label_statut.pack(pady=(8, 0))

# ── Barre du bas : actions ──
_session_barre_bas = ctk.CTkFrame(conteneur_session, fg_color="transparent")
_session_barre_bas.pack(fill="x", side="bottom")

_session_actions = ctk.CTkFrame(_session_barre_bas, fg_color="transparent")
_session_actions.pack(side="right")

btn_terminer_infini = ctk.CTkButton(_session_actions, text=t("session.terminer"),
                                      font=("Segoe UI", 14, "bold"), height=44,
                                      fg_color="#5C7A46", corner_radius=3,
                                      hover_color="#5C7A46",
                                      command=terminer_session_infini)

btn_abandonner = ctk.CTkButton(_session_actions, text=t("session.abandonner"), width=200,
                                 font=("Segoe UI", 12), corner_radius=3,
                                 fg_color="#A82230", hover_color="#A82230",
                                 command=ouvrir_tunnel_honte)
btn_abandonner.pack()


def _preparer_ecran_session():
    """Enregistre l'heure de début de session (lue par l'écran de violation
    Hardcore) — appelé une fois au lancement/reprise d'une session, avant
    montrer_ecran(ecran_session)."""
    session_cfg["heure_debut"] = datetime.now().strftime("%H:%M")

# =====================================================================
#           ÉCRAN TYPE DE SESSION — 4 régimes (fidèle au mockup)
# =====================================================================
ecran_type_mode = ctk.CTkFrame(content_frame, fg_color="#0A0908")

_tm_inner = ctk.CTkFrame(ecran_type_mode, fg_color="transparent")
_tm_inner.pack(fill="both", expand=True, padx=60, pady=(40, 30))

# ── En-tête : étape + barre de progression (3/4) ──
ctk.CTkLabel(_tm_inner, text=t("type_session.etape1_eyebrow"), font=theme_sumi.mono(10),
             text_color="#8A8071", anchor="w").pack(fill="x")
_tm_progress_bg = ctk.CTkFrame(_tm_inner, height=2, fg_color="#1F1B18", corner_radius=0)
_tm_progress_bg.pack(fill="x", pady=(10, 0))
_tm_progress_bg.pack_propagate(False)
ctk.CTkFrame(_tm_progress_bg, height=2, fg_color="#E8DFCE", corner_radius=0).place(
    relx=0, rely=0, relwidth=0.75, relheight=1)

# ── Titre centré ──
ctk.CTkLabel(_tm_inner, text=t("type_session.etape1_titre"),
             font=theme_sumi.serif(32), text_color="#E8DFCE").pack(pady=(24, 0))
ctk.CTkLabel(_tm_inner, text=t("type_session.etape1_soustitre"),
             font=("Segoe UI", 13), text_color="#B8AF9E").pack(pady=(2, 0))

_ts_mode_var = [None]     # "libre" | "tunnel" | "hardcore"
_ts_mode_refs = {}        # mode → {"card":..., "sel_badge":...}

_tm_cartes_zone = ctk.CTkFrame(_tm_inner, fg_color="transparent")
_tm_cartes_zone.pack(fill="x", pady=(32, 0))
for _i in range(3):
    _tm_cartes_zone.grid_columnconfigure(_i, weight=1, uniform="mode")

# (mode, icône texte ou None [icône dessinée], titre, tag, description)
_TS_MODES = [
    ("libre",    None, t("mode.libre_titre"),    t("mode.libre_tag"),    t("mode.libre_desc")),
    ("tunnel",   None, t("mode.tunnel_titre"),   t("mode.tunnel_tag"),   t("mode.tunnel_desc")),
    ("hardcore", None, t("mode.hardcore_titre"), t("mode.hardcore_tag"), t("mode.hardcore_desc")),
]


def _ts_selectionner_mode(mode):
    _ts_mode_var[0] = mode
    for m, refs in _ts_mode_refs.items():
        is_hc = (m == "hardcore")
        selected = (m == mode)
        if is_hc:
            refs["card"].configure(border_width=1, border_color="#E63946")
        else:
            refs["card"].configure(border_width=2 if selected else 1,
                                    border_color="#E63946" if selected else "#2A2622")
        if refs["sel_badge"] is not None:
            if selected:
                if is_hc:
                    refs["sel_badge"].place(relx=0.0, x=1, y=-1, anchor="nw")
                else:
                    refs["sel_badge"].place(relx=1.0, x=-1, y=-1, anchor="ne")
            else:
                refs["sel_badge"].place_forget()
    btn_tm_suivant.configure(state="normal", fg_color="#E8DFCE",
                              hover_color="#D8CFC0", text_color="#0A0908")


def _ts_construire_carte(parent, col, height, pad_pady, desc_font_size,
                          bg, bd, txt_main, txt_tag, txt_desc,
                          icone_char, titre, tag, desc, pts, pts_color,
                          hc_badge, sel_badge_bg, sel_badge_fg, sel_badge_text,
                          on_click):
    """Construit une carte sélectionnable (mode à l'étape 1, type à l'étape 2) :
    cadre, badge HARDCORE permanent optionnel, badge de sélection (masqué par
    défaut — affiché/caché par l'appelant), icône, titre, tag, description,
    points optionnels, binding clic sur toute la carte.
    Retourne (card, sel_badge)."""
    card = ctk.CTkFrame(parent, fg_color=bg, corner_radius=0,
                         border_width=1, border_color=bd, height=height)
    card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 16, 0))
    card.grid_propagate(False)

    if hc_badge is not None:
        _hc_bg, _hc_fg = hc_badge
        ctk.CTkLabel(card, text=t("common.hardcore_badge"), font=theme_sumi.mono(9),
                     fg_color=_hc_bg, text_color=_hc_fg, corner_radius=0
                     ).place(relx=1.0, x=-1, y=-1, anchor="ne")

    sel_badge = ctk.CTkLabel(card, text=sel_badge_text, font=theme_sumi.mono(9),
                              fg_color=sel_badge_bg, text_color=sel_badge_fg,
                              corner_radius=0)
    # placé/affiché uniquement pour la carte sélectionnée (voir l'appelant)

    pad = ctk.CTkFrame(card, fg_color="transparent")
    pad.pack(fill="both", expand=True, padx=20, pady=pad_pady)

    ico_box = ctk.CTkFrame(pad, width=36, height=36, corner_radius=0,
                            fg_color="transparent", border_width=1, border_color=txt_main)
    ico_box.pack(anchor="w")
    ico_box.pack_propagate(False)
    if isinstance(icone_char, str):
        ctk.CTkLabel(ico_box, text=icone_char, font=theme_sumi.serif(18),
                     text_color=txt_main).place(relx=0.5, rely=0.5, anchor="center")
    elif icone_char is not None:
        _lbl_ico = ctk.CTkLabel(ico_box, image=icone_char, text="")
        _lbl_ico.image = icone_char
        _lbl_ico.place(relx=0.5, rely=0.5, anchor="center")

    ctk.CTkLabel(pad, text=titre, font=theme_sumi.serif(22),
                 text_color=txt_main, anchor="w").pack(fill="x", pady=(14, 0))
    ctk.CTkLabel(pad, text=tag, font=theme_sumi.mono(9),
                 text_color=txt_tag, anchor="w").pack(fill="x", pady=(2, 0))
    ctk.CTkLabel(pad, text=desc, font=("Segoe UI", desc_font_size), justify="left",
                 text_color=txt_desc, anchor="w", wraplength=220).pack(
                 fill="x", expand=True, pady=(14, 0), anchor="nw")
    if pts is not None:
        ctk.CTkLabel(pad, text=pts, font=theme_sumi.mono(10),
                     text_color=pts_color, anchor="w").pack(fill="x", pady=(14, 0))

    card.bind("<Button-1>", on_click)
    card.configure(cursor="hand2")
    for w in pad.winfo_children():
        w.bind("<Button-1>", on_click)
        try:
            w.configure(cursor="hand2")
        except Exception:
            pass

    return card, sel_badge


for _idx, (_mode, _ico, _titre, _tag, _desc) in enumerate(_TS_MODES):
    _is_hc = (_mode == "hardcore")
    _txt_mode = "#0A0908" if _is_hc else "#E8DFCE"
    _ico_rendered = theme_sumi.card_icon(_mode, _txt_mode) if _ico is None else _ico

    def _make_mode_click(m):
        def _click(e=None):
            _ts_selectionner_mode(m)
        return _click

    _card, _sel_badge = _ts_construire_carte(
        parent=_tm_cartes_zone, col=_idx, height=220, pad_pady=24, desc_font_size=12,
        bg="#E63946" if _is_hc else "#141210", bd="#E63946" if _is_hc else "#2A2622",
        txt_main=_txt_mode,
        txt_tag="#0A0908" if _is_hc else "#8A8071",
        txt_desc="#0A0908" if _is_hc else "#B8AF9E",
        icone_char=_ico_rendered, titre=_titre, tag=_tag, desc=_desc, pts=None, pts_color=None,
        hc_badge=("#0A0908", "#E63946") if _is_hc else None,
        sel_badge_bg="#0A0908" if _is_hc else "#E63946",
        sel_badge_fg="#E63946" if _is_hc else "#0A0908",
        sel_badge_text=t("common.selectionne_coche") if _is_hc else t("common.selectionne"),
        on_click=_make_mode_click(_mode))
    _ts_mode_refs[_mode] = {"card": _card, "sel_badge": _sel_badge}


def _ts_reset_mode():
    """Remet l'étape 1 (mode) à zéro."""
    _ts_mode_var[0] = None
    for m, refs in _ts_mode_refs.items():
        is_hc = (m == "hardcore")
        refs["card"].configure(border_width=1, border_color="#E63946" if is_hc else "#2A2622")
        if refs["sel_badge"] is not None:
            refs["sel_badge"].place_forget()
    btn_tm_suivant.configure(state="disabled", fg_color="#1F1B18",
                              hover_color="#1F1B18", text_color="#5C574C")


def _ts_continuer_mode():
    mode = _ts_mode_var[0]
    if not mode:
        return
    session_cfg["mode"] = mode
    session_cfg["hardcore"] = (mode == "hardcore")
    _ts_construire_cartes_type(mode)
    slide_vers(ecran_type_session, ecran_type_mode)


_tm_nav = ctk.CTkFrame(_tm_inner, fg_color="transparent")
_tm_nav.pack(side="bottom", fill="x", pady=(0, 0))

ctk.CTkButton(_tm_nav, text=t("common.retour"), width=130, height=40,
              font=theme_sumi.ui(12), corner_radius=3,
              fg_color="transparent", hover_color="#1F1B18",
              border_width=1, border_color="#E8DFCE", text_color="#E8DFCE",
              command=lambda: slide_vers(ecran_accueil, ecran_type_mode)
              ).pack(side="left")

btn_tm_suivant = ctk.CTkButton(_tm_nav, text=t("common.suivant"), width=180, height=40,
                                font=theme_sumi.ui(14, "bold"), corner_radius=3,
                                fg_color="#1F1B18", hover_color="#1F1B18",
                                text_color="#5C574C", state="disabled",
                                command=_ts_continuer_mode)
btn_tm_suivant.pack(side="right")

# =====================================================================
#           ÉCRAN TYPE DE SESSION — ÉTAPE 2 : TYPE (filtré selon le mode)
# =====================================================================
ecran_type_session = ctk.CTkFrame(content_frame, fg_color="#0A0908")

_ts_inner = ctk.CTkFrame(ecran_type_session, fg_color="transparent")
_ts_inner.pack(fill="both", expand=True, padx=60, pady=(40, 30))

# ── En-tête : étape + barre de progression (toujours pleine, étape 4/4) ──
ctk.CTkLabel(_ts_inner, text=t("type_session.etape2_eyebrow"), font=theme_sumi.mono(10),
             text_color="#8A8071", anchor="w").pack(fill="x")
_ts_progress_bg = ctk.CTkFrame(_ts_inner, height=2, fg_color="#1F1B18", corner_radius=0)
_ts_progress_bg.pack(fill="x", pady=(10, 0))
_ts_progress_bg.pack_propagate(False)
ctk.CTkFrame(_ts_progress_bg, height=2, fg_color="#E8DFCE", corner_radius=0).place(
    relx=0, rely=0, relwidth=1.0, relheight=1)

# ── Titre centré + sous-titre dynamique (texte/couleur selon le mode) ──
ctk.CTkLabel(_ts_inner, text=t("type_session.etape2_titre"),
             font=theme_sumi.serif(32), text_color="#E8DFCE").pack(pady=(24, 0))
_ts_soustitre = ctk.CTkLabel(_ts_inner, text="", font=("Segoe UI", 13, "italic"),
                              text_color="#B8AF9E")
_ts_soustitre.pack(pady=(2, 0))

# ── Variables de session ──
_ts_type_var   = [None]   # "infini" | "pomodoro" | "fixe" | "quarantaine"
_ts_duree_var  = ctk.StringVar(value="90")
_ts_cycles_var = ctk.StringVar(value="4")
_ts_jours_var  = ctk.StringVar(value="1")
_ts_type_refs  = {}       # type → {"card":..., "sel_badge":...}

# (type, icône, titre, tag, description, points)
_TS_TYPES = {
    "infini":      ("∞", t("type.infini_titre"), t("type.infini_tag"),
                    t("type.infini_desc"), t("type.infini_pts")),
    "pomodoro":    ("◐", t("type.pomodoro_titre"), t("type.pomodoro_tag"),
                    t("type.pomodoro_desc"), t("type.pomodoro_pts")),
    "fixe":        (None, t("type.fixe_titre"), t("type.fixe_tag"),
                    t("type.fixe_desc"), t("type.fixe_pts")),
    "quarantaine": ("禅", t("type.quarantaine_titre"), t("type.quarantaine_tag"),
                    t("type.quarantaine_desc"), t("type.quarantaine_pts")),
}

# mode → types proposés à l'étape 2, dans l'ordre d'affichage
_TS_MODE_TYPES = {
    "libre":    ["infini", "pomodoro"],
    "tunnel":   ["pomodoro", "fixe"],
    "hardcore": ["quarantaine", "pomodoro", "fixe"],
}

_TS_SOUSTITRES = {
    "libre":    (t("mode.soustitre_libre"), "#B8AF9E"),
    "tunnel":   (t("mode.soustitre_tunnel"), "#B8AF9E"),
    "hardcore": (t("mode.soustitre_hardcore"), "#E63946"),
}

_ts_cartes_holder = ctk.CTkFrame(_ts_inner, fg_color="transparent")
_ts_cartes_holder.pack(pady=(32, 0))

_ts_cartes_zone = ctk.CTkFrame(_ts_cartes_holder, fg_color="transparent")
_ts_cartes_zone.pack()
_ts_cartes_zone.pack_propagate(False)


def _ts_selectionner_type(typ):
    _ts_type_var[0] = typ
    mode = session_cfg.get("mode")
    is_hardcore = (mode == "hardcore")
    for typ_key, refs in _ts_type_refs.items():
        selected = (typ_key == typ)
        pleine = is_hardcore and typ_key == "quarantaine"
        if is_hardcore:
            refs["card"].configure(border_width=1, border_color="#E63946")
        else:
            refs["card"].configure(border_width=2 if selected else 1,
                                    border_color="#E63946" if selected else "#2A2622")
        if refs["sel_badge"] is not None:
            if selected:
                if is_hardcore:
                    refs["sel_badge"].place(relx=0.0, x=1, y=-1, anchor="nw")
                else:
                    refs["sel_badge"].place(relx=1.0, x=-1, y=-1, anchor="ne")
            else:
                refs["sel_badge"].place_forget()
    _ts_afficher_param(typ)
    btn_ts_demarrer.configure(
        state="normal",
        fg_color="#E63946" if is_hardcore else "#E8DFCE",
        hover_color="#A82230" if is_hardcore else "#D8CFC0",
        text_color="#0A0908")


def _ts_construire_cartes_type(mode):
    """(Re)construit les cartes de l'étape 2 selon le mode choisi à l'étape 1.
    Destroy/rebuild plutôt qu'un pool pré-construit : au plus 3 cartes, appelé
    uniquement à la navigation étape1→étape2 (jamais dans une boucle chaude) —
    le coût réel est de l'ordre de la milliseconde, un pool ajouterait de la
    complexité d'état (garder 4 cartes possibles en mémoire + les
    montrer/masquer/re-styler) sans gain perceptible."""
    for w in _ts_cartes_zone.winfo_children():
        w.destroy()
    _ts_type_refs.clear()
    _ts_type_var[0] = None
    _ts_param_zone.pack_forget()

    is_hardcore = (mode == "hardcore")
    btn_ts_demarrer.configure(
        state="disabled", fg_color="#1F1B18", hover_color="#1F1B18",
        text_color="#5C574C",
        text=t("common.entrer_hardcore") if is_hardcore else t("common.demarrer"))

    txt, col = _TS_SOUSTITRES.get(mode, _TS_SOUSTITRES["libre"])
    _ts_soustitre.configure(text=txt, text_color=col)

    types_du_mode = _TS_MODE_TYPES.get(mode, ["fixe"])
    n = len(types_du_mode)
    for i in range(max(n, 1)):
        _ts_cartes_zone.grid_columnconfigure(i, weight=1, uniform="type")
    _ts_cartes_zone.configure(width=900 if n == 3 else 600, height=230)

    for idx, typ in enumerate(types_du_mode):
        _ico, _titre, _tag, _desc, _pts = _TS_TYPES[typ]
        if is_hardcore and typ in ("pomodoro", "fixe"):
            _desc = _desc + t("type.suffixe_hardcore")
        _pleine = is_hardcore and typ == "quarantaine"
        _txt_typ = "#0A0908" if _pleine else "#E8DFCE"
        _ico_rendered = theme_sumi.card_icon(typ, _txt_typ) if _ico is None else _ico

        def _make_type_click(typ_key):
            def _click(e=None):
                _ts_selectionner_type(typ_key)
            return _click

        _card, _sel_badge = _ts_construire_carte(
            parent=_ts_cartes_zone, col=idx, height=230, pad_pady=22, desc_font_size=11,
            bg="#E63946" if _pleine else "#141210",
            bd="#E63946" if is_hardcore else "#2A2622",
            txt_main=_txt_typ,
            txt_tag="#0A0908" if _pleine else ("#E63946" if is_hardcore else "#8A8071"),
            txt_desc="#0A0908" if _pleine else "#B8AF9E",
            icone_char=_ico_rendered, titre=_titre, tag=_tag, desc=_desc,
            pts=_pts, pts_color="#0A0908" if _pleine else "#E63946",
            hc_badge=(("#0A0908", "#E63946") if _pleine else ("#E63946", "#0A0908")) if is_hardcore else None,
            sel_badge_bg="#0A0908" if _pleine else "#E63946",
            sel_badge_fg="#E63946" if _pleine else "#0A0908",
            sel_badge_text=t("common.selectionne_coche") if is_hardcore else t("common.selectionne"),
            on_click=_make_type_click(typ))
        _ts_type_refs[typ] = {"card": _card, "sel_badge": _sel_badge}


# ── Zone paramètre (apparaît sous les cartes selon le type) ──
_ts_param_zone = ctk.CTkFrame(_ts_inner, fg_color="#1F1B18", corner_radius=0,
                               border_width=1, border_color="#2A2622")

_ts_param_inner = ctk.CTkFrame(_ts_param_zone, fg_color="transparent")
_ts_param_inner.pack(fill="x", padx=20, pady=16)

_ts_param_texte = ctk.CTkFrame(_ts_param_inner, fg_color="transparent")
_ts_param_texte.pack(side="left")
_ts_param_label = ctk.CTkLabel(_ts_param_texte, text="",
                                font=theme_sumi.mono(10), text_color="#8A8071", anchor="w")
_ts_param_label.pack(fill="x", anchor="w")
_ts_param_hint = ctk.CTkLabel(_ts_param_texte, text="",
                               font=("Segoe UI", 12), text_color="#B8AF9E", anchor="w")
_ts_param_hint.pack(fill="x", anchor="w", pady=(2, 0))

_ts_param_valeur = ctk.CTkFrame(_ts_param_inner, fg_color="transparent")
_ts_param_valeur.pack(side="right")

_ts_param_entry_fixe = ctk.CTkEntry(_ts_param_valeur, textvariable=_ts_duree_var,
                                     width=70, justify="center",
                                     font=theme_sumi.mono(20),
                                     fg_color="#141210", border_color="#E8DFCE",
                                     text_color="#E8DFCE", corner_radius=0)
_ts_param_entry_pomo = ctk.CTkEntry(_ts_param_valeur, textvariable=_ts_cycles_var,
                                     width=70, justify="center",
                                     font=theme_sumi.mono(20),
                                     fg_color="#141210", border_color="#E8DFCE",
                                     text_color="#E8DFCE", corner_radius=0)
_ts_param_entry_quar = ctk.CTkEntry(_ts_param_valeur, textvariable=_ts_jours_var,
                                     width=70, justify="center",
                                     font=theme_sumi.mono(20),
                                     fg_color="#141210", border_color="#E8DFCE",
                                     text_color="#E8DFCE", corner_radius=0)
_ts_param_unite = ctk.CTkLabel(_ts_param_valeur, text="",
                                font=theme_sumi.mono(11), text_color="#8A8071")
_ts_param_unite.pack(side="left", padx=(10, 0))


def _ts_afficher_param(typ):
    """Affiche le champ paramètre correspondant au type choisi."""
    for e in (_ts_param_entry_fixe, _ts_param_entry_pomo, _ts_param_entry_quar):
        e.pack_forget()
    is_hardcore = (session_cfg.get("mode") == "hardcore")
    bordure = "#E63946" if is_hardcore else "#2A2622"
    label_col = "#E63946" if is_hardcore else "#8A8071"
    entree_bordure = "#E63946" if is_hardcore else "#E8DFCE"
    _ts_param_zone.configure(border_color=bordure)
    _ts_param_label.configure(text_color=label_col)
    for e in (_ts_param_entry_fixe, _ts_param_entry_pomo, _ts_param_entry_quar):
        e.configure(border_color=entree_bordure)

    if typ == "fixe":
        _ts_param_label.configure(text=t("param.duree_fixe_titre"))
        _ts_param_hint.configure(text=t("param.duree_fixe_desc"))
        _ts_param_entry_fixe.pack(side="left")
        _ts_param_unite.configure(text=t("param.duree_fixe_unite"))
    elif typ == "pomodoro":
        _ts_param_label.configure(text=t("param.pomodoro_titre"))
        _ts_param_hint.configure(text=t("param.pomodoro_desc"))
        _ts_param_entry_pomo.pack(side="left")
        _ts_param_unite.configure(text=t("param.pomodoro_unite"))
    elif typ == "quarantaine":
        _ts_param_label.configure(text=t("param.quarantaine_titre"))
        _ts_param_hint.configure(text=t("param.quarantaine_desc"))
        _ts_param_entry_quar.pack(side="left")
        _ts_param_unite.configure(text=t("param.quarantaine_unite"))
    else:  # infini
        _ts_param_label.configure(text=t("param.aucun_titre"))
        _ts_param_hint.configure(text=t("param.aucun_desc"))
        _ts_param_unite.configure(text="")
    _ts_param_zone.pack(pady=(20, 0))


def _ts_reset():
    """Remet l'étape 2 (type) à zéro — sans toucher au mode choisi à l'étape 1."""
    _ts_type_var[0] = None
    is_hardcore = (session_cfg.get("mode") == "hardcore")
    for typ_key, refs in _ts_type_refs.items():
        refs["card"].configure(border_width=1, border_color="#E63946" if is_hardcore else "#2A2622")
        if refs["sel_badge"] is not None:
            refs["sel_badge"].place_forget()
    _ts_param_zone.pack_forget()
    btn_ts_demarrer.configure(state="disabled", fg_color="#1F1B18",
                              hover_color="#1F1B18", text_color="#5C574C")


def _ts_appliquer_et_continuer():
    """Applique le type choisi et passe à l'écran contrat (ou au popup Hardcore)."""
    typ = _ts_type_var[0]
    session_cfg["type"] = typ
    try:
        session_cfg["duree_minutes"] = max(1, int(_ts_duree_var.get()))
    except ValueError:
        session_cfg["duree_minutes"] = 90
    try:
        session_cfg["nb_cycles"] = max(1, int(_ts_cycles_var.get()))
    except ValueError:
        session_cfg["nb_cycles"] = 4
    try:
        session_cfg["nb_jours"] = max(1, int(_ts_jours_var.get()))
    except ValueError:
        session_cfg["nb_jours"] = 1
    # Numéro de session (en-tête du Contrat + overlay de violation Hardcore) —
    # calculé ici, sur le vrai chemin de démarrage (ouvrir_contrat() ne sert
    # que le bouton "Reprendre la dernière", jamais atteint depuis l'assistant).
    try:
        from stats_manager import charger_sessions
        num_session = len(charger_sessions()) + 1
    except Exception:
        num_session = 1
    session_cfg["num_session"] = num_session
    lbl_contrat_meta.configure(text=t("contrat.meta", n=num_session))
    if session_cfg["hardcore"]:
        ouvrir_confirmation()
    else:
        slide_vers(ecran_contrat, ecran_type_session)


def _ts_continuer():
    typ = _ts_type_var[0]
    if not typ:
        return
    _ts_appliquer_et_continuer()


# ── Boutons navigation (bas de l'écran) ──
_ts_nav = ctk.CTkFrame(_ts_inner, fg_color="transparent")
_ts_nav.pack(side="bottom", fill="x", pady=(0, 0))

ctk.CTkButton(_ts_nav, text=t("common.retour"), width=130, height=40,
              font=theme_sumi.ui(12), corner_radius=3,
              fg_color="transparent", hover_color="#1F1B18",
              border_width=1, border_color="#E8DFCE", text_color="#E8DFCE",
              command=lambda: slide_vers(ecran_type_mode, ecran_type_session)
              ).pack(side="left")

btn_ts_demarrer = ctk.CTkButton(_ts_nav, text=t("common.demarrer"), width=200, height=40,
                                 font=theme_sumi.ui(14, "bold"), corner_radius=3,
                                 fg_color="#1F1B18", hover_color="#1F1B18",
                                 text_color="#5C574C", state="disabled",
                                 command=_ts_continuer)
btn_ts_demarrer.pack(side="right")

# =====================================================================
#           ÉCRAN WHITELIST NOUVEAU
# =====================================================================
ecran_whitelist_nouveau = ctk.CTkFrame(content_frame, fg_color="transparent")

NAVIGATEURS_NOMS = {"chrome", "firefox", "edge", "brave", "opera", "vivaldi",
                    "microsoft edge", "google chrome", "mozilla firefox"}

SITES_CONNUS = [
    ("Code",      ["github.com", "gitlab.com", "stackoverflow.com", "docs.python.org"]),
    ("Design",    ["figma.com", "canva.com"]),
    ("Outils",    ["notion.so", "google.com"]),
    ("Optionnel", ["youtube.com", "twitter.com"]),
]

_wl_app_vars    = {}    # app_name → BooleanVar
_wl_site_vars   = {}    # domain → BooleanVar
_wl_sites_frame = [None]
_wl_apps_list   = []    # [(display_name, lnk_path)]
_WL_ICON_CACHE  = {}    # lnk_path → CTkImage | False
_wl_construit       = [False]   # écran apps déjà construit ? (build-once → transitions fluides)
_wl_sites_construit = [False]   # écran sites déjà construit ?


_WL_DOSSIERS_SYSTEME = {
    "administrative tools", "windows administrative tools",
    "accessories", "system tools", "windows system",
    "maintenance", "windows ease of access", "windows powershell",
    "startup", "windows tools", "windows security",
}

# Préfixes exacts et sous-chaînes à exclure (vérification avec startswith ou in)
_WL_NOMS_SYSTEME_EXACT = {
    "administrative tools", "character map", "command prompt",
    "component services", "computer management", "control panel",
    "disk cleanup", "disk defragmenter", "event viewer",
    "file explorer", "internet explorer", "local security policy",
    "msconfig", "notepad", "on-screen keyboard",
    "performance monitor", "print management", "registry editor",
    "resource monitor", "run", "services", "system configuration",
    "system information", "task manager", "task scheduler",
    "windows defender firewall", "windows features", "windows memory diagnostic",
    "windows update", "wordpad", "xps viewer", "paint",
    "steps recorder", "remote desktop connection",
    "narrator", "snipping tool", "windows fax and scan",
    "windows media player", "windows mobility center",
    "dfrgui", "mdsched", "mstsc", "optionalfeatures",
    "magnify", "magnifier", "voiceaccess", "livecaptions",
}

# Sous-chaînes : si le nom contient l'un de ces mots → exclu
_WL_NOMS_SYSTEME_CONTIENT = {
    "licence", "license", "manuel d'utilisation", "user's manual",
    "uninstall", "désinstall", "readme", "lisezmoi",
    "what's new", "release notes", " help", "- aide",
    "setup wizard", "configuration wizard",
}

def _wl_est_systeme(key: str) -> bool:
    if key in _WL_NOMS_SYSTEME_EXACT:
        return True
    return any(s in key for s in _WL_NOMS_SYSTEME_CONTIENT)

def _wl_scanner_start_menu():
    """Retourne [(nom, chemin_lnk)] triés, en excluant les outils système Windows."""
    import os, glob
    dirs = [
        os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
        os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
    ]
    seen, result = set(), []
    for base in dirs:
        if not os.path.isdir(base):
            continue
        for lnk in glob.glob(os.path.join(base, "**", "*.lnk"), recursive=True):
            # Exclure si dans un dossier système
            rel = os.path.relpath(lnk, base)
            parts = rel.split(os.sep)
            if any(p.lower() in _WL_DOSSIERS_SYSTEME for p in parts[:-1]):
                continue
            name = os.path.splitext(os.path.basename(lnk))[0]
            key  = name.lower()
            # Exclure si nom système ou déjà vu
            if _wl_est_systeme(key) or key in seen:
                continue
            seen.add(key)
            result.append((name, lnk))
    return sorted(result, key=lambda x: x[0].lower())


def _wl_get_icon(lnk_path, size=32):
    """Extrait l'icône large (32×32 native) d'un .lnk → CTkImage (ou False)."""
    if lnk_path in _WL_ICON_CACHE:
        return _WL_ICON_CACHE[lnk_path]
    out = False
    try:
        import ctypes, ctypes.wintypes, win32gui, win32ui, win32con
        from PIL import Image

        class SHFILEINFO(ctypes.Structure):
            _fields_ = [("hIcon",         ctypes.wintypes.HICON),
                        ("iIcon",         ctypes.c_int),
                        ("dwAttributes",  ctypes.c_ulong),
                        ("szDisplayName", ctypes.c_wchar * 260),
                        ("szTypeName",    ctypes.c_wchar * 80)]

        info = SHFILEINFO()
        # SHGFI_ICON seul (sans SHGFI_SMALLICON) → icône large 32×32
        ctypes.windll.shell32.SHGetFileInfoW(
            lnk_path, 0, ctypes.byref(info), ctypes.sizeof(info),
            0x000000100)

        if info.hIcon:
            SZ = 32
            hdc  = win32ui.CreateDCFromHandle(win32gui.GetDC(0))
            hbmp = win32ui.CreateBitmap()
            hbmp.CreateCompatibleBitmap(hdc, SZ, SZ)
            hdc2 = hdc.CreateCompatibleDC()
            hdc2.SelectObject(hbmp)
            hdc2.FillSolidRect((0, 0, SZ, SZ), 0x141414)
            win32gui.DrawIconEx(hdc2.GetSafeHdc(), 0, 0,
                                info.hIcon, SZ, SZ, 0, None, win32con.DI_NORMAL)
            bmpstr = hbmp.GetBitmapBits(True)
            pil = Image.frombuffer("RGBA", (SZ, SZ), bmpstr, "raw", "BGRA", 0, 1)
            if size != SZ:
                pil = pil.resize((size, size), Image.LANCZOS)
            out = ctk.CTkImage(pil, size=(size, size))
            win32gui.DestroyIcon(info.hIcon)
            hdc2.DeleteDC()
            hdc.DeleteDC()
    except Exception:
        pass
    _WL_ICON_CACHE[lnk_path] = out
    return out


def _wl_construire(first_session: bool, wl_sauvegardee: dict):
    """Construit la whitelist : liste des apps installées avec icônes + recherche.
    Construite une seule fois (build-once) : aux visites suivantes on se contente de
    re-synchroniser les cases cochées → transitions instantanées (pas de gel UI)."""
    global _wl_app_vars, _wl_site_vars, _wl_apps_list

    # Déjà construit → juste re-synchroniser l'état des cases depuis la config sauvegardée
    if _wl_construit[0]:
        apps_coches = set(wl_sauvegardee.get("apps", []))
        for name, var in _wl_app_vars.items():
            var.set(name in apps_coches)
        return

    for w in ecran_whitelist_nouveau.winfo_children():
        w.destroy()
    _wl_app_vars.clear()
    _wl_site_vars.clear()

    apps_coches  = set(wl_sauvegardee.get("apps",    []))
    sites_coches = set(wl_sauvegardee.get("blocked", []))

    _wl_apps_list = _wl_scanner_start_menu()
    # Pré-créer les BooleanVars pour toutes les apps
    for name, _ in _wl_apps_list:
        _wl_app_vars[name] = ctk.BooleanVar(value=(name in apps_coches))

    # ── Titre ──
    ctk.CTkLabel(ecran_whitelist_nouveau,
                 text=t("whitelist_legacy.titre"),
                 font=theme_sumi.serif(20), text_color="#E8DFCE").pack(pady=(18, 2))
    ctk.CTkLabel(ecran_whitelist_nouveau,
                 text=t("whitelist_legacy.sous_titre"),
                 font=("Segoe UI", 10), text_color="#8A8071").pack(pady=(0, 8))

    # ── Barre de recherche ──
    search_var = ctk.StringVar()
    ctk.CTkEntry(ecran_whitelist_nouveau,
                 textvariable=search_var,
                 placeholder_text=t("whitelist_new.recherche_placeholder"),
                 height=34, corner_radius=3,
                 fg_color="#141210", border_color="#28231F",
                 text_color="#B8AF9E",
                 font=("Segoe UI", 11)).pack(fill="x", padx=16, pady=(0, 4))

    # ── Liste scrollable ──
    scroll = ctk.CTkScrollableFrame(ecran_whitelist_nouveau,
                                     fg_color="#141210",
                                     border_color="#1F1B18", border_width=1,
                                     corner_radius=3)
    scroll.pack(fill="both", expand=True, padx=16, pady=(0, 4))

    def _build_rows(apps):
        for w in scroll.winfo_children():
            w.destroy()
        for name, lnk_path in apps:
            var = _wl_app_vars[name]

            row = ctk.CTkFrame(scroll, fg_color="transparent", height=52)
            row.pack(fill="x", padx=2, pady=0)
            row.pack_propagate(False)

            # Icône
            ico = _wl_get_icon(lnk_path, 32)
            if ico:
                ctk.CTkLabel(row, image=ico, text="",
                             width=44).pack(side="left", padx=(10, 8))
            else:
                ctk.CTkLabel(row, text="▪", width=44,
                             font=("Segoe UI", 14), text_color="#1F1B18").pack(side="left", padx=(10, 8))

            # Checkbox à droite (packée avant le label pour garder l'ancre droite)
            ctk.CTkCheckBox(row, text="", variable=var,
                            width=22, height=22, corner_radius=2,
                            fg_color="#E63946", hover_color="#A82230",
                            border_color="#3A352E").pack(side="right", padx=(0, 16))

            # Nom de l'app
            ctk.CTkLabel(row, text=name, anchor="w",
                         font=("Segoe UI", 12), text_color="#B8AF9E").pack(side="left", fill="x", expand=True)

            # Séparateur
            ctk.CTkFrame(scroll, height=1, fg_color="#141210").pack(fill="x", padx=6)

    _build_rows(_wl_apps_list)

    _wl_search_after_id = [None]

    def _appliquer_recherche():
        q = search_var.get().lower().strip()
        filtered = [(n, p) for n, p in _wl_apps_list if q in n.lower()] if q else _wl_apps_list
        _build_rows(filtered)

    def _on_search(*_):
        # Anti-rebond : reconstruire la liste (destroy+rebuild de tous les widgets)
        # seulement 150 ms après la dernière frappe, pas à chaque caractère — sinon
        # ça sature le thread UI et fait sauter des touches en tapant vite.
        if _wl_search_after_id[0] is not None:
            ecran_whitelist_nouveau.after_cancel(_wl_search_after_id[0])
        _wl_search_after_id[0] = ecran_whitelist_nouveau.after(150, _appliquer_recherche)

    search_var.trace_add("write", _on_search)

    # ── Navigation bas ──
    nav = ctk.CTkFrame(ecran_whitelist_nouveau, fg_color="transparent")
    nav.pack(pady=(8, 14))

    ctk.CTkButton(nav, text=t("common.retour"), width=130, height=38,
                  font=("Segoe UI", 12), corner_radius=3,
                  fg_color="#141210", hover_color="#28231F", text_color="#8A8071",
                  command=lambda: slide_vers(ecran_contrat, ecran_whitelist_nouveau)
                  ).pack(side="left", padx=8)

    ctk.CTkButton(nav, text=t("common.suivant_fleche"), width=180, height=38,
                  font=("JetBrains Mono", 12, "bold"), corner_radius=3,
                  fg_color="#A82230", hover_color="#A82230", text_color="#E8DFCE",
                  command=_wl_valider).pack(side="left", padx=8)

    _wl_construit[0] = True


def _wl_valider():
    """Enregistre les apps choisies et passe à l'écran sites."""
    apps = [a for a, v in _wl_app_vars.items() if v.get()]
    session_cfg["whitelist_apps"] = apps
    _wl_sites_construire()
    slide_vers(ecran_whitelist_sites, ecran_whitelist_nouveau)


def _wl_afficher(ecran_precedent):
    """Point d'entrée : construit la whitelist et slide vers elle."""
    _wl_construire(first_session=is_first_session(),
                   wl_sauvegardee=charger_whitelist_sauvegardee())
    slide_vers(ecran_whitelist_nouveau, ecran_precedent)


def _wl_construire_recap(wl: dict, ecran_precedent):
    """Affiche un récapitulatif pré-coché avec bouton Modifier."""
    global whitelist_from_recap

    for w in ecran_whitelist_nouveau.winfo_children():
        w.destroy()

    ctk.CTkLabel(ecran_whitelist_nouveau, text=t("whitelist_new.recap_titre"),
                 font=("Segoe UI", 16, "bold"), text_color="#E8DFCE").pack(pady=(28, 8))

    recap_frame = ctk.CTkFrame(ecran_whitelist_nouveau,
                                fg_color="#141210",
                                border_color="#2A2622", border_width=1,
                                corner_radius=3)
    recap_frame.pack(padx=40, fill="x", pady=(0, 12))

    ctk.CTkLabel(recap_frame, text=t("whitelist_new.recap_apps"),
                 font=("JetBrains Mono", 10, "bold"), text_color="#5C574C",
                 anchor="w").pack(fill="x", padx=16, pady=(14, 6))

    apps_flow = ctk.CTkFrame(recap_frame, fg_color="transparent")
    apps_flow.pack(fill="x", padx=16, pady=(0, 12))
    for i, app in enumerate(wl["apps"]):
        ctk.CTkLabel(apps_flow, text=f"✓  {app}",
                     font=("Segoe UI", 11), text_color="#7A9B5C").grid(
                     row=i // 3, column=i % 3, sticky="w", padx=12, pady=2)

    if wl.get("blocked"):
        ctk.CTkLabel(recap_frame, text=t("whitelist_new.recap_sites"),
                     font=("JetBrains Mono", 10, "bold"), text_color="#5C574C",
                     anchor="w").pack(fill="x", padx=16, pady=(4, 4))
        sites_flow = ctk.CTkFrame(recap_frame, fg_color="transparent")
        sites_flow.pack(fill="x", padx=16, pady=(0, 14))
        for i, site in enumerate(wl["blocked"]):
            ctk.CTkLabel(sites_flow, text=f"⛔  {site}",
                         font=("Segoe UI", 11), text_color="#E63946").grid(
                         row=i // 4, column=i % 4, sticky="w", padx=8, pady=2)

    nav = ctk.CTkFrame(ecran_whitelist_nouveau, fg_color="transparent")
    nav.pack(pady=(8, 16))

    ctk.CTkButton(nav, text=t("common.retour"), width=120, height=38,
                  font=("Segoe UI", 12), corner_radius=3,
                  fg_color="#141210", hover_color="#28231F", text_color="#8A8071",
                  command=lambda: slide_vers(ecran_contrat, ecran_whitelist_nouveau)
                  ).pack(side="left", padx=6)

    ctk.CTkButton(nav, text=t("whitelist_new.btn_modifier"), width=120, height=38,
                  font=("Segoe UI", 12), corner_radius=3,
                  fg_color="#141210", hover_color="#28231F",
                  border_width=1, border_color="#1F1B18", text_color="#8A8071",
                  command=lambda: _wl_construire(
                      first_session=False, wl_sauvegardee=wl)
                  ).pack(side="left", padx=6)

    def _lancer_recap():
        global whitelist_from_recap
        whitelist_from_recap = True
        session_cfg["whitelist_apps"]  = wl["apps"]
        session_cfg["blocked_sites"] = wl.get("blocked", [])
        _lancer_session_finale()

    ctk.CTkButton(nav, text=t("whitelist_new.btn_lancer"), width=200, height=38,
                  font=("Segoe UI", 12, "bold"), corner_radius=3,
                  fg_color="#A82230", hover_color="#A82230", text_color="#E8DFCE",
                  command=_lancer_recap).pack(side="left", padx=6)

    slide_vers(ecran_whitelist_nouveau, ecran_precedent)


# =====================================================================
#           ÉCRAN SITES AUTORISÉS
# =====================================================================
ecran_whitelist_sites = ctk.CTkFrame(content_frame, fg_color="transparent")

# (domaine, badge, bg_badge, fg_badge, nom_affiche, bloqué_par_défaut)
_SITES_PRESETS = [
    ("youtube.com",      "YT",  "#E63946", "#E8DFCE", "YouTube",       True),
    ("instagram.com",    "IN",  "#C13584", "#E8DFCE", "Instagram",     True),
    ("tiktok.com",       "TK",  "#0A0908", "#E8DFCE", "TikTok",        True),
    ("facebook.com",     "FB",  "#1877F2", "#E8DFCE", "Facebook",      True),
    ("twitter.com",      "X",   "#0A0908", "#E8DFCE", "Twitter / X",   True),
    ("reddit.com",       "r/",  "#FF4500", "#E8DFCE", "Reddit",        True),
    ("snapchat.com",     "SC",  "#FFFC00", "#0A0908", "Snapchat",      True),
    ("twitch.tv",        "TV",  "#9146FF", "#E8DFCE", "Twitch",        True),
    ("netflix.com",      "NF",  "#E50914", "#E8DFCE", "Netflix",       True),
    ("9gag.com",         "9G",  "#0A0908", "#E8DFCE", "9GAG",          True),
    ("pinterest.com",    "PI",  "#E60023", "#E8DFCE", "Pinterest",     True),
    ("discord.com",      "DC",  "#5865F2", "#E8DFCE", "Discord",       True),
    ("linkedin.com",     "LI",  "#0A66C2", "#E8DFCE", "LinkedIn",      False),
    ("spotify.com",      "SP",  "#1DB954", "#E8DFCE", "Spotify",       False),
    ("twitch.tv",        "TV",  "#9146FF", "#E8DFCE", "Twitch",        True),
    ("amazon.com",       "AM",  "#D4A24C", "#0A0908", "Amazon",        False),
    ("leboncoin.fr",     "LC",  "#FF6E14", "#E8DFCE", "Leboncoin",     False),
    ("mangadex.org",     "MD",  "#FF6740", "#E8DFCE", "MangaDex",      True),
]
# Dédoublonner
_seen_d = set()
_SITES_PRESETS_CLEAN = []
for _row in _SITES_PRESETS:
    if _row[0] not in _seen_d:
        _seen_d.add(_row[0])
        _SITES_PRESETS_CLEAN.append(_row)
_SITES_PRESETS = _SITES_PRESETS_CLEAN

_wl_site_vars    = {}   # domain → BooleanVar
_wl_custom_sites = []   # domaines ajoutés manuellement
_wl_logo_cache   = {}   # domain → CTkImage (cache en mémoire)
_WL_LOGO_DIR     = _data_path(".logo_cache")
os.makedirs(_WL_LOGO_DIR, exist_ok=True)


def _charger_logo_async(domain: str, lbl_widget):
    """Charge le logo d'un site en arrière-plan et met à jour le widget."""
    def _fetch():
        # Vérifier cache mémoire
        if domain in _wl_logo_cache:
            try:
                lbl_widget.configure(image=_wl_logo_cache[domain], text="")
            except Exception:
                pass
            return

        # Vérifier cache disque
        cache_path = os.path.join(_WL_LOGO_DIR, domain.replace("/", "_") + ".png")
        img_pil = None

        if os.path.exists(cache_path):
            try:
                img_pil = Image.open(cache_path).convert("RGBA").resize((32, 32), Image.LANCZOS)
            except Exception:
                pass

        if img_pil is None:
            try:
                import urllib.request
                url = f"https://t0.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url=https://{domain}&size=64"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=4) as resp:
                    data = resp.read()
                img_pil = Image.open(io.BytesIO(data)).convert("RGBA").resize((32, 32), Image.LANCZOS)
                img_pil.save(cache_path)
            except Exception:
                return  # Garder le badge texte si échec

        ctk_img = ctk.CTkImage(light_image=img_pil, dark_image=img_pil, size=(32, 32))
        _wl_logo_cache[domain] = ctk_img
        try:
            lbl_widget.configure(image=ctk_img, text="")
        except Exception:
            pass

    threading.Thread(target=_fetch, daemon=True).start()


def _wl_sites_construire():
    """Construit l'écran sites bloqués (build-once → transitions fluides)."""
    global _wl_site_vars, _wl_custom_sites

    # Déjà construit → re-synchroniser l'état des cases preset depuis la sauvegarde
    if _wl_sites_construit[0]:
        saved = set(charger_whitelist_sauvegardee().get("blocked", []))
        if saved:
            for dom, var in _wl_site_vars.items():
                var.set(dom in saved)
        return

    for w in ecran_whitelist_sites.winfo_children():
        w.destroy()
    _wl_site_vars.clear()
    _wl_custom_sites.clear()

    saved_blocked = set(charger_whitelist_sauvegardee().get("blocked", []))
    preset_domains = {d for d, *_ in _SITES_PRESETS}

    # ── Titre ──
    ctk.CTkLabel(ecran_whitelist_sites,
                 text=t("sites.titre"),
                 font=theme_sumi.serif(20), text_color="#E8DFCE").pack(pady=(18, 2))
    ctk.CTkLabel(ecran_whitelist_sites,
                 text=t("sites.sous_titre"),
                 font=("Segoe UI", 10), text_color="#8A8071").pack(pady=(0, 10))

    # ── Champ ajout domaine à bloquer ──
    add_frame = ctk.CTkFrame(ecran_whitelist_sites, fg_color="#141210",
                              border_color="#28231F", border_width=1, corner_radius=3)
    add_frame.pack(fill="x", padx=16, pady=(0, 8))

    add_var = ctk.StringVar()
    add_entry = ctk.CTkEntry(add_frame, textvariable=add_var,
                              placeholder_text=t("sites.ajout_placeholder"),
                              height=40, corner_radius=3,
                              fg_color="#141210", border_color="#1F1B18",
                              text_color="#B8AF9E",
                              placeholder_text_color="#8A8071",
                              font=("Segoe UI", 12))
    add_entry.pack(side="left", fill="x", expand=True, padx=(10, 6), pady=8)

    def _ajouter_site():
        dom = _valider_domaine(add_var.get())
        if not dom or dom in _wl_site_vars:
            return
        _wl_site_vars[dom] = ctk.BooleanVar(value=True)
        _wl_custom_sites.append(dom)
        add_var.set("")
        _rebuild_custom_rows()

    ctk.CTkButton(add_frame, text=t("sites.btn_bloquer"), width=100, height=30,
                  font=("JetBrains Mono", 10, "bold"), corner_radius=3,
                  fg_color="#A82230", hover_color="#A82230", text_color="#EA5561",
                  command=_ajouter_site).pack(side="right", padx=(0, 10), pady=8)
    add_entry.bind("<Return>", lambda e: _ajouter_site())

    # ── Grille presets ──
    scroll = ctk.CTkScrollableFrame(ecran_whitelist_sites,
                                     fg_color="#141210",
                                     border_color="#1F1B18", border_width=1,
                                     corner_radius=3)
    scroll.pack(fill="both", expand=True, padx=16, pady=(0, 4))

    presets_frame = ctk.CTkFrame(scroll, fg_color="transparent")
    presets_frame.pack(fill="x", padx=8, pady=8)
    presets_frame.columnconfigure((0, 1, 2), weight=1)

    for i, (dom, badge, bg_b, fg_b, nom, default_blocked) in enumerate(_SITES_PRESETS):
        # Si sauvegarde existe → utiliser sauvegarde, sinon valeur par défaut
        if saved_blocked:
            checked = dom in saved_blocked
        else:
            checked = default_blocked
        var = ctk.BooleanVar(value=checked)
        _wl_site_vars[dom] = var

        cell = ctk.CTkFrame(presets_frame, fg_color="#141210",
                             border_color="#2A2622", border_width=1, corner_radius=3)
        cell.grid(row=i // 3, column=i % 3, padx=5, pady=4, sticky="ew")

        top = ctk.CTkFrame(cell, fg_color="transparent")
        top.pack(fill="x", padx=8, pady=(8, 4))

        # Logo : badge couleur comme placeholder, remplacé par favicon dès chargement
        logo_lbl = ctk.CTkLabel(top, text=badge, font=("JetBrains Mono", 9, "bold"),
                                text_color=fg_b, fg_color=bg_b,
                                width=32, height=32, corner_radius=3)
        logo_lbl.pack(side="left")
        _charger_logo_async(dom, logo_lbl)

        ctk.CTkCheckBox(top, text="", variable=var,
                        width=20, height=20, corner_radius=2,
                        fg_color="#E63946", hover_color="#A82230",
                        border_color="#3A352E").pack(side="right")

        ctk.CTkLabel(cell, text=nom, anchor="w",
                     font=("Segoe UI", 11, "bold"), text_color="#B8AF9E").pack(
                     fill="x", padx=8, pady=(0, 2))
        ctk.CTkLabel(cell, text=dom, anchor="w",
                     font=("Segoe UI", 9), text_color="#5C574C").pack(
                     fill="x", padx=8, pady=(0, 6))

        def _bind_cell(c, v):
            def _toggle(e=None): v.set(not v.get())
            for w in [c] + list(c.winfo_children()):
                try: w.bind("<Button-1>", _toggle)
                except: pass
        _bind_cell(cell, var)

    # ── Domaines custom ──
    custom_container = ctk.CTkFrame(scroll, fg_color="transparent")
    custom_container.pack(fill="x", padx=8, pady=(4, 8))

    def _rebuild_custom_rows():
        for w in custom_container.winfo_children():
            w.destroy()
        for dom in _wl_custom_sites:
            if dom not in _wl_site_vars:
                _wl_site_vars[dom] = ctk.BooleanVar(value=True)
            row = ctk.CTkFrame(custom_container, fg_color="#141210",
                               border_color="#2A2622", border_width=1, corner_radius=3)
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(row, text="🚫", font=("Segoe UI", 12),
                         width=30).pack(side="left", padx=(8, 4))
            ctk.CTkLabel(row, text=dom, anchor="w",
                         font=("Segoe UI", 11), text_color="#B8AF9E").pack(
                         side="left", fill="x", expand=True)
            ctk.CTkCheckBox(row, text="", variable=_wl_site_vars[dom],
                            width=20, height=20, corner_radius=2,
                            fg_color="#E63946", hover_color="#A82230",
                            border_color="#3A352E").pack(side="right", padx=(0, 12), pady=8)

    # Restaurer domaines custom de la sauvegarde
    for dom in saved_blocked:
        if dom not in {d for d, *_ in _SITES_PRESETS} and dom not in _wl_custom_sites:
            _wl_custom_sites.append(dom)
            _wl_site_vars[dom] = ctk.BooleanVar(value=True)
    _rebuild_custom_rows()

    # ── Navigation ──
    nav = ctk.CTkFrame(ecran_whitelist_sites, fg_color="transparent")
    nav.pack(pady=(6, 14))

    ctk.CTkButton(nav, text=t("common.retour"), width=130, height=38,
                  font=("Segoe UI", 12), corner_radius=3,
                  fg_color="#141210", hover_color="#28231F", text_color="#8A8071",
                  command=lambda: slide_vers(ecran_whitelist_nouveau, ecran_whitelist_sites)
                  ).pack(side="left", padx=8)

    ctk.CTkButton(nav, text=t("whitelist_legacy.btn_demarrer"), width=220, height=38,
                  font=("JetBrains Mono", 12, "bold"), corner_radius=3,
                  fg_color="#A82230", hover_color="#A82230", text_color="#E8DFCE",
                  command=_wl_sites_valider).pack(side="left", padx=8)

    _wl_sites_construit[0] = True


def _wl_sites_valider():
    """Enregistre les sites bloqués et lance l'animation cadenas."""
    sites = [d for d, v in _wl_site_vars.items() if v.get()]
    session_cfg["blocked_sites"] = sites
    sauvegarder_whitelist_session(session_cfg["whitelist_apps"], sites)

    if sites and not _est_admin():
        # Fenêtre d'avertissement admin
        dlg = ctk.CTkToplevel(root)
        dlg.title(t("sites_admin.titre_fenetre"))
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.attributes("-topmost", True)
        _centrer_popup(dlg, 420, 200)
        ctk.CTkLabel(dlg, text=t("sites_admin.titre"),
                     font=("JetBrains Mono", 14, "bold"), text_color="#D4A24C").pack(pady=(24, 4))
        ctk.CTkLabel(dlg,
                     text=t("sites_admin.texte"),
                     font=("Segoe UI", 11), text_color="#B8AF9E", justify="center").pack(pady=(0, 16))
        btns = ctk.CTkFrame(dlg, fg_color="transparent")
        btns.pack()
        ctk.CTkButton(btns, text=t("sites_admin.relancer"), width=160, height=34,
                      fg_color="#5A3000", hover_color="#7A4800", text_color="#E8C99A",
                      font=("Segoe UI", 11),
                      command=lambda: (_relancer_en_admin())).pack(side="left", padx=8)
        ctk.CTkButton(btns, text=t("sites_admin.continuer_sans"), width=180, height=34,
                      fg_color="#1F1B18", hover_color="#28231F", text_color="#8A8071",
                      font=("Segoe UI", 11),
                      command=lambda: (dlg.destroy(),
                                       _animer_serrure_et_lancer(parent=ecran_whitelist_sites,
                                                                  on_done=_apres_animation_whitelist))
                      ).pack(side="left", padx=8)
        return

    _animer_serrure_et_lancer(parent=ecran_whitelist_sites,
                              on_done=_apres_animation_whitelist)


# =====================================================================
#           ÉCRAN VERROUILLAGE
# =====================================================================
ecran_verrouillage = ctk.CTkFrame(content_frame, fg_color="transparent")
_verrou_countdown_id = [None]
_verrou_restant = [3]
_verrou_lbl_compte = [None]
_verrou_annule = [False]

def _construire_ecran_verrouillage():
    """Reconstruit le contenu de l'écran de verrouillage selon session_cfg."""
    for w in ecran_verrouillage.winfo_children():
        w.destroy()
    _verrou_annule[0] = False
    _verrou_restant[0] = 3

    lbl_compte = ctk.CTkLabel(ecran_verrouillage, text="3...",
                               font=("JetBrains Mono", 72, "bold"), text_color="#1F1B18")
    lbl_compte.place(relx=0.5, rely=0.5, anchor="center")
    _verrou_lbl_compte[0] = lbl_compte

    def _tick_verrou():
        if _verrou_annule[0]:
            return
        _verrou_restant[0] -= 1
        if _verrou_restant[0] <= 0:
            _lancer_session_finale()
            return
        if _verrou_lbl_compte[0] and _verrou_lbl_compte[0].winfo_exists():
            _verrou_lbl_compte[0].configure(text=f"{_verrou_restant[0]}...")
        _verrou_countdown_id[0] = root.after(1000, _tick_verrou)

    _verrou_countdown_id[0] = root.after(1000, _tick_verrou)


def _lancer_session_finale():
    """Applique la whitelist et démarre la session selon session_cfg."""
    global session_type, duree_heures, duree_minutes, mode_infini, _wl_session_keys_cache

    # Précalculer les clés de whitelist une seule fois pour toute la session
    _wl_session_keys_cache = set()
    for _app in session_cfg.get("whitelist_apps", []):
        _wl_session_keys_cache |= generer_cles_recherche(_app)

    # Mode Libre = pas de blocage d'apps
    mode = session_cfg.get("mode", "tunnel")
    if mode == "libre":
        session_cfg["whitelist_apps"] = list(checkbox_vars.keys())  # tout autoriser

    _preparer_ecran_session()

    # Bloquer les sites cochés via le fichier hosts (silencieux si pas admin)
    sites_bloques = session_cfg.get("blocked_sites", [])
    if sites_bloques and mode != "libre":
        bloquer_sites(sites_bloques)

    type_session = session_cfg["type"]
    if type_session == "pomodoro":
        session_type = "pomodoro"
        global pomodoro_phase
        pomodoro_phase = "focus"
        duree_heures = 0
        duree_minutes = 25
        mode_infini = False
        demarrer_pomodoro()
    elif type_session == "quarantaine":
        session_type = "quarantaine"
        demarrer_quarantaine()
    elif type_session == "infini":
        session_type = "infini"
        mode_infini = True
        demarrer_infini()
    else:  # fixe
        session_type = "normale"
        mins = session_cfg["duree_minutes"]
        duree_heures = mins // 60
        duree_minutes = mins % 60
        mode_infini = False
        demarrer()

    # Watchdog léger : jamais pour Hardcore (verrouillage lourd via hc_activer
    # ci-dessous à la place). Point de passage unique — mode déjà fixé dans
    # session_cfg à ce stade, donc plus besoin de re-vérifier dans chaque demarrer*().
    if not session_cfg.get("hardcore"):
        _session_watchdog_activer()

    # Activer le Mode Hardcore si demandé.
    # En reprise (après kill/redémarrage), on réactive directement sans re-gater le premium
    # → les gardiens redémarrent même hors ligne.
    if session_cfg.get("hardcore"):
        root.after(800, _hc_activer_effectif if _HC_REPRISE else hc_activer)


# =====================================================================
#     OVERLAY VIOLATION HARDCORE — écran 11 du design (verrouillage négatif)
#     Habillage visuel plein écran pour la Soft-Correction en session
#     Hardcore : aucune nouvelle règle métier, la fermeture forcée reste
#     entièrement pilotée par _surveiller_processus() / soft_correction_*.
# =====================================================================
_hc_violation_win = [None]


def _hc_temps_restant_secs():
    """Temps restant avant la fin de la session Hardcore, en secondes.
    Quarantaine active → échéance dédiée (peut dépasser 24h) ; sinon (reprise
    Hardcore après redémarrage, qui tourne en type 'libre') → temps_restant."""
    if quarantaine_active:
        return max(0, int(quarantaine_fin_ts - time.time()))
    return max(0, int(temps_restant))


def _formater_hms(secs):
    secs = max(0, int(secs))
    j, reste = divmod(secs, 86400)
    h, reste = divmod(reste, 3600)
    m, s = divmod(reste, 60)
    if j > 0:
        return f"{j}j {h:02d}:{m:02d}:{s:02d}"
    return f"{h:02d}:{m:02d}:{s:02d}"


def _construire_violation_hardcore():
    """Construit (une seule fois, réutilisé ensuite) le Toplevel plein écran
    rouge de l'écran 11 du design — fond #A82230, sceau 120px, minuteur géant."""
    win = ctk.CTkToplevel(root)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.configure(fg_color=theme_sumi.HANKO_DEEP)
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    win.geometry(f"{sw}x{sh}+0+0")

    # Coin haut-gauche : puce + libellé session
    coin_hg = ctk.CTkFrame(win, fg_color="transparent")
    coin_hg.place(x=32, y=24)
    ctk.CTkFrame(coin_hg, width=8, height=8, fg_color=theme_sumi.PAPER,
                 corner_radius=0).pack(side="left", pady=2)
    win._lbl_session = ctk.CTkLabel(coin_hg, text="", font=theme_sumi.mono(10),
                                     text_color=theme_sumi.PAPER, anchor="w")
    win._lbl_session.pack(side="left", padx=(10, 0))

    # Coin haut-droit : tentative détectée
    win._lbl_tentative = ctk.CTkLabel(win, text="", font=theme_sumi.mono(10),
                                       text_color=theme_sumi.PAPER, anchor="e")
    win._lbl_tentative.place(relx=1.0, x=-32, y=24, anchor="ne")

    # Accents en L (coin haut-gauche + coin bas-droit)
    ctk.CTkFrame(win, width=24, height=2, fg_color=theme_sumi.PAPER,
                 corner_radius=0).place(x=60, y=60)
    ctk.CTkFrame(win, width=2, height=24, fg_color=theme_sumi.PAPER,
                 corner_radius=0).place(x=60, y=60)
    ctk.CTkFrame(win, width=24, height=2, fg_color=theme_sumi.PAPER,
                 corner_radius=0).place(relx=1.0, rely=1.0, x=-84, y=-62)
    ctk.CTkFrame(win, width=2, height=24, fg_color=theme_sumi.PAPER,
                 corner_radius=0).place(relx=1.0, rely=1.0, x=-62, y=-84)

    # Colonne centrale
    centre = ctk.CTkFrame(win, fg_color="transparent")
    centre.place(relx=0.5, rely=0.5, anchor="center")

    seal = ctk.CTkFrame(centre, width=120, height=120, corner_radius=60,
                         fg_color=theme_sumi.PAPER)
    seal.pack()
    seal.pack_propagate(False)
    ctk.CTkLabel(seal, text="禅", font=theme_sumi.serif(64),
                 text_color=theme_sumi.HANKO_DEEP).place(relx=0.5, rely=0.5, anchor="center")

    ctk.CTkLabel(centre, text=t("violation.verrou_en_cours"),
                 font=theme_sumi.mono(11), text_color=theme_sumi.PAPER
                 ).pack(pady=(28, 0))

    ligne_titre = ctk.CTkFrame(centre, fg_color="transparent")
    ligne_titre.pack(pady=(6, 0))
    ctk.CTkLabel(ligne_titre, text=t("violation.reviens_dans"), font=theme_sumi.serif(52),
                 text_color=theme_sumi.PAPER).pack(side="left")
    win._lbl_minuteur = ctk.CTkLabel(
        ligne_titre, text="00:00:00",
        font=(theme_sumi.FONT_SERIF, 52, "underline"),
        text_color=theme_sumi.PAPER)
    win._lbl_minuteur.pack(side="left")
    ctk.CTkLabel(ligne_titre, text=".", font=theme_sumi.serif(52),
                 text_color=theme_sumi.PAPER).pack(side="left")

    win._lbl_citation = ctk.CTkLabel(
        centre, text="", font=theme_sumi.serif(18, italic=True),
        text_color=theme_sumi.PAPER, wraplength=520, justify="center")
    win._lbl_citation.pack(pady=(14, 0))

    # Ligne de courtoisie (10 s pour fermer soi-même) — conservée en plus du
    # décompte principal pour ne pas perdre l'info déjà affichée dans
    # label_statut en dehors du mode Hardcore.
    win._lbl_grace = ctk.CTkLabel(
        centre, text="", font=theme_sumi.mono(12), text_color=theme_sumi.PAPER)
    win._lbl_grace.pack(pady=(10, 0))

    bloc = ctk.CTkFrame(centre, fg_color="transparent", border_width=1,
                         border_color=theme_sumi.PAPER, corner_radius=0)
    bloc.pack(pady=(36, 0))
    ligne_bloc = ctk.CTkFrame(bloc, fg_color="transparent")
    ligne_bloc.pack(padx=24, pady=16)
    ctk.CTkLabel(ligne_bloc, text=t("violation.deblocage_urgence"), font=theme_sumi.mono(10),
                 text_color=theme_sumi.PAPER).pack(side="left")
    ctk.CTkFrame(ligne_bloc, width=1, height=20,
                 fg_color=theme_sumi.PAPER).pack(side="left", padx=16)
    ctk.CTkLabel(ligne_bloc, text="• • • • • •", font=theme_sumi.mono(14),
                 text_color=theme_sumi.PAPER, fg_color=theme_sumi.HANKO_FIELD,
                 width=160, corner_radius=0, padx=12, pady=8).pack(side="left")
    # Cosmétique/informatif uniquement — pas de mécanisme de déblocage réel
    # (cf. plan de reproduction du design, point de scope #3).
    ctk.CTkButton(ligne_bloc, text=t("violation.debloquer_btn"), font=theme_sumi.ui(12, "bold"),
                  fg_color=theme_sumi.PAPER, hover_color=theme_sumi.PAPER,
                  text_color=theme_sumi.HANKO_DEEP, corner_radius=0, width=150,
                  command=lambda: None).pack(side="left", padx=(16, 0))

    ctk.CTkLabel(centre, text=t("violation.tentatives_incorrectes"),
                 font=theme_sumi.mono(10), text_color=theme_sumi.PAPER
                 ).pack(pady=(12, 0))

    win.withdraw()
    return win


def _afficher_violation_hardcore(app_nom, countdown):
    """Affiche/actualise l'overlay plein écran de violation Hardcore (écran 11) :
    habillage visuel uniquement, appelé depuis _surveiller_processus() sans
    modifier la logique de Soft-Correction existante."""
    win = _hc_violation_win[0]
    if win is None or not win.winfo_exists():
        win = _construire_violation_hardcore()
        _hc_violation_win[0] = win

    num_session = session_cfg.get("num_session", 1)
    win._lbl_session.configure(text=t("violation.session_label", n=num_session))
    win._lbl_tentative.configure(
        text=t("violation.tentative", app=app_nom.upper(), heure=datetime.now().strftime("%H:%M")))
    win._lbl_minuteur.configure(text=_formater_hms(_hc_temps_restant_secs()))

    objectif = (session_cfg.get("objectif") or "").strip()
    if objectif:
        court = objectif[:100] + ("…" if len(objectif) > 100 else "")
        prenom = _nom_utilisateur_local() or t("session.toi_defaut")
        heure_debut = session_cfg.get("heure_debut", "")
        win._lbl_citation.configure(text=t("violation.citation", citation=court, prenom=prenom, heure=heure_debut))
    else:
        win._lbl_citation.configure(text="")

    win._lbl_grace.configure(
        text=t("violation.grace", app=app_nom, n=countdown))

    win.deiconify()
    win.lift()
    win.attributes("-topmost", True)


def _fermer_violation_hardcore():
    """Cache l'overlay de violation Hardcore (réutilisé, jamais détruit)."""
    win = _hc_violation_win[0]
    if win is not None and win.winfo_exists():
        win.withdraw()


def _afficher_ecran_langue():
    """Choix de langue au tout premier lancement (pas de clé "lang" dans
    config.json). Textes en dur (jamais via t()) — aucune langue n'est encore
    choisie. Au clic Confirmer : écrit lang dans config.json puis relance l'app
    pour que tout reparte dans la bonne langue dès le premier widget."""
    pop = ctk.CTkToplevel(root)
    pop.title("BeFree")
    pop.resizable(False, False)
    pop.transient(root)
    pop.grab_set()
    pop.configure(fg_color="#0A0908")
    pop.protocol("WM_DELETE_WINDOW", lambda: None)
    _centrer_popup(pop, 560, 490)

    modal = ctk.CTkFrame(pop, fg_color="#141210", corner_radius=0,
                          border_width=1, border_color="#2A2622")
    modal.pack(fill="both", expand=True, padx=20, pady=20)

    inner = ctk.CTkFrame(modal, fg_color="transparent")
    inner.pack(fill="both", expand=True, padx=32, pady=(28, 24))

    # Wordmark
    _wm = ctk.CTkFrame(inner, fg_color="transparent")
    _wm.pack()
    ctk.CTkLabel(_wm, text="BeFree", font=theme_sumi.serif(26),
                 text_color="#E8DFCE").pack(side="left")
    ctk.CTkLabel(_wm, text=".", font=theme_sumi.serif(26),
                 text_color="#E63946").pack(side="left")

    ctk.CTkLabel(inner, text="Choisissez votre langue",
                 font=theme_sumi.serif(20), text_color="#E8DFCE").pack(pady=(14, 0))
    ctk.CTkLabel(inner, text="Choose your language",
                 font=theme_sumi.mono(10), text_color="#8A8071").pack(pady=(2, 0))

    # Drapeaux PIL
    def _flag_fr():
        img = Image.new("RGBA", (56, 38), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, 18, 37], fill="#002395")
        d.rectangle([19, 0, 37, 37], fill="#FFFFFF")
        d.rectangle([38, 0, 55, 37], fill="#ED2939")
        return ctk.CTkImage(light_image=img, size=(56, 38))

    def _flag_uk():
        img = Image.new("RGBA", (56, 38), "#012169")
        d = ImageDraw.Draw(img)
        d.line([(0, 0), (56, 38)], fill="white", width=8)
        d.line([(0, 38), (56, 0)], fill="white", width=8)
        d.line([(0, 0), (56, 38)], fill="#C8102E", width=4)
        d.line([(0, 38), (56, 0)], fill="#C8102E", width=4)
        d.rectangle([24, 0, 32, 38], fill="white")
        d.rectangle([0, 15, 56, 23], fill="white")
        d.rectangle([26, 0, 30, 38], fill="#C8102E")
        d.rectangle([0, 17, 56, 21], fill="#C8102E")
        return ctk.CTkImage(light_image=img, size=(56, 38))

    def _flag_es_small():
        img = Image.new("RGBA", (24, 16), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, 23, 3],  fill="#AA151B")
        d.rectangle([0, 4, 23, 11], fill="#F1BF00")
        d.rectangle([0, 12, 23, 15], fill="#AA151B")
        return ctk.CTkImage(light_image=img, size=(24, 16))

    _img_fr = _flag_fr()
    _img_uk = _flag_uk()
    _img_es = _flag_es_small()

    def _choisir(code):
        cfg = charger_config()
        cfg["lang"] = code
        sauvegarder_config(cfg)
        subprocess.Popen(
            _cmd_relancer(),
            cwd=DATA_DIR,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        os._exit(0)

    _sel_var  = [None]
    _card_refs = {}

    _LANG_DATA = [
        ("fr", _img_fr, "Français", "FRANÇAIS"),
        ("en", _img_uk, "English",  "ENGLISH"),
    ]

    def _selectionner(code):
        _sel_var[0] = code
        for _c, _refs in _card_refs.items():
            _sel = (_c == code)
            _refs["card"].configure(
                border_width=2 if _sel else 1,
                border_color="#E63946" if _sel else "#2A2622")
            if _sel:
                _refs["badge"].place(relx=1.0, x=-1, y=-1, anchor="ne")
            else:
                _refs["badge"].place_forget()
            _refs["native"].configure(text_color="#E63946" if _sel else "#8A8071")
        btn_confirmer.configure(state="normal", fg_color="#E8DFCE",
                                 hover_color="#D8CFC0", text_color="#0A0908")

    # Cartes de langue
    cards_frame = ctk.CTkFrame(inner, fg_color="transparent")
    cards_frame.pack(pady=(20, 0))

    for _i, (_code, _flag_img, _nom, _natif) in enumerate(_LANG_DATA):
        _card = ctk.CTkFrame(cards_frame, width=210, height=165,
                              fg_color="#141210", corner_radius=0,
                              border_width=1, border_color="#2A2622")
        _card.grid(row=0, column=_i, padx=(0 if _i == 0 else 12, 0))
        _card.grid_propagate(False)

        _badge = ctk.CTkLabel(_card, text="✓  SÉLECTIONNÉ",
                               font=theme_sumi.mono(8),
                               fg_color="#E63946", text_color="#0A0908",
                               corner_radius=0)

        _pad = ctk.CTkFrame(_card, fg_color="transparent")
        _pad.place(relx=0.5, rely=0.5, anchor="center")

        _fl = ctk.CTkLabel(_pad, image=_flag_img, text="")
        _fl.image = _flag_img
        _fl.pack(pady=(0, 10))

        ctk.CTkLabel(_pad, text=_nom, font=theme_sumi.serif(20),
                     text_color="#E8DFCE").pack()
        _native_lbl = ctk.CTkLabel(_pad, text=_natif, font=theme_sumi.mono(9),
                                    text_color="#8A8071")
        _native_lbl.pack(pady=(2, 0))

        _card_refs[_code] = {"card": _card, "badge": _badge, "native": _native_lbl}

        def _make_click(c):
            def _click(e=None): _selectionner(c)
            return _click

        _fn = _make_click(_code)
        _card.bind("<Button-1>", _fn)
        _card.configure(cursor="hand2")
        for _w in _pad.winfo_children():
            _w.bind("<Button-1>", _fn)
            try:
                _w.configure(cursor="hand2")
            except Exception:
                pass

    # Ligne Español (coming soon)
    _es_row = ctk.CTkFrame(inner, fg_color="transparent")
    _es_row.pack(fill="x", pady=(14, 0))
    _es_flag_lbl = ctk.CTkLabel(_es_row, image=_img_es, text="")
    _es_flag_lbl.image = _img_es
    _es_flag_lbl.pack(side="left", padx=(0, 8))
    ctk.CTkLabel(_es_row, text="Español", font=theme_sumi.serif(15),
                 text_color="#5C574C").pack(side="left")
    ctk.CTkLabel(_es_row, text="PROCHAINEMENT", font=theme_sumi.mono(9),
                 text_color="#5C574C").pack(side="right")

    # Bouton Confirmer
    btn_confirmer = ctk.CTkButton(
        inner, text="Confirmer  ▶",
        font=theme_sumi.ui(14, "bold"),
        state="disabled",
        fg_color="#1F1B18", hover_color="#1F1B18",
        text_color="#5C574C",
        corner_radius=0, height=46,
        command=lambda: _choisir(_sel_var[0]) if _sel_var[0] else None
    )
    btn_confirmer.pack(fill="x", pady=(20, 0))

    pop.lift()
    pop.attributes("-topmost", True)


_ERREURS_AUTH_FR = {
    "user_already_exists": "Un compte existe déjà avec cet email — connecte-toi plutôt.",
    "invalid_credentials": "Email ou mot de passe incorrect.",
    "email_not_confirmed": "Confirme ton email avant de te connecter (vérifie ta boîte mail).",
    "weak_password": "Mot de passe trop faible (6 caractères minimum).",
}


def _msg_auth_fr(erreur):
    return _ERREURS_AUTH_FR.get(erreur.code, erreur.message)


def _afficher_ecran_compte_fondateur(then_langue, depuis_parametres=False):
    """Écran Compte Fondateur (email + mot de passe via Supabase Auth),
    réservation atomique d'une des 500 places. Toujours contournable
    ("Continuer sans compte") — ne bloque jamais l'usage gratuit de BeFree.
    Affiché soit au tout premier lancement (avant la langue), soit rappelé
    depuis Paramètres pour un utilisateur en mode invité (depuis_parametres)."""
    pop = ctk.CTkToplevel(root)
    pop.title("BeFree")
    pop.resizable(False, False)
    pop.transient(root)
    pop.grab_set()
    pop.configure(fg_color="#0A0908")
    pop.protocol("WM_DELETE_WINDOW", lambda: None)
    _centrer_popup(pop, 560, 700)

    modal = ctk.CTkFrame(pop, fg_color="#141210", corner_radius=0,
                          border_width=1, border_color="#2A2622")
    modal.pack(fill="both", expand=True, padx=20, pady=20)

    inner = ctk.CTkFrame(modal, fg_color="transparent")
    inner.pack(fill="both", expand=True, padx=32, pady=(26, 22))

    _wm = ctk.CTkFrame(inner, fg_color="transparent")
    _wm.pack()
    ctk.CTkLabel(_wm, text="BeFree", font=theme_sumi.serif(26),
                 text_color="#E8DFCE").pack(side="left")
    ctk.CTkLabel(_wm, text=".", font=theme_sumi.serif(26),
                 text_color="#E63946").pack(side="left")

    ctk.CTkLabel(inner, text="DISCIPLINE — TU EN AS DÉJÀ. NOUS L'ORGANISONS.",
                 font=theme_sumi.mono(9), text_color="#8A8071"
                 ).pack(pady=(6, 0))

    lbl_places = ctk.CTkLabel(inner, text="", font=theme_sumi.mono(9, "bold"),
                               text_color="#E63946")
    lbl_places.pack(pady=(10, 0))

    def _rafraichir_places():
        restantes = founder_backend.slots_remaining()
        lbl_places.configure(
            text=f"{restantes} / 500 PLACES PREMIUM RESTANTES" if restantes is not None else "")

    _rafraichir_places()

    onglets = ctk.CTkFrame(inner, fg_color="transparent")
    onglets.pack(fill="x", pady=(20, 0))

    _mode = ["connexion"]

    btn_onglet_connexion = ctk.CTkButton(
        onglets, text="CONNEXION", font=theme_sumi.mono(11, "bold"),
        corner_radius=0, height=38, border_width=0)
    btn_onglet_connexion.pack(side="left", fill="x", expand=True)

    btn_onglet_creer = ctk.CTkButton(
        onglets, text="CRÉER UN COMPTE", font=theme_sumi.mono(11, "bold"),
        corner_radius=0, height=38, border_width=0)
    btn_onglet_creer.pack(side="left", fill="x", expand=True)

    ctk.CTkLabel(inner, text="EMAIL", font=theme_sumi.mono(9),
                 text_color="#8A8071", anchor="w").pack(fill="x", pady=(20, 4))
    entry_email = ctk.CTkEntry(inner, font=theme_sumi.ui(13), height=40,
                                corner_radius=0, fg_color="#0A0908",
                                border_width=1, border_color="#2A2622",
                                text_color="#E8DFCE",
                                placeholder_text="jules@atelier.co")
    entry_email.pack(fill="x")

    ligne_mdp = ctk.CTkFrame(inner, fg_color="transparent")
    ligne_mdp.pack(fill="x", pady=(16, 4))
    ctk.CTkLabel(ligne_mdp, text="MOT DE PASSE", font=theme_sumi.mono(9),
                 text_color="#8A8071", anchor="w").pack(side="left")
    lbl_oublie = ctk.CTkLabel(ligne_mdp, text="Oublié ?", font=theme_sumi.ui(11),
                               text_color="#E63946", cursor="hand2")
    lbl_oublie.pack(side="right")

    entry_mdp = ctk.CTkEntry(inner, font=theme_sumi.ui(13), height=40,
                              corner_radius=0, fg_color="#0A0908",
                              border_width=1, border_color="#2A2622",
                              text_color="#E8DFCE", show="•")
    entry_mdp.pack(fill="x")

    lbl_erreur = ctk.CTkLabel(inner, text="", font=theme_sumi.ui(11),
                               text_color="#E63946", wraplength=420, justify="left")
    lbl_erreur.pack(fill="x", pady=(10, 0))

    def _set_erreur(msg):
        lbl_erreur.configure(text=msg)

    def _appliquer_mode():
        connexion = _mode[0] == "connexion"
        btn_onglet_connexion.configure(
            fg_color="#E8DFCE" if connexion else "transparent",
            text_color="#0A0908" if connexion else "#8A8071",
            hover_color="#D8CFC0" if connexion else "#1F1B18")
        btn_onglet_creer.configure(
            fg_color="#E8DFCE" if not connexion else "transparent",
            text_color="#0A0908" if not connexion else "#8A8071",
            hover_color="#D8CFC0" if not connexion else "#1F1B18")
        lbl_oublie.configure(text="Oublié ?" if connexion else "")
        btn_principal.configure(text="Se connecter" if connexion else "Créer mon compte")
        _set_erreur("")

    def _choisir_mode(mode):
        _mode[0] = mode
        _appliquer_mode()

    def _restaurer_bouton():
        """Réactive le bouton principal sans effacer le message d'erreur
        (contrairement à _appliquer_mode, utilisé au changement d'onglet)."""
        btn_principal.configure(
            state="normal",
            text="Se connecter" if _mode[0] == "connexion" else "Créer mon compte")

    btn_onglet_connexion.configure(command=lambda: _choisir_mode("connexion"))
    btn_onglet_creer.configure(command=lambda: _choisir_mode("creer"))

    def _continuer():
        pop.destroy()
        if depuis_parametres:
            _rafraichir_code_fondateur_param()
        elif then_langue:
            _afficher_ecran_langue()

    def _valider():
        email = entry_email.get().strip()
        mdp = entry_mdp.get()
        if not email or "@" not in email:
            _set_erreur("Entre un email valide.")
            return
        if len(mdp) < 6:
            _set_erreur("Le mot de passe doit faire au moins 6 caractères.")
            return

        btn_principal.configure(state="disabled", text="…")
        pop.update_idletasks()
        try:
            if _mode[0] == "connexion":
                session = founder_backend.login(email, mdp)
            else:
                session = founder_backend.signup(email, mdp)
            access_token = session.get("access_token")
            if not access_token:
                _restaurer_bouton()
                _set_erreur("Vérifie ta boîte mail pour confirmer ton compte, "
                             "puis reviens te connecter.")
                return

            cfg = charger_config()
            code_local = cfg.get("founder_code") or _generer_code_fondateur()
            try:
                row = founder_backend.reserve_founder_slot(access_token, code_local)
                cfg["founder_code"] = row.get("founder_code", code_local)
                cfg["founder_slot_number"] = row.get("slot_number")
            except founder_backend.FounderAuthError as e:
                if e.code != "FOUNDER_SLOTS_FULL":
                    raise
                cfg.pop("founder_code", None)
                cfg.pop("founder_slot_number", None)
            cfg["founder_email"] = email
            cfg["founder_account_done"] = True
            sauvegarder_config(cfg)
            _continuer()
        except founder_backend.FounderAuthError as e:
            _restaurer_bouton()
            _set_erreur(_msg_auth_fr(e))

    btn_principal = ctk.CTkButton(
        inner, text="Se connecter", font=theme_sumi.ui(13, "bold"),
        fg_color="#E8DFCE", hover_color="#D8CFC0", text_color="#0A0908",
        corner_radius=0, height=44, command=_valider)
    btn_principal.pack(fill="x", pady=(16, 0))

    def _mot_de_passe_oublie(event=None):
        email = entry_email.get().strip()
        if not email or "@" not in email:
            _set_erreur("Entre ton email d'abord, puis clique sur \"Oublié ?\".")
            return
        try:
            founder_backend.recover_password(email)
            _set_erreur("Email de réinitialisation envoyé.")
        except founder_backend.FounderAuthError as e:
            _set_erreur(_msg_auth_fr(e))

    lbl_oublie.bind("<Button-1>", _mot_de_passe_oublie)

    sep = ctk.CTkFrame(inner, fg_color="transparent")
    sep.pack(fill="x", pady=(20, 0))
    ctk.CTkFrame(sep, height=1, fg_color="#2A2622").pack(side="left", fill="x", expand=True)
    ctk.CTkLabel(sep, text="OU", font=theme_sumi.mono(9), text_color="#8A8071"
                 ).pack(side="left", padx=10)
    ctk.CTkFrame(sep, height=1, fg_color="#2A2622").pack(side="left", fill="x", expand=True)

    ctk.CTkButton(
        inner, text="Continuer avec Google  ·  bientôt disponible",
        font=theme_sumi.ui(12), fg_color="transparent", hover_color="#141210",
        border_width=1, border_color="#2A2622", text_color="#5C574C",
        corner_radius=0, height=44, state="disabled"
    ).pack(fill="x", pady=(14, 0))

    lbl_skip = ctk.CTkLabel(
        inner, text="Annuler" if depuis_parametres else "Continuer sans compte",
        font=theme_sumi.ui(11), text_color="#8A8071", cursor="hand2")
    lbl_skip.pack(pady=(18, 0))

    def _passer(event=None):
        if depuis_parametres:
            pop.destroy()
            return
        cfg = charger_config()
        cfg["founder_account_done"] = True
        sauvegarder_config(cfg)
        _continuer()

    lbl_skip.bind("<Button-1>", _passer)

    ctk.CTkLabel(inner, text="En continuant, tu acceptes que ton email soit "
                 "utilisé uniquement pour gérer ton statut Premium.",
                 font=theme_sumi.ui(10), text_color="#5C574C",
                 wraplength=420, justify="center").pack(pady=(20, 0))

    _appliquer_mode()
    pop.lift()
    pop.attributes("-topmost", True)


# =====================================================================
#                           LANCEMENT
# =====================================================================
root.protocol("WM_DELETE_WINDOW", on_fermeture)

if not charger_config().get("founder_account_done", False):
    _afficher_ecran_compte_fondateur(then_langue=_PREMIER_LANCEMENT_LANGUE)
elif _PREMIER_LANCEMENT_LANGUE:
    _afficher_ecran_langue()

# Charger les apps détectées avant la restauration
load_detected_apps()

# Initialiser le badge grade sur l'accueil
update_grade_accueil()

# Reprise après redémarrage en Mode Hardcore
if "--reprendre-hardcore" in sys.argv and hc_reprendre_apres_redemarrage():
    _lancer_session_finale()
else:
    # Vérifier si une session était en cours avant de montrer l'accueil
    session_saved = charger_etat()
    if session_saved:
        restaurer_session(session_saved)
    else:
        naviguer_sidebar("accueil")

# Morning-Zero — après le rendu initial
root.after(500, verifier_morning_zero)

root.update_idletasks()
root.after(100, lambda: root.state("zoomed"))
root.mainloop()
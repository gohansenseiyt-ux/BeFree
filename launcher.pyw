"""
launcher.pyw — Point d'entrée "normal" de BeFree.

Vérifie que les dépendances (requirements.txt) sont installées, les installe
automatiquement si besoin (avec un écran de chargement), puis lance main.py.

N'utilise que la bibliothèque standard (tkinter, subprocess, importlib) pour
pouvoir tourner même sur un clone tout frais où rien n'est encore installé.
"""
import importlib
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
sys.path.insert(0, BASE_DIR)


def _pythonw_executable():
    """Chemin vers pythonw.exe (sans console), quel que soit l'exe qui a
    lancé ce script (python.exe ou pythonw.exe)."""
    exe = sys.executable
    candidate = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(candidate):
        return candidate
    return exe

import theme_sumi  # necessite uniquement ctypes/os (stdlib) -> safe ici

# (nom pip, module a importer pour verifier la presence)
REQUIRED = [
    ("customtkinter", "customtkinter"),
    ("Pillow",        "PIL"),
    ("psutil",        "psutil"),
    ("pystray",       "pystray"),
    ("plyer",         "plyer"),
    ("pywin32",       "win32api"),
    ("keyring",       "keyring"),
    ("matplotlib",    "matplotlib"),
    ("numpy",          "numpy"),
]

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class Launcher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("BeFree")
        self.configure(bg=theme_sumi.SUMI)
        self.resizable(False, False)
        self._center(480, 360)
        theme_sumi.register_fonts()

        wordmark = tk.Frame(self, bg=theme_sumi.SUMI)
        wordmark.pack(pady=(30, 6))
        tk.Label(wordmark, text="BeFree", font=(theme_sumi.FONT_SERIF, 28),
                 fg=theme_sumi.INK, bg=theme_sumi.SUMI).pack(side="left")
        tk.Label(wordmark, text=".", font=(theme_sumi.FONT_SERIF, 28),
                 fg=theme_sumi.HANKO, bg=theme_sumi.SUMI).pack(side="left")

        self.lbl_status = tk.Label(self, text="Vérification de l'installation…",
                                    font=(theme_sumi.FONT_MONO, 10),
                                    fg=theme_sumi.MUTED, bg=theme_sumi.SUMI)
        self.lbl_status.pack(pady=(0, 16))

        self.progress = ttk.Progressbar(self, mode="indeterminate", length=420)
        self.progress.pack(pady=(0, 16))

        log_frame = tk.Frame(self, bg=theme_sumi.SURFACE, highlightthickness=1,
                              highlightbackground=theme_sumi.RULE)
        log_frame.pack(padx=28, fill="both", expand=True)
        self.log = tk.Text(log_frame, height=9, bg=theme_sumi.SURFACE,
                            fg=theme_sumi.INK_2, font=(theme_sumi.FONT_MONO, 9),
                            bd=0, wrap="word", state="disabled",
                            insertbackground=theme_sumi.INK)
        self.log.pack(fill="both", expand=True, padx=10, pady=10)

        self.btn_close = tk.Button(self, text="Fermer", command=self.destroy,
                                    bg=theme_sumi.SURFACE, fg=theme_sumi.INK,
                                    activebackground=theme_sumi.RULE,
                                    activeforeground=theme_sumi.INK,
                                    bd=0, relief="flat", padx=18, pady=6,
                                    font=(theme_sumi.FONT_UI, 10))

        self.after(200, self.start)

    def _center(self, w, h):
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x, y = (sw - w) // 2, (sh - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    # ── UI helpers (toujours appelés depuis le thread principal via .after) ──
    def _log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _status(self, text, color=None):
        self.lbl_status.configure(text=text, fg=color or theme_sumi.MUTED)

    def _error(self, message):
        self.progress.stop()
        self._status(message, theme_sumi.HANKO)
        self.btn_close.pack(pady=(0, 20))

    # ── Logique (tourne dans un thread pour ne pas geler l'UI) ──
    def start(self):
        self.progress.start(12)
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        missing = []
        for pip_name, import_name in REQUIRED:
            try:
                importlib.import_module(import_name)
                self.after(0, self._log, f"✓ {pip_name}")
            except ImportError:
                missing.append(pip_name)
                self.after(0, self._log, f"… {pip_name} manquant")

        if missing:
            self.after(0, self._status, "Installation des dépendances manquantes…")
            self.after(0, self._log, "")
            self.after(0, self._log, "Installation via pip — ça peut prendre quelques minutes...")
            if not self._pip_install():
                self.after(0, self._error,
                           "Échec de l'installation. Vérifie ta connexion internet et réessaie.")
                return
            # Re-vérification après installation
            still_missing = []
            for pip_name, import_name in missing_pairs(missing):
                try:
                    importlib.invalidate_caches()
                    importlib.import_module(import_name)
                except ImportError:
                    still_missing.append(pip_name)
            if still_missing:
                self.after(0, self._error,
                           "Certaines dépendances n'ont pas pu s'installer : "
                           + ", ".join(still_missing))
                return

        self.after(0, self._status, "Lancement de BeFree…")
        self.after(300, self._launch_app)

    def _pip_install(self):
        try:
            proc = subprocess.Popen(
                [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
                cwd=BASE_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, creationflags=CREATE_NO_WINDOW,
            )
            for line in proc.stdout:
                self.after(0, self._log, line.rstrip())
            proc.wait()
            return proc.returncode == 0
        except Exception as e:
            self.after(0, self._log, f"Erreur : {e}")
            return False

    def _launch_app(self):
        self.progress.stop()
        try:
            subprocess.Popen([_pythonw_executable(), os.path.join(BASE_DIR, "main.py")],
                              cwd=BASE_DIR, creationflags=CREATE_NO_WINDOW)
        except Exception as e:
            self._error(f"Impossible de lancer BeFree : {e}")
            return
        self.after(600, self.destroy)


def missing_pairs(missing_names):
    return [pair for pair in REQUIRED if pair[0] in missing_names]


if __name__ == "__main__":
    Launcher().mainloop()

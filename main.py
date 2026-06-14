import os
import shutil
import time
import threading
import customtkinter as ctk
import psutil

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

FICHIER = "test.txt"
DOSSIER_QUARANTAINE = "Quarantaine"
LOGICIELS_SURVEILLES = {"Discord.exe", "RobloxStudio.exe"}

# --- FENÊTRE PRINCIPALE ---
root = ctk.CTk()
root.title("Hardcore Focus V1")
root.geometry("480x360")
root.resizable(False, False)

timer_active = False
temps_restant = 0
victory_printed = False

# --- WIDGETS ---
label_titre = ctk.CTkLabel(root, text="Hardcore Focus V1", font=("Arial", 28, "bold"))
label_titre.pack(pady=(20, 10))

entry_minutes = ctk.CTkEntry(root, placeholder_text="Minutes de travail", width=200, justify="center",
                              font=("Arial", 16))
entry_minutes.pack(pady=5)

label_chrono = ctk.CTkLabel(root, text="--:--", font=("Arial", 48, "bold"))
label_chrono.pack(pady=15)

label_statut = ctk.CTkLabel(root, text="", font=("Arial", 14))
label_statut.pack(pady=(0, 10))

# --- MISE EN QUARANTAINE ---
def mettre_en_quarantaine():
    if os.path.exists(FICHIER):
        os.makedirs(DOSSIER_QUARANTAINE, exist_ok=True)
        destination = os.path.join(DOSSIER_QUARANTAINE, FICHIER)
        shutil.move(FICHIER, destination)

# --- TRICHE DÉTECTÉE ---
def declencher_triche(nom_process):
    global timer_active
    timer_active = False
    mettre_en_quarantaine()

    label_chrono.configure(text="TRICHE !", text_color="red")
    label_statut.configure(text=f"{nom_process} détecté — fichier envoyé en quarantaine",
                           text_color="red")

    # Bouton en rouge
    btn_demarrer.configure(text="SESSION ÉCHOUÉE", fg_color="#8b0000", hover_color="#5a0000")

    # Assombrir la fenêtre
    root.configure(fg_color="#1a0000")
    label_titre.configure(text_color="#aa0000")

# --- VICTOIRE ---
def victoire():
    global timer_active, victory_printed
    timer_active = False
    if victory_printed:
        return
    victory_printed = True
    label_chrono.configure(text="VALIDÉ !", text_color="green")
    label_statut.configure(text="Session terminée. Tu as gagné le droit de jouer.", text_color="green")

    # Bouton en bleu
    btn_demarrer.configure(text="VICTOIRE !", fg_color="#005a9e", hover_color="#003f6e")

# --- BOUCLE DE SURVEILLANCE (appelée via after) ---
def tick():
    global temps_restant, timer_active, victory_printed

    if not timer_active:
        return

    # Surveillance des processus
    for process in psutil.process_iter(["name"]):
        try:
            nom = process.info["name"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if nom in LOGICIELS_SURVEILLES:
            declencher_triche(nom)
            return

    # Mise à jour du chrono
    temps_restant -= 1  # on tick toutes les 1 seconde

    if temps_restant <= 0:
        temps_restant = 0
        victoire()
        return

    mins = temps_restant // 60
    secs = temps_restant % 60
    label_chrono.configure(text=f"{mins:02d}:{secs:02d}")

    # Programmer le prochain tick dans 1 seconde
    root.after(1000, tick)

# --- DÉMARRAGE ---
def demarrer():
    global timer_active, temps_restant, victory_printed

    if timer_active:
        return

    try:
        minutes = int(entry_minutes.get())
        if minutes <= 0:
            raise ValueError
    except ValueError:
        label_statut.configure(text="Entre un nombre valide de minutes.", text_color="orange")
        return

    timer_active = True
    victory_printed = False
    temps_restant = minutes * 60

    # Bouton en gris "en cours"
    btn_demarrer.configure(text="FOCUS EN COURS...", fg_color="#555555", hover_color="#444444")

    mins = temps_restant // 60
    secs = temps_restant % 60
    label_chrono.configure(text=f"{mins:02d}:{secs:02d}", text_color=("white", "white"))
    label_statut.configure(text=f"Focus actif — {minutes} minute(s)", text_color="lightblue")
    root.configure(fg_color=("gray90", "gray10"))
    label_titre.configure(text_color=("black", "white"))

    # Lancer le premier tick dans 1 seconde
    root.after(1000, tick)

# --- BOUTON ---
btn_demarrer = ctk.CTkButton(root, text="DÉMARRER LE FOCUS", font=("Arial", 18, "bold"),
                              height=50, width=260, corner_radius=8,
                              fg_color="#2b7a2b", hover_color="#1f5f1f",
                              command=demarrer)
btn_demarrer.pack(pady=10)

# --- INTERCEPTION DE LA FERMETURE ---
def on_fermeture():
    if timer_active:
        label_statut.configure(text="Fermeture interdite pendant le focus !", text_color="orange")
        return  # ne fait rien, la fenêtre reste ouverte
    root.destroy()

root.protocol("WM_DELETE_WINDOW", on_fermeture)

# --- LANCEMENT DE LA FENÊTRE ---
root.mainloop()
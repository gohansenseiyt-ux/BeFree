"""Supprime le bloc BeFree du fichier hosts. Lancer en tant qu'administrateur."""
import sys

HOSTS_FILE = r"C:\Windows\System32\drivers\etc\hosts"
MARKER_START = "# ==BeFree-block-start=="
MARKER_END   = "# ==BeFree-block-end=="

try:
    with open(HOSTS_FILE, "r", encoding="utf-8", errors="ignore") as f:
        lignes = f.readlines()

    propres = []
    dans_bloc = False
    for ligne in lignes:
        if MARKER_START in ligne:
            dans_bloc = True
            continue
        if MARKER_END in ligne:
            dans_bloc = False
            continue
        if not dans_bloc:
            propres.append(ligne)

    with open(HOSTS_FILE, "w", encoding="utf-8") as f:
        f.writelines(propres)

    print("✓ Sites débloqués. Vide le cache DNS avec : ipconfig /flushdns")
except PermissionError:
    print("✗ Droits insuffisants. Relance ce script en tant qu'administrateur.")
    sys.exit(1)
except Exception as e:
    print(f"✗ Erreur : {e}")
    sys.exit(1)

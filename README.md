# BeFree

> Une application de focus **hardcore** pour Windows. Pas de gamification molle, pas de rappels gentils — un dojo numérique où tu signes un contrat avec toi-même et où les distractions sont mises en quarantaine.

BeFree bloque tes applications et sites distrayants pendant tes sessions de travail. En mode **Hardcore**, une fois la session lancée, tu ne peux plus l'arrêter avant la fin : ni pause, ni annulation, et les `.exe` distrayants sont déplacés en quarantaine sur le disque.

L'interface suit une direction visuelle **« Sumi »** (encre de nuit, dojo/kendo) : fond noir encre, typographie éditoriale, et un sceau de cinabre rouge réservé aux moments d'engagement.

---

## ✨ Fonctionnalités

- **4 régimes de session**
  - **Libre** — une durée, du tracking, aucun blocage.
  - **Pomodoro** — cycles de 25 min focus / 5 min pause.
  - **Infini** — le chronomètre monte au lieu de descendre.
  - **Quarantaine (Hardcore)** — blocage irrévocable, multi-jours, `.exe` en quarantaine.
- **Contrat de travail** — tu écris ton objectif et tu signes avant de commencer.
- **Liste blanche d'applications** et **blocage de sites web** (via le fichier `hosts`).
- **Système de grades** (Deep Work Score) — 6 rangs du dojo, de *Novice* à *BeFree*.
- **Statistiques** — temps passé par application, graphiques, séries de jours.
- **Mode Morning-Zero** — bloque le PC 30 min dès l'allumage entre 4h et 12h.
- **Physical Lock** — seule une clé USB physique peut ouvrir les réglages / autoriser un abandon.
- **Protection par mot de passe** des paramètres.
- **Persistance de session** — la session se réactive après un redémarrage (impossible de tricher).
- **100 % local** — aucune connexion, aucun compte, aucune donnée envoyée nulle part.

---

## 🖥️ Prérequis

- **Windows 10 / 11** (l'app utilise des API Windows : `pywin32`, `ctypes`).
- **Python 3.11+**
- Certaines fonctions (blocage de sites, mode Hardcore) nécessitent de lancer l'app **en administrateur**.

---

## 🚀 Installation

### Option 1 — Double-clic (le plus simple)

1. Installe [Python](https://www.python.org/downloads/) si ce n'est pas déjà fait (coche **"Add python.exe to PATH"** pendant l'installation).
2. Clone ou télécharge ce dépôt.
3. Double-clique sur **`BeFree.bat`**.

Un écran de chargement vérifie automatiquement les dépendances et installe celles qui manquent (via pip), puis lance l'application — comme une appli normale, sans étape manuelle. Les lancements suivants sont quasi instantanés (tout est déjà installé).

### Option 2 — Ligne de commande

```bash
# 1. Cloner le dépôt
git clone https://github.com/gohansenseiyt-ux/BeFree.git
cd BeFree

# 2. (Recommandé) créer un environnement virtuel
python -m venv .venv
.venv\Scripts\activate

# 3. Installer les dépendances
pip install -r requirements.txt

# 4. Lancer
python main.py
```

---

## 📁 Structure du projet

| Fichier | Rôle |
|---|---|
| `main.py` | Application principale (UI + logique). |
| `launcher.pyw` | Lanceur : vérifie/installe les dépendances puis démarre `main.py`. |
| `theme_sumi.py` | Palette de couleurs et polices (design system « Sumi »). |
| `ui_elements.py` | Tableau de bord des statistiques. |
| `chart_renderer.py` | Rendu des graphiques (matplotlib). |
| `stats_manager.py` | Gestion des sessions et agrégats statistiques. |
| `hc_integrity.py` | Signature d'intégrité de l'état Hardcore (anti-triche). |
| `fonts/` | Polices embarquées (Cormorant Garamond, JetBrains Mono). |
| `icons/` | Icône de l'application (`befree.ico`). |

Les fichiers de données locales (`config.json`, `stats.json`, etc.) sont générés au premier lancement et **ne sont pas versionnés**.

---

## 🎨 Design

La refonte visuelle « Sumi » a été conçue avec [Claude Design](https://claude.ai/design), puis implémentée dans l'application.

---

## ⚠️ Avertissement

BeFree modifie temporairement le fichier `hosts` de Windows et peut fermer / déplacer des applications pendant une session. Le mode Hardcore est **volontairement difficile à contourner** — c'est le but. Utilise-le en connaissance de cause.

---

## 📄 Licence

Distribué sous licence **MIT**. Voir le fichier [LICENSE](LICENSE).

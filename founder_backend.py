"""founder_backend.py — Compte Fondateur : appels HTTP à Supabase (Auth + RPC)
pour la reservation atomique des 500 places fondateur. Aucune donnee de session
Tk ici, uniquement des fonctions reseau pures (facilite les tests / le mock)."""
import requests

SUPABASE_URL = "https://wevbrzbandmqucflizxn.supabase.co"
SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6IndldmJyemJhbmRtcXVjZmxpenhuIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODIzNzA4ODMsImV4cCI6MjA5Nzk0Njg4M30."
    "OyBmsZIm2UwnW5utjZwcxd1HXIeZpjF9T-Zru_I3_Wk"
)

_TIMEOUT = 10


class FounderAuthError(Exception):
    """code: identifiant machine de l'erreur (ex. FOUNDER_SLOTS_FULL,
    invalid_credentials, user_already_exists) ; message: texte a afficher."""
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _headers(access_token=None):
    return {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {access_token or SUPABASE_ANON_KEY}",
        "Content-Type": "application/json",
    }


def _post(path, json_body, access_token=None):
    try:
        r = requests.post(f"{SUPABASE_URL}{path}", headers=_headers(access_token),
                           json=json_body, timeout=_TIMEOUT)
    except requests.exceptions.RequestException:
        raise FounderAuthError("network_error", "Impossible de contacter le serveur. Vérifie ta connexion internet.")
    try:
        data = r.json()
    except ValueError:
        data = {}
    return r, data


def signup(email, password):
    r, data = _post("/auth/v1/signup", {"email": email, "password": password})
    if r.status_code >= 400:
        raise FounderAuthError(data.get("error_code") or data.get("code"),
                                data.get("msg") or data.get("error_description") or "Inscription impossible.")
    return data


def login(email, password):
    r, data = _post("/auth/v1/token?grant_type=password", {"email": email, "password": password})
    if r.status_code >= 400:
        raise FounderAuthError(data.get("error_code") or data.get("code"),
                                data.get("msg") or data.get("error_description") or "Email ou mot de passe incorrect.")
    return data


def recover_password(email):
    r, data = _post("/auth/v1/recover", {"email": email})
    if r.status_code >= 400:
        raise FounderAuthError(data.get("error_code"), data.get("msg") or "Envoi de l'email impossible.")


def reserve_founder_slot(access_token, founder_code):
    r, data = _post("/rest/v1/rpc/reserve_founder_slot", {"p_founder_code": founder_code}, access_token)
    if r.status_code >= 400:
        message = data.get("message", "") if isinstance(data, dict) else ""
        if "FOUNDER_SLOTS_FULL" in message:
            raise FounderAuthError("FOUNDER_SLOTS_FULL", "Les 500 places fondateur sont toutes prises.")
        raise FounderAuthError(data.get("code") if isinstance(data, dict) else None,
                                message or "Réservation de la place fondateur impossible.")
    return data


def slots_remaining():
    """Retourne le nombre de places restantes, ou None si injoignable
    (l'appelant doit gérer ce cas sans bloquer l'écran)."""
    try:
        r, data = _post("/rest/v1/rpc/founder_slots_remaining", {})
    except FounderAuthError:
        return None
    if r.status_code >= 400:
        return None
    return data

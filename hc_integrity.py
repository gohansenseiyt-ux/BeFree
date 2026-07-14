"""
hc_integrity.py — Signature HMAC de l'état Mode Hardcore.

Empêche de désactiver le Mode Hardcore en éditant hardcore_state.json à la main :
le secret vit dans le trousseau Windows (Credential Manager), pas en clair dans
le dossier de l'app. Utilisé par main.py aussi bien en écriture (session normale)
qu'en vérification (rôle --watchdog-role, le même exécutable relancé comme gardien).
"""
import hashlib
import hmac
import json
import secrets

_SERVICE = "BeFree"
_USER = "hc_integrity_secret"


def _secret() -> str:
    try:
        import keyring
        val = keyring.get_password(_SERVICE, _USER)
        if val:
            return val
        val = secrets.token_hex(32)
        keyring.set_password(_SERVICE, _USER, val)
        return val
    except Exception:
        # Trousseau indisponible : secret dérivé de la machine (mieux que rien,
        # au moins il ne vit pas en clair à côté du fichier qu'il protège).
        import platform
        return hashlib.sha256(platform.node().encode("utf-8", "ignore")).hexdigest()


def _canon(etat: dict) -> bytes:
    sans_sig = {k: v for k, v in etat.items() if k != "sig"}
    return json.dumps(sans_sig, sort_keys=True, ensure_ascii=False).encode("utf-8")


def signer(etat: dict) -> dict:
    """Retourne une copie de etat avec le champ 'sig' calculé (HMAC-SHA256)."""
    etat = dict(etat)
    etat["sig"] = hmac.new(_secret().encode("utf-8"), _canon(etat), hashlib.sha256).hexdigest()
    return etat


def verifier(etat: dict) -> bool:
    """True si la signature du dict correspond à son contenu (fichier non modifié depuis l'écriture)."""
    if not etat or "sig" not in etat:
        return False
    attendu = hmac.new(_secret().encode("utf-8"), _canon(etat), hashlib.sha256).hexdigest()
    try:
        return hmac.compare_digest(attendu, etat["sig"])
    except Exception:
        return False

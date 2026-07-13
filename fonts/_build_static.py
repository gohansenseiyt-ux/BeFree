"""
Instancie des coupes statiques a partir des variable fonts telechargees,
et renomme chaque instance pour qu'elle soit adressable comme une famille
Windows distincte (ex: "Cormorant Garamond Medium").
A executer une seule fois (build-time), pas au runtime de l'appli.
"""
import os
from fontTools import varLib
from fontTools.varLib.instancer import instantiateVariableFont
from fontTools.ttLib import TTFont

HERE = os.path.dirname(os.path.abspath(__file__))

JOBS = [
    # (source, axes, family_name, subfamily_name, out_filename)
    ("CormorantGaramond-Variable.ttf", {"wght": 500}, "Cormorant Garamond Medium", "Regular", "CormorantGaramond-Medium.ttf"),
    ("CormorantGaramond-Variable.ttf", {"wght": 600}, "Cormorant Garamond SemiBold", "Regular", "CormorantGaramond-SemiBold.ttf"),
    ("CormorantGaramond-Variable.ttf", {"wght": 400}, "Cormorant Garamond", "Regular", "CormorantGaramond-Regular.ttf"),
    ("JetBrainsMono-Variable.ttf", {"wght": 400}, "JetBrains Mono", "Regular", "JetBrainsMono-Regular.ttf"),
    ("JetBrainsMono-Variable.ttf", {"wght": 500}, "JetBrains Mono Medium", "Regular", "JetBrainsMono-Medium.ttf"),
    ("JetBrainsMono-Variable.ttf", {"wght": 700}, "JetBrains Mono", "Bold", "JetBrainsMono-Bold.ttf"),
]

NAME_IDS = {
    1: None,   # family
    2: None,   # subfamily
    4: None,   # full name
    6: None,   # postscript name
    16: None,  # typographic family
    17: None,  # typographic subfamily
}


def set_names(font, family, subfamily):
    name_tbl = font["name"]
    full = f"{family} {subfamily}".strip() if subfamily != "Regular" else family
    ps = full.replace(" ", "")
    for plat_id, enc_id, lang_id in [(3, 1, 0x409), (1, 0, 0)]:
        name_tbl.setName(family, 1, plat_id, enc_id, lang_id)
        name_tbl.setName(subfamily, 2, plat_id, enc_id, lang_id)
        name_tbl.setName(full, 4, plat_id, enc_id, lang_id)
        name_tbl.setName(ps, 6, plat_id, enc_id, lang_id)
        name_tbl.setName(family, 16, plat_id, enc_id, lang_id)
        name_tbl.setName(subfamily, 17, plat_id, enc_id, lang_id)
    if "STAT" in font:
        del font["STAT"]


for src, axes, family, subfamily, out in JOBS:
    path = os.path.join(HERE, src)
    font = TTFont(path)
    instantiateVariableFont(font, axes, inplace=True)
    set_names(font, family, subfamily)
    out_path = os.path.join(HERE, out)
    font.save(out_path)
    print(f"OK  {src} {axes} -> {out}  [{family} / {subfamily}]")

# -*- coding: utf-8 -*-
import json, glob, os
from ingestion_silver import pipeline

archivos = sorted(glob.glob("/mnt/user-data/uploads/*.json"))
print(f"Blobs de prueba: {len(archivos)}\n")
print(f"{'FAMILIA':<18}{'VIG':<5}{'REL':<6}{'TIPO SEÑAL':<22}{'ENTIDAD':<26}FLAGS")
print("-"*120)

conteo_fam = {}
for a in archivos:
    blob = json.load(open(a, encoding="utf-8"))
    r = pipeline.procesar(blob)
    if r.estado != "ok":
        print(f"[{r.estado}] {os.path.basename(a)[:12]}  {r.motivo}")
        continue
    c = r.canonico
    conteo_fam[r.familia] = conteo_fam.get(r.familia, 0) + 1
    vig = "sí" if c["vigencia"]["vigente"] else "NO"
    rel = c["relevancia"]["score"]
    passa = "" if c["relevancia"]["pasa_umbral"] else "✗"
    tipo = c["clasificacion"]["tipo_senal"]
    ent = (c["entidad_monitoreada"] or "—")[:24]
    flags = ", ".join(c["calidad"]["flags"]) or "-"
    print(f"{r.familia:<18}{vig:<5}{str(rel)+passa:<6}{tipo:<22}{ent:<26}{flags}")

print("\n=== Familias detectadas ===")
for k,v in sorted(conteo_fam.items()):
    print(f"  {k:<18} {v}")
print(f"  TOTAL familias distintas: {len(conteo_fam)}")
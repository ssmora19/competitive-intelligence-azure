import os, json, sys

with open("local.settings.json") as f:
    settings = json.load(f)
for k, v in settings["Values"].items():
    os.environ[k] = v

sys.path.insert(0, ".")
from shared.secop_client import SecopClient

secop = SecopClient()

print("=== Contratos relevantes para TAK — últimos 30 días ===")
contratos = secop.get_contratos_tak(dias_recientes=90, limite=5)
print(f"Obtenidos: {len(contratos)}")
for c in contratos[:3]:
    print(f"  - {c.get('nombre_entidad','')} | {c.get('descripcion_del_proceso','')[:60]}")
    print(f"    Valor: ${c.get('valor_del_contrato',0)} | Proveedor: {c.get('proveedor_adjudicado','')}")

print("\n=== Licitaciones abiertas relevantes para TAK ===")
procesos = secop.get_procesos_activos_tak(limite=5)
print(f"Obtenidos: {len(procesos)}")
for p in procesos[:3]:
    print(f"  - {p.get('entidad','')} | {p.get('descripci_n_del_procedimiento','')[:60]}")
    print(f"    Valor base: ${p.get('precio_base','')} | Estado: {p.get('estado_de_apertura_del_proceso','')}")
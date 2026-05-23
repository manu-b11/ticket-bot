import json
from ollama_client import analizar_ticket

CASES = [
    {
        "name": "Caída masiva de internet",
        "input": "No hay internet en toda la empresa desde hace 30 minutos. Ningún usuario puede trabajar.",
        "expected_prioridad": "Alta",
        "expected_tipo": "Incidente",
    },
    {
        "name": "Fallo de correo (usuario no puede enviar)",
        "input": "No se pueden enviar correos desde Outlook. Recibir sí, pero enviar da error 0x800.",
        "expected_prioridad": "Media",
        "expected_tipo": "Incidente",
    },
    {
        "name": "Solicitud de acceso",
        "input": "Necesito que me habiliten acceso a la VPN para un nuevo colaborador.",
        "expected_prioridad": "Baja",
        "expected_tipo": "Requerimiento",
    },
]


def main():
    ok = 0
    for c in CASES:
        out = analizar_ticket(c["input"])
        passed = (out["prioridad"] == c["expected_prioridad"] and out["tipo"] == c["expected_tipo"])
        status = "✅" if passed else "❌"
        print(f"{status} {c['name']}")
        print("  Input:", c["input"])
        print("  Output:", json.dumps(out, ensure_ascii=False))
        print("  Expected:", {"prioridad": c["expected_prioridad"], "tipo": c["expected_tipo"]})
        print()
        ok += int(passed)

    print(f"Resultado: {ok}/{len(CASES)} casos pasaron")


if __name__ == "__main__":
    main()

"""Falla si se intenta commitear material privado: Context/, credenciales, pesos o corridas.

Es la segunda barrera después de `.gitignore` (alguien puede forzar con `git add -f`).
Uso: python scripts/bloquear_privados.py <archivo>...
"""

from __future__ import annotations

import sys
from pathlib import PurePosixPath

CARPETAS_PRIVADAS = {"context", "claude outputs", "modelos", "corridas"}
EXTENSIONES_PRIVADAS = {".pem", ".key", ".gguf", ".safetensors", ".onnx", ".bin"}


def motivo(ruta: str) -> str | None:
    """Devuelve por qué no se puede commitear `ruta`, o None si está permitida."""
    p = PurePosixPath(ruta.replace("\\", "/"))
    partes = [x.lower() for x in p.parts]
    if partes and partes[0] in CARPETAS_PRIVADAS:
        return f"carpeta privada '{p.parts[0]}/'"
    if "corridas" in partes[:-1]:
        return "resultados de corridas de corrección"
    nombre = p.name.lower()
    if nombre == ".env" or (nombre.startswith(".env.") and nombre != ".env.example"):
        return "archivo de entorno con posibles credenciales"
    if nombre == "settings.local.json" and ".claude" in partes:
        return "configuración local de Claude Code"
    if p.suffix.lower() in EXTENSIONES_PRIVADAS:
        return f"archivo de claves o pesos ({p.suffix})"
    return None


def main(argv: list[str]) -> int:
    fallos = [(a, m) for a in argv if (m := motivo(a))]
    for ruta, razon in fallos:
        print(f"BLOQUEADO: {ruta}: {razon}", file=sys.stderr)
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

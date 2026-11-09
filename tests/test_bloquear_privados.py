"""La barrera que impide commitear Context/, credenciales y pesos."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_RUTA = Path(__file__).resolve().parent.parent / "scripts" / "bloquear_privados.py"
_spec = importlib.util.spec_from_file_location("bloquear_privados", _RUTA)
bloquear = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bloquear)


@pytest.mark.parametrize(
    "ruta",
    [
        "Context/PLAN_MAESTRO.md",
        "context/notas.md",
        "Context/Clon del REPO/correccion/rubrica.yaml",
        "Claude outputs/PLAN_MAESTRO.md",
        ".env",
        ".env.local",
        ".claude/settings.local.json",
        "modelos/small/model.bin",
        "evaluacion/corridas/x/informe.md",
        "docker/clave.pem",
        "pesos/qwen.gguf",
        "Context\\handoff\\x.md",
    ],
)
def test_bloquea(ruta):
    assert bloquear.motivo(ruta) is not None


@pytest.mark.parametrize(
    "ruta",
    [
        ".env.example",
        ".gitignore",
        "src/sistema/config.py",
        "docker-compose.yml",
        "tests/test_contrato_importa.py",
        "docs/contexto.md",
    ],
)
def test_permite(ruta):
    assert bloquear.motivo(ruta) is None


def test_main_devuelve_codigo():
    assert bloquear.main(["src/a.py"]) == 0
    assert bloquear.main(["src/a.py", "Context/x"]) == 1

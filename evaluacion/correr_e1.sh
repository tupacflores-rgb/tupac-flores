#!/bin/sh
# Corre el corrector oficial (copia local en Context/) sobre los escenarios de la Entrega 1 y resume el resultado.
# La carpeta de la corrida lleva el hash del commit; no se versiona (evaluacion/corridas/ está ignorada).
#
#   sh evaluacion/correr_e1.sh
#
# Requiere la planta construible en Context/Clon del REPO y un venv solo para el corrector, dentro de Context/:
#   python -m venv Context/.venv-correccion && Context/.venv-correccion/Scripts/python -m pip install httpx pyyaml
set -e
cd "$(git rev-parse --show-toplevel)"

if [ -x "Context/.venv-correccion/Scripts/python.exe" ]; then
  PY="Context/.venv-correccion/Scripts/python.exe"
else
  PY="Context/.venv-correccion/bin/python"
fi
COMMIT=$(git rev-parse --short HEAD)
SALIDA="evaluacion/corridas/e1-$COMMIT-$(date +%H%M%S)"

"$PY" "Context/Clon del REPO/correccion/corregir.py" --repo . --alumno "Rolando Flores" \
  --escenarios martes-tranquilo fuga-sin-enlace --salida "$SALIDA"
"$PY" evaluacion/resumen.py "$SALIDA"
echo "Corrida guardada en $SALIDA (commit $COMMIT)"

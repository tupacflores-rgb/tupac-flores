# tupac-flores · Academia Novit 2026 · TP Planta de inyección

Sistema que se conecta a la planta simulada de Novit: recibe las notas de voz del operador, arma el parte por la
tablet, detecta condiciones inseguras y publica eventos. Entrega 1: sistema conectado, parte por voz y detección.

> **Estado:** en desarrollo (rama `entrega-1`). Este README es un esqueleto; se completa en V8.

## Dependencia provisional del contrato

`planta_contrato` se instala con pip desde un commit fijo de `NovitSoftware/academia2026-tp`:

```
planta-contrato @ git+https://github.com/NovitSoftware/academia2026-tp@ab65b95e911731351dba395817a74a8cc60da2b2#subdirectory=contrato
```

**Provisional, pendiente de confirmación de Novit.** La URL de `docs/contrato.md`
(`NovitSoftware/academia2026-planta`) devuelve 404. Se eligió este repositorio porque es público y su contenido
coincide byte a byte con la copia oficial de la planta (234 de 234 archivos). Si Novit indica otra URL o commit,
se cambia una sola línea de `pyproject.toml`.

## Desarrollo

Las herramientas (ruff, pytest, pre-commit) corren dentro de un contenedor, no en el host:

```bash
docker compose -f docker/compose.dev.yml run --rm dev pytest
docker compose -f docker/compose.dev.yml run --rm dev ruff check .
```

## Estructura

```
src/sistema/     código del sistema (config, integraciones, detección, voz, persistencia)
tests/           pruebas unitarias, de contrato e infraestructura
docker/          Dockerfiles y compose de desarrollo
```

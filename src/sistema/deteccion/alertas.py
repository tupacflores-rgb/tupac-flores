"""Alerta de seguridad: el evento `AlertaSeguridad` y el aviso de emergencia en la tablet.

Los ids dependen solo de la nota (`EV-SEG-{nota_id}`, `TAB-SEG-{nota_id}`): publicar dos veces es el mismo
mensaje. La alerta no espera a que el operador confirme ningún parte.
"""

from __future__ import annotations

import logging

from planta_contrato import EventoSistema, MensajeTablet, NotaRecorrida, TipoEvento, Topicos

from ..persistencia.salida import Salida
from .seguridad import Deteccion

log = logging.getLogger("sistema.alertas")


def id_evento(nota_id: str) -> str:
    return f"EV-SEG-{nota_id}"


def id_aviso(nota_id: str) -> str:
    return f"TAB-SEG-{nota_id}"


def construir(nota: NotaRecorrida, d: Deteccion) -> tuple[EventoSistema, MensajeTablet]:
    donde = f" en {d.equipo}" if d.equipo else ""
    evento = EventoSistema(
        id=id_evento(nota.id),
        ts=nota.ts,
        tipo=TipoEvento.alerta_seguridad,
        equipo=d.equipo,
        origen="parte",
        nota_ids=[nota.id],
        resumen=f"Condición insegura {d.codigo}: {d.descripcion}{donde}. Detectada en la nota {nota.id}.",
    )
    aviso = MensajeTablet(
        id=id_aviso(nota.id),
        ts=nota.ts,
        nota_ids=[nota.id],
        texto=(
            f"ALERTA DE SEGURIDAD: {d.descripcion}{donde}. La alerta ya fue emitida. "
            "Mantenete a distancia y avisá al supervisor."
        ),
        espera_respuesta=False,
    )
    return evento, aviso


def emitir(salida: Salida, nota: NotaRecorrida, d: Deteccion) -> bool:
    """Registra y publica la alerta y el aviso. Devuelve True si salieron los dos."""
    evento, aviso = construir(nota, d)
    ok = salida.emitir_lote([(evento.id, Topicos.EVENTOS, evento), (aviso.id, Topicos.TABLET, aviso)])
    log.warning("ALERTA %s en %s (%s): publicada=%s", d.codigo, nota.id, d.frase, ok)
    return ok

"""Diálogo con el operador en la tablet: un borrador del parte por nota, confirmación y correcciones.

Política (Plan Maestro §10.1, derivada de la rúbrica):
  - Nota clara: UN borrador con filas y sin opciones.
  - Equipo ambiguo ("la bomba"): primero una pregunta con opciones y sin filas; el borrador viene después.
  - Valor ambiguo ("dieciocho"): borrador con el candidato marcado provisional y las dos opciones.
  - Corrección del operador: se aplica y se muestra un borrador nuevo (hasta 3 borradores por nota).
  - D8 estricta: sin confirmación del operador el parte queda en Borrador, sin eventos.
  - Confirmación: aviso «guardado» (sin esperar respuesta) y recién ahí los eventos.
  - Nota insegura: la alerta ya salió por otro camino; acá solo se anota la observación del equipo real.

Cada mensaje de tablet lleva un id nuevo `TAB-{nota_id}-{n}`: la planta ignora un id repetido.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime

from planta_contrato import MensajeTablet, NotaRecorrida, Pantalla, RespuestaOperador, Topicos

from ..config import Config
from ..deteccion.seguridad import CON_EQUIPO, Deteccion
from ..dominio.catalogo import Catalogo
from ..dominio.parte import (
    UNIDAD_MOSTRADA,
    VARIABLE_CONDICION_INSEGURA,
    AmbiguedadValor,
    EstadoParte,
    Fila,
    filas_desde_dict,
)
from ..dominio.rango import RangoHistorico
from ..estado import RelojPlanta
from ..persistencia.salida import Salida
from .numeros import formatear
from .parser import completar_unidades, extraer
from .respuestas import aplicar, interpretar

log = logging.getLogger("sistema.dialogo")

MAX_BORRADORES = 3  # por nota
MAX_MENSAJES = 6  # preguntas + borradores por nota


@dataclass
class Estado:
    nota_id: str
    ts: str
    estado: EstadoParte
    texto: str = ""
    mensajes: int = 0
    borradores: int = 0
    filas: list[Fila] = field(default_factory=list)
    ambiguedades_valor: list[AmbiguedadValor] = field(default_factory=list)
    opciones_equipo: list[str] = field(default_factory=list)
    ultimo_mensaje_id: str | None = None
    # A-9: filas cuyo valor se corrigió por el rango histórico (se perdió el «mil») y avisos del borrador.
    filas_con_mil: list[dict] = field(default_factory=list)
    avisos_rango: list[str] = field(default_factory=list)

    def a_dict(self) -> dict:
        d = asdict(self)
        d["estado"] = self.estado.value
        return d

    @classmethod
    def desde_dict(cls, d: dict) -> Estado:
        d = dict(d)
        d["estado"] = EstadoParte(d["estado"])
        d["filas"] = filas_desde_dict(d.get("filas", []))
        d["ambiguedades_valor"] = [AmbiguedadValor(**a) for a in d.get("ambiguedades_valor", [])]
        return cls(**d)

    @property
    def confirmable(self) -> bool:
        return any(f.valor is not None for f in self.filas)


class Dialogo:
    def __init__(
        self,
        config: Config,
        salida: Salida,
        base,
        catalogo: Callable[[], Catalogo | None],
        reloj: RelojPlanta,
        al_confirmar: Callable[[Estado], None] | None = None,
        rango: RangoHistorico | None = None,
    ):
        self._rango = rango
        self._config = config
        self._salida = salida
        self._base = base
        self._catalogo = catalogo
        self._reloj = reloj
        self.al_confirmar = al_confirmar
        self._estados: dict[str, Estado] = {}
        self._mensaje_nota: dict[str, str] = {}
        self._cola: queue.Queue[tuple] = queue.Queue()

    # -- entrada de trabajo --------------------------------------------------------------------------

    def encolar_nota(self, nota: NotaRecorrida, texto: str, deteccion: Deteccion | None) -> None:
        self._cola.put(("nota", nota, texto, deteccion))

    def encolar_respuesta(self, resp: RespuestaOperador) -> None:
        self._cola.put(("respuesta", resp))

    def correr(self, parar: threading.Event) -> None:
        while not parar.is_set():
            try:
                tarea = self._cola.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if tarea[0] == "nota":
                    self.procesar_nota(*tarea[1:])
                else:
                    self.procesar_respuesta(tarea[1])
            except Exception:
                log.exception("falló el diálogo (%s)", tarea[0])

    # -- persistencia (con respaldo en memoria si la base no responde) -------------------------------

    def _guardar(self, est: Estado) -> None:
        self._estados[est.nota_id] = est
        try:
            self._base.guardar_dialogo(est.nota_id, est.a_dict())
        except Exception as e:
            log.warning("no se pudo persistir el diálogo de %s (%s); sigue en memoria", est.nota_id, e)

    def _cargar(self, nota_id: str) -> Estado | None:
        if nota_id in self._estados:
            return self._estados[nota_id]
        try:
            d = self._base.cargar_dialogo(nota_id)
        except Exception:
            return None
        if d is None:
            return None
        est = Estado.desde_dict(d)
        self._estados[nota_id] = est
        return est

    def _nota_de(self, mensaje_id: str) -> str | None:
        if mensaje_id in self._mensaje_nota:
            return self._mensaje_nota[mensaje_id]
        try:
            return self._base.nota_de_mensaje(mensaje_id)
        except Exception:
            return None

    # -- notas ---------------------------------------------------------------------------------------

    def procesar_nota(self, nota: NotaRecorrida, texto: str, deteccion: Deteccion | None) -> None:
        cat = self._catalogo()
        if cat is None:
            log.error("sin catálogo de equipos: no se puede armar el parte de %s", nota.id)
            return
        if self._cargar(nota.id) is not None:
            log.info("nota %s: el diálogo ya existe; se ignora la repetición", nota.id)
            return
        est = Estado(nota.id, nota.ts.isoformat(), EstadoParte.borrador, texto=texto)
        if deteccion is not None:
            self._nota_insegura(est, texto, deteccion, cat)
            return
        ext = extraer(texto, cat)
        est.filas, est.ambiguedades_valor = ext.filas, ext.ambiguedades_valor
        if not ext.filas:
            est.estado = EstadoParte.sin_filas
            self._enviar(
                est,
                "Recibí tu nota pero no identifiqué equipos ni valores. Queda registrada sin parte.",
                espera=False,
            )
            return
        if ext.equipo_ambiguo:
            est.opciones_equipo = [t for t in cat.bombas() if t not in ext.equipos_dichos] or cat.bombas()
            self._preguntar_equipo(est, cat)
            return
        self._mostrar_borrador(est, cat)

    def _nota_insegura(self, est: Estado, texto: str, d: Deteccion, cat: Catalogo) -> None:
        # S2 y S4 no llevan equipo (D14 provisional): sin filas, solo el aviso de emergencia que ya salió.
        if d.codigo not in CON_EQUIPO or d.equipo is None:
            est.estado = EstadoParte.sin_filas
            self._guardar(est)
            return
        est.filas = [Fila(d.equipo, VARIABLE_CONDICION_INSEGURA, None, None, texto[:200])]
        est.estado = EstadoParte.pendiente_validacion
        nombre = self._nombre(cat, d.equipo)
        self._enviar(
            est,
            f"Anoté la condición insegura de {nombre} en el parte. La alerta ya fue emitida; "
            "el parte queda como borrador pendiente de validación.",
            filas=est.filas,
            espera=False,
        )

    def _preguntar_equipo(self, est: Estado, cat: Catalogo) -> None:
        if est.mensajes >= MAX_MENSAJES:
            self._pendiente(est)
            return
        est.estado = EstadoParte.pregunta_equipo
        nombres = [self._nombre(cat, t) for t in est.opciones_equipo]
        self._enviar(
            est,
            f"¿De cuál bomba hablás: {', '.join(nombres[:-1])} o {nombres[-1]}?"
            if len(nombres) > 1
            else f"¿Hablás de {nombres[0]}?",
            opciones=nombres,
            espera=True,
        )

    def _mostrar_borrador(self, est: Estado, cat: Catalogo, corregido: bool = False) -> None:
        if est.borradores >= MAX_BORRADORES or est.mensajes >= MAX_MENSAJES:
            self._pendiente(est)
            return
        completar_unidades(est.filas, cat)
        self._revisar_rango(est, cat)
        est.borradores += 1
        # Solo los valores ambiguos (1,8 o 18) ofrecen opciones; el «mil» recuperado por el rango no.
        provisional = [f for f in est.filas if f.provisional and self._ambigua(est, f)]
        opciones: list[str] = []
        resumen = "; ".join(f.descripcion(self._nombre(cat, f.tag)) for f in est.filas if f.tag)
        if provisional:
            cands = self._candidatos(est, provisional[0])
            opciones = [formatear(c) for c in cands]
            texto = (
                f"No estoy seguro del valor: lo anoté como {opciones[0]}, pero podría ser {opciones[-1]}. "
                f"Parte: {resumen}. ¿Confirmás o me corregís?"
            )
        else:
            cabecera = "Corregí el parte" if corregido else "Esto es lo que entendí"
            texto = f"{cabecera}: {resumen}. ¿Confirmás?"
        if est.avisos_rango:
            pregunta = texto.replace("¿Confirmás?", "¿Confirmás o me corregís?")
            texto = f"Ojo: {' '.join(est.avisos_rango)} {pregunta}"
        if not est.confirmable and self._config.confirmacion_estricta:
            # D8 estricta: nada que el operador pueda comparar y confirmar. Queda Borrador, sin eventos.
            est.estado = EstadoParte.pendiente_validacion
            self._enviar(
                est,
                f"Anoté: {resumen}. Son observaciones sin valores: el parte queda como borrador "
                "pendiente de validación.",
                filas=est.filas,
                espera=False,
            )
            return
        est.estado = EstadoParte.borrador
        self._enviar(est, texto, filas=est.filas, opciones=opciones, espera=True)

    def _revisar_rango(self, est: Estado, cat: Catalogo) -> None:
        """A-9: un valor fuera del rango de los últimos 45 días se avisa; si con el «mil» recuperado cae
        adentro, se muestra ese valor como provisional (sin opciones). Sin historia, no opina."""
        est.avisos_rango = []
        if self._rango is None:
            return
        ya = {d["fila"] for d in est.filas_con_mil}
        for i, f in enumerate(est.filas):
            if not f.tag or f.valor is None or i in ya or self._ambigua(est, f):
                continue
            ev = self._rango.evaluar(f.tag, f.variable, f.valor, f.unidad)
            if ev is None or ev.dentro:
                continue
            nombre, u = self._nombre(cat, f.tag), UNIDAD_MOSTRADA.get(f.unidad or "", f.unidad or "")
            tramo = f"{formatear(ev.rango.minimo)} a {formatear(ev.rango.maximo)} kg/cm²"
            if ev.con_mil is not None:
                est.filas_con_mil.append({"fila": i, "dicho": f.valor, "valor": ev.con_mil})
                est.avisos_rango.append(
                    f"entendí {formatear(f.valor)} {u} de {nombre}, que está fuera de lo que midió en los "
                    f"últimos 45 días ({tramo}); lo anoté como {formatear(ev.con_mil)} {u} "
                    "(¿se perdió el «mil»?)."
                )
                f.valor, f.provisional = ev.con_mil, True
            else:
                est.avisos_rango.append(
                    f"{formatear(f.valor)} {u} de {nombre} está fuera de lo que midió en los últimos 45 días "
                    f"({tramo})."
                )

    @staticmethod
    def _ambigua(est: Estado, fila: Fila) -> bool:
        return any(a.fila < len(est.filas) and est.filas[a.fila] is fila for a in est.ambiguedades_valor)

    def _observacion_provisional(self, est: Estado, fila: Fila) -> str:
        """Solo se muestra; la observación real de la fila no se toca (no es una anomalía)."""
        for d in est.filas_con_mil:
            if d["fila"] < len(est.filas) and est.filas[d["fila"]] is fila:
                return f"provisional: dijiste {formatear(d['dicho'])}, ¿se perdió el «mil»?"
        return f"provisional: ¿{' o '.join(formatear(c) for c in self._candidatos(est, fila))}?"

    @staticmethod
    def _candidatos(est: Estado, fila: Fila) -> list[float]:
        for a in est.ambiguedades_valor:
            if a.fila < len(est.filas) and est.filas[a.fila] is fila:
                return a.candidatos
        return [fila.valor] if fila.valor is not None else []

    # -- respuestas ----------------------------------------------------------------------------------

    def procesar_respuesta(self, resp: RespuestaOperador) -> None:
        cat = self._catalogo()
        nota_id = self._nota_de(resp.mensaje_id)
        est = self._cargar(nota_id) if nota_id else None
        if est is None or cat is None:
            log.info("respuesta %s a un mensaje desconocido (%s): se ignora", resp.id, resp.mensaje_id)
            return
        if est.ultimo_mensaje_id != resp.mensaje_id:
            log.info("respuesta %s a un mensaje viejo de %s: se ignora", resp.id, est.nota_id)
            return
        it = interpretar(resp.texto, cat)
        log.info("%s ← %r → %s %s", est.nota_id, resp.texto, it.tipo, it.cambios or "")
        if est.estado == EstadoParte.pregunta_equipo:
            self._responder_equipo(est, it, cat)
        elif est.estado == EstadoParte.borrador:
            self._responder_borrador(est, it, cat)

    def _responder_equipo(self, est: Estado, it, cat: Catalogo) -> None:
        if it.tipo == "equipo" and it.tag in cat.bombas():
            for f in est.filas:
                if f.tag is None:
                    f.tag = it.tag
            self._mostrar_borrador(est, cat)
        else:
            self._preguntar_equipo(est, cat)

    def _responder_borrador(self, est: Estado, it, cat: Catalogo) -> None:
        if it.tipo == "confirma" or (
            not est.confirmable and not self._config.confirmacion_estricta and it.tipo != "corrige"
        ):
            self._confirmar(est)
        elif it.tipo == "corrige":
            if it.cambios:
                est.filas = aplicar(est.filas, it.cambios, cat)
            est.ambiguedades_valor = []  # lo ambiguo ya se resolvió con la respuesta del operador
            self._mostrar_borrador(est, cat, corregido=True)
        else:
            self._mostrar_borrador(est, cat)  # no entendimos: se vuelve a mostrar

    def _confirmar(self, est: Estado) -> None:
        for f in est.filas:
            f.provisional = False
        est.estado = EstadoParte.confirmado
        self._enviar(est, "Listo, el parte quedó guardado.", espera=False)
        log.info("parte de %s confirmado (%d filas)", est.nota_id, len(est.filas))
        if self.al_confirmar is not None:
            self.al_confirmar(est)

    def _pendiente(self, est: Estado) -> None:
        est.estado = EstadoParte.pendiente_validacion
        self._enviar(
            est,
            "No pude cerrar el parte con tu confirmación. Queda como borrador pendiente de validación.",
            espera=False,
        )

    # -- mensajes ------------------------------------------------------------------------------------

    def _nombre(self, cat: Catalogo, tag: str) -> str:
        nombres = cat.equipos[tag].nombres
        return nombres[0] if nombres else tag

    def _enviar(
        self,
        est: Estado,
        texto: str,
        filas: list[Fila] | None = None,
        opciones: list[str] | None = None,
        espera: bool = True,
    ) -> None:
        est.mensajes += 1
        mensaje_id = f"TAB-{est.nota_id}-{est.mensajes}"
        est.ultimo_mensaje_id = mensaje_id
        self._mensaje_nota[mensaje_id] = est.nota_id
        self._guardar(est)  # antes de publicar: la respuesta puede llegar enseguida
        try:
            self._base.registrar_mensaje(mensaje_id, est.nota_id)
        except Exception as e:
            log.warning("no se pudo registrar %s (%s)", mensaje_id, e)
        contrato_filas = [
            f.a_contrato(self._observacion_provisional(est, f) if f.provisional else None)
            for f in (filas or [])
            if f.tag
        ]
        pantalla = (
            Pantalla(titulo="Parte de recorrida", filas=contrato_filas, opciones=opciones or [])
            if contrato_filas or opciones
            else None
        )
        msg = MensajeTablet(
            id=mensaje_id,
            ts=self._reloj.ahora(),
            nota_ids=[est.nota_id],
            texto=texto,
            pantalla=pantalla,
            espera_respuesta=espera,
        )
        self._salida.emitir(mensaje_id, Topicos.TABLET, msg)


def ts_de(est: Estado) -> datetime:
    return datetime.fromisoformat(est.ts)

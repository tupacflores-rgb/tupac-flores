"""Sonda del enlace con la nube.

Se abre una conexión al proxy `enlace:3128` y se pide un CONNECT. Con el enlace arriba el proxy responde
(200, o 502 si no alcanza el destino); con el enlace caído corta la conexión sin responder nada.
"""

from __future__ import annotations

import logging
import socket

log = logging.getLogger("sistema.enlace")


def enlace_arriba(proxy: str, destino: str, timeout_s: float = 3.0) -> bool:
    host, _, puerto = proxy.rpartition(":")
    try:
        with socket.create_connection((host, int(puerto)), timeout=timeout_s) as s:
            s.settimeout(timeout_s)
            s.sendall(f"CONNECT {destino} HTTP/1.1\r\nHost: {destino}\r\n\r\n".encode())
            respuesta = s.recv(64)
    except OSError:
        return False
    return respuesta.startswith(b"HTTP/")

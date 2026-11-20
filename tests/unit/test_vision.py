import base64
import json

import httpx
import pytest

from sistema.config import Config
from sistema.integraciones.vision import ClienteVision

URL = "https://vision.test/analizar"
IMAGEN = b"contenido-de-imagen"


def test_envia_imagen_en_base64_y_parsea_el_hallazgo():
    def responder(request: httpx.Request) -> httpx.Response:
        datos = request.read()
        assert str(request.url) == URL
        assert request.extensions["timeout"]["read"] == 15.0
        assert json.loads(datos) == {"image_base64": base64.b64encode(IMAGEN).decode("ascii")}
        return httpx.Response(
            200,
            json={"severidad": "grave", "objeto": "empaquetadura", "descripcion": "daño visible"},
        )

    with httpx.Client(transport=httpx.MockTransport(responder)) as http:
        hallazgo = ClienteVision(URL, http).analizar_imagen(IMAGEN)

    assert hallazgo.estado == "completa"
    assert hallazgo.severidad == "grave"
    assert hallazgo.objeto == "empaquetadura"
    assert hallazgo.respuesta == {
        "severidad": "grave",
        "objeto": "empaquetadura",
        "descripcion": "daño visible",
    }


@pytest.mark.parametrize(
    "fallo",
    [
        lambda req: httpx.ReadTimeout("timeout", request=req),
        lambda req: httpx.ConnectError("sin conexión", request=req),
    ],
)
def test_timeout_o_error_de_red_deja_hallazgo_pendiente(fallo):
    def responder(request: httpx.Request) -> httpx.Response:
        raise fallo(request)

    with httpx.Client(transport=httpx.MockTransport(responder)) as http:
        hallazgo = ClienteVision(URL, http).analizar_imagen(IMAGEN)

    assert hallazgo.estado == "pendiente"
    assert hallazgo.severidad == "desconocida"
    assert hallazgo.error


def test_error_http_deja_hallazgo_pendiente():
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(503, request=request))
    ) as http:
        hallazgo = ClienteVision(URL, http).analizar_imagen(IMAGEN)

    assert hallazgo.estado == "pendiente"
    assert hallazgo.severidad == "desconocida"
    assert hallazgo.error == "http_503"


def test_respuesta_json_invalida_deja_hallazgo_pendiente():
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"no-json"))
    ) as http:
        hallazgo = ClienteVision(URL, http).analizar_imagen(IMAGEN)

    assert hallazgo.estado == "pendiente"
    assert hallazgo.severidad == "desconocida"
    assert hallazgo.error == "respuesta_json_invalida"


def test_url_de_vision_se_lee_desde_config():
    config = Config.desde_entorno({"NVIDIA_VISION_URL": f" {URL} "})

    assert config.nvidia_vision_url == URL


def test_rechaza_url_vacia():
    with pytest.raises(ValueError, match="NVIDIA_VISION_URL"):
        ClienteVision("")

"""El client_id MQTT sale del proyecto de compose: dos stacks nunca se pisan la sesión."""

from sistema.config import Config


def test_client_id_por_proyecto_de_compose():
    assert Config.desde_entorno({"COMPOSE_PROJECT_NAME": "alumno-tupac-flores"}).mqtt_client_id == (
        "alumno-tupac-flores-agente"
    )
    assert (
        Config.desde_entorno({"COMPOSE_PROJECT_NAME": "tupac-flores"}).mqtt_client_id == "tupac-flores-agente"
    )


def test_dos_proyectos_distintos_dan_client_id_distintos():
    a = Config.desde_entorno({"COMPOSE_PROJECT_NAME": "a"}).mqtt_client_id
    b = Config.desde_entorno({"COMPOSE_PROJECT_NAME": "b"}).mqtt_client_id
    assert a != b


def test_sin_proyecto_usa_el_valor_por_defecto_y_el_override_explicito_gana():
    assert Config.desde_entorno({}).mqtt_client_id == "tupac-flores-agente"
    assert (
        Config.desde_entorno({"COMPOSE_PROJECT_NAME": "x", "MQTT_CLIENT_ID": "fijo"}).mqtt_client_id == "fijo"
    )

"""Definición de planta y textos de seguridad compartidos por los tests (réplica mínima de GET /api/planta)."""
# ruff: noqa: E501

DEFINICION = {
    "equipos": [
        {"tag": "B-101", "tipo": "bomba", "nombres": ["la uno", "la 1", "la de la punta", "la del portón", "bomba uno"],
         "manometro_local": {"unidad": "kg/cm2"}},
        {"tag": "B-102", "tipo": "bomba", "nombres": ["la dos", "la 2", "la del medio", "bomba dos"],
         "manometro_local": {"unidad": "psi"}},
        {"tag": "B-103", "tipo": "bomba", "nombres": ["la tres", "la 3", "la del fondo", "la de reserva", "bomba tres"],
         "manometro_local": {"unidad": "bar"}},
        {"tag": "F-201A", "tipo": "filtro", "nombres": ["el filtro A", "el primario", "el de la izquierda", "el grueso"]},
        {"tag": "F-201B", "tipo": "filtro", "nombres": ["el filtro B", "el secundario", "el de la derecha", "el fino"]},
        {"tag": "TK-301", "tipo": "tanque", "nombres": ["el pulmón", "el tanque", "el tanque pulmón"]},
        {"tag": "FT-401", "tipo": "caudalimetro", "nombres": ["el caudalímetro", "el colector"]},
    ],
    "sensores": [],
}  # fmt: skip

# Los cuatro textos exactos que dicta el operador virtual (operador.py: nota_seguridad).
S1 = ("¡Atención! Se rompió algo en la descarga de la uno, sale agua a presión para todos lados, "
      "me alejé. Saqué foto desde lejos.")  # fmt: skip
S2 = "Hay olor a gas fuerte del lado del portón, viene del lado del separador."
S3 = "Sale humo del motor de la dos, hay olor a quemado."
S4 = "Mi compañero se golpeó la mano con la llave en el skid de filtros, está sangrando bastante."

"""Carga el esquema SQL compartido por el agente y el contenedor de PostgreSQL."""

from importlib.resources import files

ESQUEMA = files(__package__).joinpath("esquema.sql").read_text(encoding="utf-8")

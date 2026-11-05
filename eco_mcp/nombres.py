"""Resuelve nombres escritos por una persona ("tarjeta visa", "comida") al id que usa la API."""

import unicodedata

from fastmcp.exceptions import ToolError


def normalizar(texto):
    sin_tildes = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode("ascii")
    return " ".join(sin_tildes.lower().split())


def elegir(valor, candidatos, que):
    """Devuelve el id del candidato que corresponde a `valor`.

    candidatos: lista de (id, nombre). Acepta el id directo, el nombre exacto o una parte unica del nombre.
    Si no hay una unica coincidencia, explica cuales hay para que se pueda corregir la llamada.
    """
    texto = normalizar(valor)
    if not texto:
        raise ToolError("Falta indicar %s." % que)
    if texto.isdigit():
        for id_, _ in candidatos:
            if id_ == int(texto):
                return id_
    exactos = [id_ for id_, nombre in candidatos if normalizar(nombre) == texto]
    if len(exactos) == 1:
        return exactos[0]
    parciales = [id_ for id_, nombre in candidatos if texto in normalizar(nombre)]
    if len(parciales) == 1:
        return parciales[0]
    disponibles = ", ".join('"%s"' % nombre for _, nombre in candidatos) or "(ninguna)"
    if len(parciales) > 1 or len(exactos) > 1:
        raise ToolError('"%s" coincide con varias opciones de %s. Opciones: %s.' % (valor, que, disponibles))
    raise ToolError('No encontré "%s" entre %s. Opciones: %s.' % (valor, que, disponibles))


def nombre_de(id_, candidatos):
    for candidato_id, nombre in candidatos:
        if candidato_id == id_:
            return nombre
    return str(id_)

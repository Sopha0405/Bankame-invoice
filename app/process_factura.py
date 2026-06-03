from app.gemini_agent import analizar_factura

def procesar_factura(
    file_bytes: bytes,
    mime_type: str,
    nombres: str,
    apellido_paterno: str,
    apellido_materno: str,
    direccion: str,
    geolocalizacion: str,
    tipo_vivienda: int
):
    return analizar_factura(
        file_bytes=file_bytes,
        mime_type=mime_type,
        nombres=nombres,
        apellido_paterno=apellido_paterno,
        apellido_materno=apellido_materno,
        direccion=direccion,
        geolocalizacion=geolocalizacion,
        tipo_vivienda=tipo_vivienda
    )
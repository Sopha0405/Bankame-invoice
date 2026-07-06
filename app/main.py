from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from app.gemini_agent import GeminiAnalysisError, analizar_factura
from app.models import FacturaResultado

app = FastAPI(
    title="Factura Analyzer AI"
)

SUPPORTED_CONTENT_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
}


@app.get("/")
def root():
    return {
        "service": "Factura Analyzer AI",
        "status": "running",
        "docs": "/docs",
        "health": "/health"
    }


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/ocr/factura/", response_model=FacturaResultado)
async def analizar(
    file: UploadFile = File(...),
    nombres: str = Form(...),
    apellido_paterno: str = Form(...),
    apellido_materno: str = Form(...),
    direccion: str = Form(...),
    geolocalizacion: str = Form(...),
    tipo_vivienda: int = Form(...)
):
    try:
        if file.content_type not in SUPPORTED_CONTENT_TYPES:
            raise HTTPException(
                status_code=415,
                detail="Tipo de archivo no soportado. Use PDF, JPEG, PNG o WEBP.",
            )

        contenido = await file.read()

        data = analizar_factura(
            file_bytes=contenido,
            mime_type=file.content_type,
            nombres=nombres,
            apellido_paterno=apellido_paterno,
            apellido_materno=apellido_materno,
            direccion=direccion,
            geolocalizacion=geolocalizacion,
            tipo_vivienda=tipo_vivienda
        )
        data["document_filename"] = file.filename
        data["document_content_type"] = file.content_type

        return FacturaResultado(
            success=True,
            message="Factura analizada correctamente",
            data=data
        )

    except GeminiAnalysisError as e:
        status_code = e.status_code
        if status_code in (401, 403):
            detail = "Credenciales de Gemini invalidas o sin permisos"
        elif status_code == 429:
            detail = "Limite de Gemini excedido temporalmente"
        elif status_code >= 500:
            detail = "Gemini no esta disponible temporalmente"
        else:
            detail = "Gemini rechazo la solicitud"

        raise HTTPException(
            status_code=status_code,
            detail=detail,
        )

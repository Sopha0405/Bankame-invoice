import logging
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from app.gemini_agent import GeminiAnalysisError, analizar_factura
from app.models import FacturaResultado
from app.routers.ci_quality import router as ci_quality_router

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Factura Analyzer AI"
)

app.include_router(ci_quality_router)

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
    direccion: Optional[str] = Form(None),
    geolocalizacion: Optional[str] = Form(None),
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

    except HTTPException:
        raise

    except GeminiAnalysisError as e:
        logger.exception(
            "GeminiAnalysisError analizando factura. status_code=%s message=%s",
            e.status_code,
            str(e),
        )

        status_code = e.status_code
        if status_code in (401, 403):
            detail = "Credenciales de Gemini invalidas o sin permisos"
        elif status_code == 429:
            detail = "Limite de Gemini excedido temporalmente"
        elif status_code >= 500:
            detail = f"Gemini no esta disponible temporalmente: {str(e)}"
        else:
            detail = f"Gemini rechazo la solicitud: {str(e)}"

        raise HTTPException(
            status_code=status_code,
            detail=detail,
        )

    except Exception as e:
        logger.exception(
            "Error interno no controlado analizando factura: %s",
            str(e),
        )

        raise HTTPException(
            status_code=500,
            detail=f"Error interno OCR: {type(e).__name__}: {str(e)}",
        )

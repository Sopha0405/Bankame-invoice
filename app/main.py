from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from app.gemini_agent import analizar_factura
from app.models import FacturaResultado

app = FastAPI(
    title="Factura Analyzer AI"
)


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

    except Exception as e:
        message = str(e)
        status_code = 503 if "503" in message or "UNAVAILABLE" in message else 500
        raise HTTPException(
            status_code=status_code,
            detail="Gemini no esta disponible temporalmente" if status_code == 503 else "No fue posible analizar la factura",
        )

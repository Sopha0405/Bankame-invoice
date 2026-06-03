# Bankame Invoice

API con FastAPI para analizar facturas bolivianas usando Gemini y validar datos relevantes del cliente, servicio y direccion.

## Requisitos

- Python 3.12
- `GEMINI_API_KEY` valida de Gemini

## Ejecucion local

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

La API queda disponible en:

- `GET /`
- `GET /health`
- `POST /ocr/factura/`
- `GET /docs`

## Variables de entorno

Crea un archivo `.env` local:

```env
GEMINI_API_KEY=tu_api_key
```

En Cloud Run configura la misma variable como secreto. No subas `.env`, llaves ni archivos JSON de credenciales.

## Docker

Construir la imagen:

```bash
docker build -t bankame-invoice .
```

Ejecutar localmente:

```bash
docker run --rm -p 8080:8080 --env-file .env bankame-invoice
```

## Cloud Run

El contenedor expone `8080` por defecto y ejecuta Uvicorn usando la variable `PORT` que define Cloud Run:

```bash
uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
```

Ejemplo de despliegue:

```bash
gcloud run deploy bankame-invoice \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-secrets GEMINI_API_KEY=gemini-api-key:latest
```

Para produccion, usa Secret Manager en lugar de pasar la llave en texto plano.

## Tests

```bash
pytest tests
```

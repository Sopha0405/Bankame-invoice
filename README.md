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
GEMINI_MODELS=gemini-2.5-flash,gemini-2.5-flash-lite
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

### Despliegue automatizado

El servicio se publica privado. Gemini se ejecuta dentro de este contenedor y el backend solamente invoca su API HTTP.

Primero crea el secreto una sola vez, sin guardar la clave en archivos:

```powershell
$ApiKey = Read-Host "Gemini API key" -AsSecureString
$Ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($ApiKey)
try {
    [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Ptr) |
        gcloud secrets create gemini-api-key --data-file=-
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Ptr)
}
```

Despliega indicando la cuenta de servicio usada por `bankame-backend`:

```powershell
.\deploy-cloud-run.ps1 `
  -ProjectId "PROJECT_ID" `
  -Region "us-central1" `
  -CallerServiceAccount "bankame-backend@PROJECT_ID.iam.gserviceaccount.com"
```

El script:

- habilita Cloud Run, Cloud Build y Secret Manager;
- crea la identidad de ejecución si no existe;
- permite que esa identidad lea `gemini-api-key`;
- despliega el contenedor sin acceso público;
- concede `roles/run.invoker` al backend indicado.

Comando equivalente manual:

```bash
gcloud run deploy bankame-invoice \
  --source . \
  --region us-central1 \
  --no-allow-unauthenticated \
  --set-secrets GEMINI_API_KEY=gemini-api-key:latest
```

El backend debe enviar un ID token de Google cuyo audience sea la URL del servicio Cloud Run. No debe conocer ni recibir `GEMINI_API_KEY`.

## Tests

```bash
pytest tests
```

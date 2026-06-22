param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,

    [string]$Region = "us-central1",
    [string]$ServiceName = "bankame-invoice",
    [string]$RuntimeServiceAccount = "",
    [string]$CallerServiceAccount = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RuntimeServiceAccount)) {
    $RuntimeServiceAccount = "$ServiceName@$ProjectId.iam.gserviceaccount.com"
}

gcloud config set project $ProjectId
gcloud services enable run.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com

$runtimeAccountExists = gcloud iam service-accounts describe $RuntimeServiceAccount --format="value(email)" 2>$null
if (-not $runtimeAccountExists) {
    gcloud iam service-accounts create $ServiceName --display-name="Bankame Invoice Cloud Run"
}

gcloud secrets describe gemini-api-key --project=$ProjectId 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "Falta el secreto gemini-api-key. Crealo antes del despliegue: `$ApiKey | gcloud secrets create gemini-api-key --data-file=-"
}

gcloud secrets add-iam-policy-binding gemini-api-key `
    --member="serviceAccount:$RuntimeServiceAccount" `
    --role="roles/secretmanager.secretAccessor"

gcloud run deploy $ServiceName `
    --source . `
    --project $ProjectId `
    --region $Region `
    --service-account $RuntimeServiceAccount `
    --set-secrets="GEMINI_API_KEY=gemini-api-key:latest" `
    --port 8080 `
    --memory 1Gi `
    --cpu 1 `
    --concurrency 4 `
    --timeout 300 `
    --max-instances 10 `
    --no-allow-unauthenticated

if (-not [string]::IsNullOrWhiteSpace($CallerServiceAccount)) {
    gcloud run services add-iam-policy-binding $ServiceName `
        --project $ProjectId `
        --region $Region `
        --member="serviceAccount:$CallerServiceAccount" `
        --role="roles/run.invoker"
}

gcloud run services describe $ServiceName --project $ProjectId --region $Region --format="value(status.url)"

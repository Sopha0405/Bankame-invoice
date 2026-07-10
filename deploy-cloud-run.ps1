param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,

    [string]$Region = "southamerica-west1",
    [string]$ServiceName = "bankame-invoice",
    [string]$Image = "",
    [string]$RuntimeServiceAccount = "",
    [string]$CallerServiceAccount = "",
    [int]$Concurrency = 8,
    [int]$MinInstances = 1,
    [int]$MaxInstances = 15,
    [switch]$SkipSetup,
    [switch]$SkipIamBindings
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false
$Gcloud = "gcloud.cmd"

function Invoke-Gcloud {
    & $Gcloud @args
    if ($LASTEXITCODE -ne 0) {
        throw "gcloud fallo con codigo ${LASTEXITCODE}: $($args -join ' ')"
    }
}

if ([string]::IsNullOrWhiteSpace($RuntimeServiceAccount)) {
    $RuntimeServiceAccount = "$ServiceName@$ProjectId.iam.gserviceaccount.com"
}

Invoke-Gcloud config set project $ProjectId

if (-not $SkipSetup) {
    Invoke-Gcloud services enable run.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com

    $ErrorActionPreference = "Continue"
    $runtimeAccountExists = & $Gcloud iam service-accounts describe $RuntimeServiceAccount --format="value(email)" 2>$null
    $ErrorActionPreference = "Stop"
    if (-not $runtimeAccountExists) {
        Invoke-Gcloud iam service-accounts create $ServiceName --display-name="Bankame Invoice Cloud Run"
    }

    $ErrorActionPreference = "Continue"
    & $Gcloud secrets describe gemini-api-key --project=$ProjectId 2>$null
    $ErrorActionPreference = "Stop"
    if ($LASTEXITCODE -ne 0) {
        throw "Falta el secreto gemini-api-key. Crealo antes del despliegue: `$ApiKey | gcloud secrets create gemini-api-key --data-file=-"
    }
}

if (-not $SkipIamBindings) {
    Invoke-Gcloud secrets add-iam-policy-binding gemini-api-key `
        --member="serviceAccount:$RuntimeServiceAccount" `
        --role="roles/secretmanager.secretAccessor"
}

$deployArgs = @(
    "run", "deploy", $ServiceName,
    "--project", $ProjectId,
    "--region", $Region,
    "--service-account", $RuntimeServiceAccount,
    "--set-secrets", "GEMINI_API_KEY=gemini-api-key:latest",
    "--env-vars-file", "cloudrun-env.yaml",
    "--port", "8080",
    "--memory", "1Gi",
    "--cpu", "1",
    "--concurrency", "$Concurrency",
    "--timeout", "300",
    "--min-instances", "$MinInstances",
    "--max-instances", "$MaxInstances",
    "--execution-environment", "gen2",
    "--no-allow-unauthenticated"
)

if ([string]::IsNullOrWhiteSpace($Image)) {
    $deployArgs += @("--source", ".")
} else {
    $deployArgs += @("--image", $Image)
}

Invoke-Gcloud @deployArgs

if ((-not $SkipIamBindings) -and (-not [string]::IsNullOrWhiteSpace($CallerServiceAccount))) {
    Invoke-Gcloud run services add-iam-policy-binding $ServiceName `
        --project $ProjectId `
        --region $Region `
        --member="serviceAccount:$CallerServiceAccount" `
        --role="roles/run.invoker"
}

Invoke-Gcloud run services describe $ServiceName --project $ProjectId --region $Region --format="value(status.url)"

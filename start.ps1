Set-Location -LiteralPath $PSScriptRoot
$ErrorActionPreference = 'Stop'
$loadedProjectKey = $false
$credentialPath = Join-Path $env:LOCALAPPDATA 'LogisticsDemo/deepseek-key.dpapi'
if (-not $env:LOGISTICS_DEEPSEEK_API_KEY -and (Test-Path -LiteralPath $credentialPath)) {
    $secureKey = (Get-Content -LiteralPath $credentialPath -Raw).Trim() | ConvertTo-SecureString
    $keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    try {
        $env:LOGISTICS_DEEPSEEK_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
        $loadedProjectKey = $true
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
        $secureKey.Dispose()
    }
}
try { python server.py } finally {
    if ($loadedProjectKey) { Remove-Item Env:LOGISTICS_DEEPSEEK_API_KEY -ErrorAction SilentlyContinue }
}

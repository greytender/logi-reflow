param([int]$Port=8766,[string]$Database='')
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
try {
    if ($Database) { python server.py --port $Port --database $Database }
    else { python server.py --port $Port }
} finally {
    if ($loadedProjectKey) { Remove-Item Env:LOGISTICS_DEEPSEEK_API_KEY -ErrorAction SilentlyContinue }
}

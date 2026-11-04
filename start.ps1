param([int]$Port=8766,[string]$Database='',[string]$ApplicationDatabase='',[string]$IntakeDatabase='')
Set-Location -LiteralPath $PSScriptRoot
$ErrorActionPreference = 'Stop'
$loadedProjectKey = $false
$credentialPath = Join-Path $env:LOCALAPPDATA 'LogisticsDemo/deepseek-key.dpapi'
if (-not $env:LOGISTICS_DEEPSEEK_API_KEY -and (Test-Path -LiteralPath $credentialPath)) {
    $secureKey = $null
    $keyPointer = [IntPtr]::Zero
    try {
        $secureKey = (Get-Content -LiteralPath $credentialPath -Raw).Trim() | ConvertTo-SecureString
        $keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
        $env:LOGISTICS_DEEPSEEK_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
        $loadedProjectKey = $true
    } catch {
        Write-Warning '未能加载本机保存的AI密钥，核心试算仍可运行。可在网页连接设置中重新配置。'
    } finally {
        if ($keyPointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer) }
        if ($null -ne $secureKey) { $secureKey.Dispose() }
    }
}
try {
    $serverArgs = @('server.py', '--port', "$Port")
    if ($Database) { $serverArgs += @('--database', $Database) }
    if ($ApplicationDatabase) { $serverArgs += @('--application-database', $ApplicationDatabase) }
    if ($IntakeDatabase) { $serverArgs += @('--intake-database', $IntakeDatabase) }
    python @serverArgs
} finally {
    if ($loadedProjectKey) { Remove-Item Env:LOGISTICS_DEEPSEEK_API_KEY -ErrorAction SilentlyContinue }
}

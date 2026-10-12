param([int]$Port = 8766, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$siteUrl = "http://127.0.0.1:$Port/"
$ready = $false
try {
    $response = Invoke-WebRequest -Uri "$($siteUrl)api/scenario/sample" -UseBasicParsing -TimeoutSec 2
    $sample = $response.Content | ConvertFrom-Json
    $ready = $response.StatusCode -eq 200 -and $null -ne $sample.csv_files
} catch { }
if (-not $ready) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    if ($listener) { throw "端口 $Port 已被其他程序占用。可运行 app-launch.ps1 -Port 8767 使用另一端口。" }
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCmd) { throw '请先安装 Python 3.10 或以上版本，并加入 PATH。' }
    $startupArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + (Join-Path $PSScriptRoot 'start.ps1') + '"'), '-Port', $Port)
    $runtimeLogs = Join-Path $PSScriptRoot 'outputs/app-runtime'
    [IO.Directory]::CreateDirectory($runtimeLogs) | Out-Null
    Start-Process -FilePath powershell.exe -ArgumentList $startupArgs -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeLogs "server-$Port.log") -RedirectStandardError (Join-Path $runtimeLogs "error-$Port.log")
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 250
        try {
            $response = Invoke-WebRequest -Uri "$($siteUrl)api/scenario/sample" -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) { $ready = $true; break }
        } catch { }
    }
    if (-not $ready) { throw '服务未成功启动。请在项目目录执行 python server.py 查看错误。' }
}
if ($NoBrowser) { Write-Output $siteUrl; exit 0 }
$chromePath = Join-Path $env:ProgramFiles 'Google/Chrome/Application/chrome.exe'
if (Test-Path -LiteralPath $chromePath) { Start-Process -FilePath $chromePath -ArgumentList $siteUrl }
else { Start-Process $siteUrl }

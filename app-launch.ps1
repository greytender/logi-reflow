param([int]$Port = 8766, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$expectedBuild = '20261104-rounded-fit-v1'
$siteUrl = "http://127.0.0.1:$Port/"
$ready = $false
$selectedPort = $null
function Test-LocalPort([int]$ProbePort) {
    $portProbe = New-Object System.Net.Sockets.TcpClient
    try { $portProbe.Connect('127.0.0.1', $ProbePort); return $true }
    catch { return $false }
    finally { $portProbe.Dispose() }
}
for ($candidatePort = $Port; $candidatePort -lt $Port + 10; $candidatePort++) {
    $candidateUrl = "http://127.0.0.1:$candidatePort/"
    try {
        $response = Invoke-WebRequest -Uri "$($candidateUrl)api/product/status" -UseBasicParsing -TimeoutSec 2
        $productState = $response.Content | ConvertFrom-Json
        if ($productState.build -eq $expectedBuild) { $selectedPort = $candidatePort; $ready = $true; break }
    } catch { }
    $listener = Test-LocalPort $candidatePort
    if (-not $listener) { $selectedPort = $candidatePort; break }
}
if ($null -eq $selectedPort) { throw '没有找到可用的本机端口，请关闭不再使用的旧服务后重试。' }
$Port = $selectedPort
$siteUrl = "http://127.0.0.1:$Port/"
if (-not $ready) {
    $listener = Test-LocalPort $Port
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
            $response = Invoke-WebRequest -Uri "$($siteUrl)api/product/status" -UseBasicParsing -TimeoutSec 2
            $productState = $response.Content | ConvertFrom-Json
            if ($response.StatusCode -eq 200 -and $productState.build -eq $expectedBuild) { $ready = $true; break }
        } catch { }
    }
    if (-not $ready) { throw '服务未成功启动。请在项目目录执行 python server.py 查看错误。' }
}
$runtimeLogs = Join-Path $PSScriptRoot 'outputs/app-runtime'
[IO.Directory]::CreateDirectory($runtimeLogs) | Out-Null
$siteUrl | Set-Content -LiteralPath (Join-Path $runtimeLogs 'active-url.txt') -Encoding UTF8
if ($NoBrowser) { Write-Output $siteUrl; exit 0 }
$chromePath = Join-Path $env:ProgramFiles 'Google/Chrome/Application/chrome.exe'
if (Test-Path -LiteralPath $chromePath) { Start-Process -FilePath $chromePath -ArgumentList $siteUrl }
else { Start-Process $siteUrl }

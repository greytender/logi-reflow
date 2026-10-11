$ErrorActionPreference = 'Stop'
# Read the user's copied key without printing it or passing it on a command line.
$copiedKey = (Get-Clipboard -Raw).Trim()
if ($copiedKey -notmatch '^sk-[A-Za-z0-9_-]{16,200}$') {
    throw '剪贴板不是有效格式的DeepSeek密钥。请重新复制密钥后再运行。'
}
$credentialDirectory = Join-Path $env:LOCALAPPDATA 'LogisticsDemo'
$credentialPath = Join-Path $credentialDirectory 'deepseek-key.dpapi'
New-Item -ItemType Directory -Path $credentialDirectory -Force | Out-Null
if (Test-Path -LiteralPath $credentialPath) {
    Copy-Item -LiteralPath $credentialPath -Destination ($credentialPath + '.backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
}
$secureKey = ConvertTo-SecureString $copiedKey -AsPlainText -Force
ConvertFrom-SecureString $secureKey | Set-Content -LiteralPath $credentialPath -Encoding ASCII
$copiedKey = $null
$secureKey.Dispose()
Write-Output '临时密钥已由Windows加密保存到项目目录之外，没有显示或写入源代码。'

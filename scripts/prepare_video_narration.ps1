param([string]$Plan = 'docs/video-narration.json', [string]$Output = 'outputs/video-work/audio')
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$projectRoot = Split-Path $PSScriptRoot -Parent
$planPath = Join-Path $projectRoot $Plan
$outputDir = Join-Path $projectRoot $Output
[IO.Directory]::CreateDirectory($outputDir) | Out-Null
$document = Get-Content -Raw -Encoding UTF8 -LiteralPath $planPath | ConvertFrom-Json
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $speaker.SelectVoice($document.voice)
    $speaker.Rate = $document.rate
    $records = @()
    foreach ($scene in $document.scenes) {
        $phrases = [regex]::Matches($scene.text, '[^\u3002\uFF01\uFF1F\uFF1B]+[\u3002\uFF01\uFF1F\uFF1B]?') | ForEach-Object { $_.Value.Trim() } | Where-Object { $_ }
        $part = 0
        foreach ($phrase in $phrases) {
            $name = "scene-$($scene.id)-$('{0:D2}' -f $part).wav"
            $file = Join-Path $outputDir $name
            $speaker.SetOutputToWaveFile($file)
            $speaker.Speak($phrase)
            $speaker.SetOutputToNull()
            $records += @{ scene = $scene.id; part = $part; text = $phrase; filename = $name }
            $part++
        }
    }
    $json = $records | ConvertTo-Json -Depth 5
    [IO.File]::WriteAllText((Join-Path $outputDir 'phrases.json'), $json, [Text.UTF8Encoding]::new($false))
    Write-Output "Created $($records.Count) narrated phrases using local Chinese speech synthesis."
} finally { $speaker.Dispose() }

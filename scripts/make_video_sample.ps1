$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$sampleDir = Join-Path $projectRoot 'runtime/samples'
New-Item -ItemType Directory -Force -Path $sampleDir | Out-Null
Add-Type -AssemblyName System.Speech
$speechProbe = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $speechProbe.SetOutputToWaveFile((Join-Path $sampleDir 'validation.wav'))
    $speechProbe.Speak('This is a knowledge management test. Keep the original document. Review your notes every Friday. The project budget is twelve thousand eight hundred dollars.')
} finally { $speechProbe.Dispose() }
& ffmpeg -hide_banner -loglevel error -y -f lavfi -i color=c=white:s=320x240:r=10 -i (Join-Path $sampleDir 'validation.wav') -shortest -c:v libx264 -c:a aac (Join-Path $sampleDir 'validation.mp4')
if ($LASTEXITCODE -ne 0) { throw 'FFmpeg sample generation failed' }

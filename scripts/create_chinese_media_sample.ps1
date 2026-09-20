$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$sampleDir = Join-Path $projectRoot 'runtime/samples'
$wavPath = Join-Path $sampleDir 'chinese-validation.wav'
$mp4Path = Join-Path $sampleDir 'chinese-validation.mp4'
$textPath = Join-Path $sampleDir 'chinese-validation.txt'
$manifestPath = Join-Path $sampleDir 'chinese-validation.json'
$voiceName = 'Microsoft Huihui Desktop'
$sampleText = '这是一段中文知识管理验收样本。项目预算为一万二千八百元人民币。负责人是李明。团队每周五复盘一次。下一步行动是下周三之前完成原始资料归档。'

$ffmpegPath = (Get-Command ffmpeg -CommandType Application -ErrorAction Stop).Source
$ffprobePath = (Get-Command ffprobe -CommandType Application -ErrorAction Stop).Source
New-Item -ItemType Directory -Force -Path $sampleDir | Out-Null

Add-Type -AssemblyName System.Speech
$sampleSpeech = [System.Speech.Synthesis.SpeechSynthesizer]::new()
try {
    $chineseVoice = @($sampleSpeech.GetInstalledVoices() | Where-Object {
        $_.Enabled -and $_.VoiceInfo.Name -eq $voiceName -and $_.VoiceInfo.Culture.Name -eq 'zh-CN'
    })
    if ($chineseVoice.Count -ne 1) {
        throw "Required enabled zh-CN voice is unavailable: $voiceName"
    }
    $sampleSpeech.SelectVoice($voiceName)
    $sampleSpeech.Rate = 0
    $sampleSpeech.Volume = 100
    $sampleSpeech.SetOutputToWaveFile($wavPath)
    $sampleSpeech.Speak($sampleText)
} finally {
    $sampleSpeech.Dispose()
}

$wavStream = [System.IO.File]::OpenRead($wavPath)
try {
    $header = New-Object byte[] 12
    if ($wavStream.Read($header, 0, $header.Length) -ne 12 -or $wavStream.Length -le 44) {
        throw 'Generated WAV is empty or truncated.'
    }
    if ([System.Text.Encoding]::ASCII.GetString($header, 0, 4) -ne 'RIFF' -or
        [System.Text.Encoding]::ASCII.GetString($header, 8, 4) -ne 'WAVE') {
        throw 'Generated audio does not have a RIFF/WAVE header.'
    }
} finally {
    $wavStream.Dispose()
}

$wavDuration = & $ffprobePath -v error -show_entries format=duration -of 'default=noprint_wrappers=1:nokey=1' $wavPath
if ($LASTEXITCODE -ne 0) {
    throw "FFprobe failed to read WAV duration with exit code $LASTEXITCODE."
}
$wavDurationSeconds = [double]::Parse($wavDuration, [System.Globalization.CultureInfo]::InvariantCulture)
if ($wavDurationSeconds -le 0) {
    throw 'Generated WAV has no positive duration.'
}

& $ffmpegPath -hide_banner -loglevel error -nostdin -y -f lavfi -i 'color=c=0x19324D:s=640x360:r=10' -i $wavPath -map 0:v:0 -map 1:a:0 -shortest -t $wavDuration -c:v libx264 -pix_fmt yuv420p -c:a aac -b:a 128k -movflags +faststart $mp4Path
if ($LASTEXITCODE -ne 0) {
    throw "FFmpeg sample generation failed with exit code $LASTEXITCODE."
}

$mediaInfo = foreach ($mediaPath in @($wavPath, $mp4Path)) {
    $probeJson = & $ffprobePath -v error -show_entries 'format=duration,size:stream=index,codec_name,codec_type,sample_rate,channels,width,height,duration' -of json $mediaPath
    if ($LASTEXITCODE -ne 0) {
        throw "FFprobe failed for $mediaPath with exit code $LASTEXITCODE."
    }
    $probe = ($probeJson -join "`n") | ConvertFrom-Json
    if ([double]::Parse($probe.format.duration, [System.Globalization.CultureInfo]::InvariantCulture) -le 0 -or
        @($probe.streams | Where-Object { $_.codec_type -eq 'audio' }).Count -ne 1) {
        throw "Generated sample has no usable audio stream: $mediaPath"
    }
    if ($mediaPath -eq $mp4Path -and @($probe.streams | Where-Object { $_.codec_type -eq 'video' }).Count -ne 1) {
        throw 'Generated MP4 has no video stream.'
    }
    if ($mediaPath -eq $mp4Path -and [Math]::Abs([double]::Parse($probe.format.duration, [System.Globalization.CultureInfo]::InvariantCulture) - $wavDurationSeconds) -gt 0.15) {
        throw 'Generated MP4 duration does not match the WAV duration within one video frame plus encoding tolerance.'
    }
    [ordered]@{
        path = $mediaPath
        sha256 = (Get-FileHash -LiteralPath $mediaPath -Algorithm SHA256).Hash.ToLowerInvariant()
        format = $probe.format
        streams = @($probe.streams)
    }
}

[System.IO.File]::WriteAllText($textPath, $sampleText, [System.Text.UTF8Encoding]::new($false))
$manifest = [ordered]@{
    generated_utc = [DateTime]::UtcNow.ToString('o')
    voice = $voiceName
    culture = 'zh-CN'
    text = $sampleText
    expected_facts = [ordered]@{
        budget_cny = 12800
        responsible_person = '李明'
        review_frequency = '每周五'
        action = '下周三之前完成原始资料归档'
    }
    wav_header = 'RIFF/WAVE'
    speech_recognition_tested = $false
    media = @($mediaInfo)
}
$manifestJson = $manifest | ConvertTo-Json -Depth 8
[System.IO.File]::WriteAllText($manifestPath, $manifestJson, [System.Text.UTF8Encoding]::new($false))
$manifestJson

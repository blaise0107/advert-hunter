param(
    [string]$Path,
    [string]$Lang = "ru",
    [switch]$Server
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding = [System.Text.Encoding]::UTF8

Add-Type -AssemblyName System.Runtime.WindowsRuntime

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}

[Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime] | Out-Null
[Windows.Globalization.Language, Windows.Globalization, ContentType=WindowsRuntime] | Out-Null

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new($Lang))
if ($engine -eq $null) { Write-Error "OCR для языка $Lang недоступен"; exit 2 }

function Recognize-File([string]$ImagePath) {
    $stream = $null
    $bitmap = $null
    try {
        $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])
        $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

        $lines = @()
        foreach ($line in $result.Lines) {
            $words = @()
            foreach ($w in $line.Words) {
                $words += [pscustomobject]@{
                    t = $w.Text
                    x = [int]$w.BoundingRect.X
                    y = [int]$w.BoundingRect.Y
                    w = [int]$w.BoundingRect.Width
                    h = [int]$w.BoundingRect.Height
                }
            }
            $lines += [pscustomobject]@{
                text = $line.Text
                x = ($words | Measure-Object -Property x -Minimum).Minimum
                y = ($words | Measure-Object -Property y -Minimum).Minimum
                words = $words
            }
        }
        # Массив остаётся массивом даже при нуле или одной строке.
        ConvertTo-Json -InputObject @($lines) -Depth 5 -Compress
    } finally {
        if ($null -ne $bitmap) { $bitmap.Dispose() }
        if ($null -ne $stream) { $stream.Dispose() }
    }
}

if ($Server) {
    # Один процесс и один движок на весь прогон, без запуска PowerShell на кадр.
    while ($null -ne ($request = [Console]::ReadLine())) {
        try {
            $inputData = $request | ConvertFrom-Json
            [Console]::WriteLine((Recognize-File $inputData.path))
        } catch {
            [Console]::WriteLine((ConvertTo-Json -InputObject @{ error = $_.Exception.Message } -Compress))
        }
    }
} else {
    if ([string]::IsNullOrWhiteSpace($Path)) { Write-Error "Укажите -Path или -Server"; exit 2 }
    Recognize-File ([System.IO.Path]::GetFullPath($Path))
}

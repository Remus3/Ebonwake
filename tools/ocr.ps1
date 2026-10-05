# Ebonwake OCR (plan 009 slice A). Windows PowerShell 5.1, ASCII + LF.
#
# Usage: powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass
#        -File tools/ocr.ps1 <image path>
#
# Reads ONE operator screenshot (read-only) with the built-in Windows.Media.Ocr
# engine and writes ONE JSON object to stdout:
#   {"text": "...", "lines": [{"text": "...", "x": 0, "y": 0, "w": 0, "h": 0}]}
# Coordinates are pixels of the original image. On failure it writes
# {"error": "..."} and exits 1. Output is pure ASCII (non-ASCII is \u-escaped).
# Never touches the game: no process, window, memory or input.

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Path
)

$ErrorActionPreference = 'Stop'

function Write-AsciiJson($obj) {
    $json = ConvertTo-Json -InputObject $obj -Depth 6 -Compress
    $sb = New-Object System.Text.StringBuilder
    foreach ($ch in $json.ToCharArray()) {
        $code = [int]$ch
        if ($code -lt 128) {
            [void]$sb.Append($ch)
        } else {
            [void]$sb.Append(('\u{0:x4}' -f $code))
        }
    }
    [Console]::Out.Write($sb.ToString())
    [Console]::Out.Flush()
}

$script:AsTaskGeneric = $null

function Wait-WinRt($operation, [Type]$resultType) {
    # Await a WinRT IAsyncOperation<T> via System.WindowsRuntimeSystemExtensions.AsTask.
    $method = $script:AsTaskGeneric.MakeGenericMethod($resultType)
    $task = $method.Invoke($null, @($operation))
    [void]$task.Wait(-1)
    return $task.Result
}

$stream = $null
try {
    $full = [System.IO.Path]::GetFullPath($Path)
    if (-not [System.IO.File]::Exists($full)) {
        throw 'image file not found'
    }

    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
    $null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
    $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
    $null = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType = WindowsRuntime]

    $script:AsTaskGeneric = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object {
            $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and
            $_.GetParameters().Count -eq 1 -and
            $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
        } | Select-Object -First 1
    if ($null -eq $script:AsTaskGeneric) {
        throw 'AsTask(IAsyncOperation) not found'
    }

    $file = Wait-WinRt ([Windows.Storage.StorageFile]::GetFileFromPathAsync($full)) ([Windows.Storage.StorageFile])
    $stream = Wait-WinRt ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Wait-WinRt ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])

    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if ($null -eq $engine) {
        throw 'no OCR engine for the user profile languages'
    }

    # The engine refuses images over MaxImageDimension; scale down and map back.
    $maxDim = [double][Windows.Media.Ocr.OcrEngine]::MaxImageDimension
    $width = [double]$decoder.PixelWidth
    $height = [double]$decoder.PixelHeight
    $scale = 1.0
    if ($width -gt $maxDim -or $height -gt $maxDim) {
        $scale = [Math]::Min($maxDim / $width, $maxDim / $height)
        $transform = New-Object Windows.Graphics.Imaging.BitmapTransform
        $transform.ScaledWidth = [uint32][Math]::Floor($width * $scale)
        $transform.ScaledHeight = [uint32][Math]::Floor($height * $scale)
        $transform.InterpolationMode = [Windows.Graphics.Imaging.BitmapInterpolationMode]::Fant
        $bitmapOp = $decoder.GetSoftwareBitmapAsync(
            [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
            [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied,
            $transform,
            [Windows.Graphics.Imaging.ExifOrientationMode]::IgnoreExifOrientation,
            [Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage)
    } else {
        $bitmapOp = $decoder.GetSoftwareBitmapAsync()
    }
    $bitmap = Wait-WinRt $bitmapOp ([Windows.Graphics.Imaging.SoftwareBitmap])

    $result = Wait-WinRt ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

    $lines = New-Object System.Collections.Generic.List[object]
    $texts = New-Object System.Collections.Generic.List[string]
    foreach ($line in $result.Lines) {
        $x0 = [double]::MaxValue
        $y0 = [double]::MaxValue
        $x1 = 0.0
        $y1 = 0.0
        foreach ($word in $line.Words) {
            $r = $word.BoundingRect
            $x0 = [Math]::Min($x0, [double]$r.X)
            $y0 = [Math]::Min($y0, [double]$r.Y)
            $x1 = [Math]::Max($x1, [double]$r.X + [double]$r.Width)
            $y1 = [Math]::Max($y1, [double]$r.Y + [double]$r.Height)
        }
        if ($x0 -eq [double]::MaxValue) {
            $x0 = 0.0
            $y0 = 0.0
        }
        $lines.Add([ordered]@{
            text = [string]$line.Text
            x = [int][Math]::Round($x0 / $scale)
            y = [int][Math]::Round($y0 / $scale)
            w = [int][Math]::Round([Math]::Max(0.0, $x1 - $x0) / $scale)
            h = [int][Math]::Round([Math]::Max(0.0, $y1 - $y0) / $scale)
        })
        $texts.Add([string]$line.Text)
    }

    Write-AsciiJson ([ordered]@{
        text = ($texts.ToArray() -join "`n")
        lines = $lines.ToArray()
    })
    exit 0
} catch {
    $msg = [string]$_.Exception.Message
    if ($_.Exception.InnerException) {
        $msg = [string]$_.Exception.InnerException.Message
    }
    Write-AsciiJson ([ordered]@{ error = $msg })
    exit 1
} finally {
    if ($null -ne $stream) {
        try { $stream.Dispose() } catch { }
    }
}

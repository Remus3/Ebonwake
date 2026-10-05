# Ebonwake OCR benchmark helper (plan 009 follow-up). Windows PowerShell 5.1,
# ASCII + LF. Driven by tools/ocr_bench.py; never touches the game.
#
#   ocr_bench.ps1 render <manifest.json> <outdir>
#       Renders each case {id, text, font, px, fg, bg, frame} to <outdir>\<id>.png
#       with System.Drawing (ClearType-free antialias, like a game UI). frame=1
#       places the text on a 1920x1080 dark canvas with distractor UI text.
#   ocr_bench.ps1 winocr <list.txt> <scale>
#       Windows.Media.Ocr over every path in list.txt (engine loaded once), the
#       image upscaled by <scale> first (1 = as is). One JSON object per line:
#       {"path", "text", "lines": [{text,x,y,w,h}]} in original pixels.
#   ocr_bench.ps1 prep <list.txt> <scale>,<outdir>
#       Grayscale, invert if mostly dark, bicubic upscale; prints each out path.

param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Mode,
    [Parameter(Mandatory = $true, Position = 1)][string]$Arg1,
    [Parameter(Mandatory = $true, Position = 2)][string]$Arg2
)

$ErrorActionPreference = 'Stop'

function Get-Color([string]$hex) {
    return [System.Drawing.ColorTranslator]::FromHtml($hex)
}

function Invoke-Render([string]$manifest, [string]$outdir) {
    Add-Type -AssemblyName System.Drawing
    $cases = Get-Content -Raw -LiteralPath $manifest | ConvertFrom-Json
    [void](New-Item -ItemType Directory -Force -Path $outdir)
    foreach ($c in $cases) {
        $font = New-Object System.Drawing.Font($c.font, [single]$c.px, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
        $probe = New-Object System.Drawing.Bitmap(4, 4)
        $pg = [System.Drawing.Graphics]::FromImage($probe)
        $size = $pg.MeasureString([string]$c.text, $font)
        $pg.Dispose()
        $probe.Dispose()
        if ([int]$c.frame -eq 1) {
            $w = 1920
            $h = 1080
        } else {
            $w = [int][Math]::Ceiling($size.Width) + 40
            $h = [int][Math]::Ceiling($size.Height) + 24
        }
        $bmp = New-Object System.Drawing.Bitmap($w, $h)
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
        $g.Clear((Get-Color $c.bg))
        $brush = New-Object System.Drawing.SolidBrush((Get-Color $c.fg))
        if ([int]$c.frame -eq 1) {
            $dim = New-Object System.Drawing.SolidBrush((Get-Color '#8a8a8a'))
            $small = New-Object System.Drawing.Font($c.font, [single]12, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
            $g.DrawString('Inventory   Weight 1,203.4 / 2,150 LT', $small, $dim, 1380, 300)
            $g.DrawString('Lv.62  Deadeye  Calpheon', $small, $dim, 40, 40)
            $g.DrawString('Pearls 0', $small, $dim, 1380, 900)
            $g.FillRectangle($dim, 1380, 330, 480, 2)
            $g.DrawString([string]$c.text, $font, $brush, 1400, 860)
            $dim.Dispose()
            $small.Dispose()
        } else {
            $g.DrawString([string]$c.text, $font, $brush, 20, 12)
        }
        $bmp.Save((Join-Path $outdir ($c.id + '.png')), [System.Drawing.Imaging.ImageFormat]::Png)
        $brush.Dispose()
        $g.Dispose()
        $bmp.Dispose()
        $font.Dispose()
    }
}

$script:AsTaskGeneric = $null
function Wait-WinRt($operation, [Type]$resultType) {
    $method = $script:AsTaskGeneric.MakeGenericMethod($resultType)
    $task = $method.Invoke($null, @($operation))
    [void]$task.Wait(-1)
    return $task.Result
}

function Invoke-WinOcr([string]$list, [double]$scale) {
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
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    $maxDim = [double][Windows.Media.Ocr.OcrEngine]::MaxImageDimension
    foreach ($p in (Get-Content -LiteralPath $list)) {
        if (-not $p) { continue }
        $full = [System.IO.Path]::GetFullPath($p)
        $file = Wait-WinRt ([Windows.Storage.StorageFile]::GetFileFromPathAsync($full)) ([Windows.Storage.StorageFile])
        $stream = Wait-WinRt ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $decoder = Wait-WinRt ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $s = $scale
        $width = [double]$decoder.PixelWidth
        $height = [double]$decoder.PixelHeight
        $s = [Math]::Min($s, [Math]::Min($maxDim / $width, $maxDim / $height))
        $transform = New-Object Windows.Graphics.Imaging.BitmapTransform
        $transform.ScaledWidth = [uint32][Math]::Floor($width * $s)
        $transform.ScaledHeight = [uint32][Math]::Floor($height * $s)
        $transform.InterpolationMode = [Windows.Graphics.Imaging.BitmapInterpolationMode]::Cubic
        $bitmap = Wait-WinRt ($decoder.GetSoftwareBitmapAsync(
            [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
            [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied,
            $transform,
            [Windows.Graphics.Imaging.ExifOrientationMode]::IgnoreExifOrientation,
            [Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage)) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $result = Wait-WinRt ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        $lines = @()
        $texts = @()
        foreach ($line in $result.Lines) {
            $x0 = [double]::MaxValue; $y0 = [double]::MaxValue; $x1 = 0.0; $y1 = 0.0
            foreach ($word in $line.Words) {
                $r = $word.BoundingRect
                $x0 = [Math]::Min($x0, [double]$r.X); $y0 = [Math]::Min($y0, [double]$r.Y)
                $x1 = [Math]::Max($x1, [double]$r.X + [double]$r.Width)
                $y1 = [Math]::Max($y1, [double]$r.Y + [double]$r.Height)
            }
            if ($x0 -eq [double]::MaxValue) { $x0 = 0.0; $y0 = 0.0 }
            $lines += [ordered]@{
                text = [string]$line.Text
                x = [int][Math]::Round($x0 / $s); y = [int][Math]::Round($y0 / $s)
                w = [int][Math]::Round([Math]::Max(0.0, $x1 - $x0) / $s)
                h = [int][Math]::Round([Math]::Max(0.0, $y1 - $y0) / $s)
            }
            $texts += [string]$line.Text
        }
        $stream.Dispose()
        $obj = [ordered]@{ path = [string]$p; text = [string]($texts -join "`n"); lines = $lines }
        [Console]::Out.WriteLine((ConvertTo-Json -InputObject $obj -Depth 6 -Compress))
    }
}

function Invoke-Prep([string]$list, [string]$spec) {
    # spec "<scale>,<outdir>": the production preprocessing (tools/ocr_prep.ps1).
    . (Join-Path $PSScriptRoot 'ocr_prep.ps1')
    $parts = $spec.Split(',', 2)
    $scale = [double]$parts[0]
    $outdir = $parts[1]
    [void](New-Item -ItemType Directory -Force -Path $outdir)
    foreach ($p in (Get-Content -LiteralPath $list)) {
        if (-not $p) { continue }
        $out = Join-Path $outdir ([System.IO.Path]::GetFileName([string]$p))
        [void](Convert-OcrImage ([string]$p) $out $scale 8000.0)
        [Console]::Out.WriteLine($out)
    }
}

if ($Mode -eq 'render') {
    Invoke-Render $Arg1 $Arg2
} elseif ($Mode -eq 'prep') {
    Invoke-Prep $Arg1 $Arg2
} elseif ($Mode -eq 'winocr') {
    Invoke-WinOcr $Arg1 ([double]$Arg2)
} else {
    throw "unknown mode $Mode"
}

# Ebonwake OCR preprocessing for Tesseract (plan 009 follow-up). Windows
# PowerShell 5.1, ASCII + LF.
#
# Usage: powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass
#        -File tools/ocr_prep.ps1 -In <image> -Out <png> -Scale <n> [-MaxDim <px>]
#
# Reads ONE operator screenshot (read-only) and writes a grayscale PNG, inverted
# when the image is mostly dark (Tesseract wants dark text on light), upscaled
# with bicubic interpolation by Scale, capped so neither side exceeds MaxDim.
# Prints {"out": path, "scale": applied} as ASCII JSON; {"error"} + exit 1 on
# failure. tools/ocr_bench.ps1 dot-sources this file for Convert-OcrImage.
# Never touches the game: no process, window, memory or input.

param(
    [string]$In = '',
    [string]$Out = '',
    [double]$Scale = 3.0,
    [double]$MaxDim = 8000.0
)

function Convert-OcrImage([string]$src, [string]$dst, [double]$scale, [double]$maxDim) {
    Add-Type -AssemblyName System.Drawing
    $img = New-Object System.Drawing.Bitmap($src)
    try {
        $s = [Math]::Min($scale, [Math]::Min($maxDim / $img.Width, $maxDim / $img.Height))
        $s = [Math]::Max($s, 0.1)
        $w = [int][Math]::Max(1, [Math]::Floor($img.Width * $s))
        $h = [int][Math]::Max(1, [Math]::Floor($img.Height * $s))
        $sum = 0.0
        $n = 0
        $sy = [Math]::Max(1, [int]($img.Height / 40))
        $sx = [Math]::Max(1, [int]($img.Width / 40))
        for ($y = 0; $y -lt $img.Height; $y += $sy) {
            for ($x = 0; $x -lt $img.Width; $x += $sx) {
                $sum += $img.GetPixel($x, $y).GetBrightness()
                $n += 1
            }
        }
        $dark = ($sum / [Math]::Max(1, $n)) -lt 0.5
        $k = 1.0
        $off = 0.0
        if ($dark) { $k = -1.0; $off = 1.0 }
        $cm = New-Object System.Drawing.Imaging.ColorMatrix
        $cm.Matrix00 = $k * 0.299; $cm.Matrix01 = $k * 0.299; $cm.Matrix02 = $k * 0.299
        $cm.Matrix10 = $k * 0.587; $cm.Matrix11 = $k * 0.587; $cm.Matrix12 = $k * 0.587
        $cm.Matrix20 = $k * 0.114; $cm.Matrix21 = $k * 0.114; $cm.Matrix22 = $k * 0.114
        $cm.Matrix33 = 1.0
        $cm.Matrix40 = $off; $cm.Matrix41 = $off; $cm.Matrix42 = $off; $cm.Matrix44 = 1.0
        $ia = New-Object System.Drawing.Imaging.ImageAttributes
        $ia.SetColorMatrix($cm)
        $bmp = New-Object System.Drawing.Bitmap($w, $h)
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $rect = New-Object System.Drawing.Rectangle(0, 0, $w, $h)
        $g.DrawImage($img, $rect, 0, 0, $img.Width, $img.Height, [System.Drawing.GraphicsUnit]::Pixel, $ia)
        $tmp = $dst + '.tmp'
        $bmp.Save($tmp, [System.Drawing.Imaging.ImageFormat]::Png)
        $g.Dispose()
        $bmp.Dispose()
        $ia.Dispose()
        Move-Item -LiteralPath $tmp -Destination $dst -Force
        return $s
    } finally {
        $img.Dispose()
    }
}

if ($In) {
    $ErrorActionPreference = 'Stop'
    try {
        $applied = Convert-OcrImage ([System.IO.Path]::GetFullPath($In)) ([System.IO.Path]::GetFullPath($Out)) $Scale $MaxDim
        [Console]::Out.Write((ConvertTo-Json -Compress -InputObject ([ordered]@{ out = [string]$Out; scale = [double]$applied })))
        exit 0
    } catch {
        $msg = [string]$_.Exception.Message -replace '[^\x20-\x7e]', '?'
        [Console]::Out.Write((ConvertTo-Json -Compress -InputObject ([ordered]@{ error = $msg })))
        exit 1
    }
}

# Ebonwake OCR ink probe (plan 009 follow-up, word-gap fix). Windows
# PowerShell 5.1, ASCII + LF.
#
# Usage: powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass
#        -File tools/ocr_ink.ps1 -In <png> -Rects "x,y,w,h;x,y,w,h" [-Level 0.5]
#
# Reads ONE preprocessed image (tools/ocr_prep.ps1 output: dark text on light)
# and prints {"ink": [f, ...]}: per rectangle, the fraction of its pixels darker
# than Level (brightness 0..1). A rectangle is clipped to the image; an empty
# one reports 0. {"error"} + exit 1 on failure. server/ew/ocr.py uses it to tell
# a comma Tesseract dropped (its tail leaves ink below the baseline) from a real
# space. Never touches the game: no process, window, memory or input.

param(
    [string]$In = '',
    [string]$Rects = '',
    [double]$Level = 0.5
)

function Measure-OcrInk([string]$src, [string]$rects, [double]$level) {
    Add-Type -AssemblyName System.Drawing
    $img = New-Object System.Drawing.Bitmap($src)
    try {
        $out = New-Object System.Collections.Generic.List[double]
        foreach ($spec in $rects.Split(';')) {
            if (-not $spec) { continue }
            $p = $spec.Split(',')
            if ($p.Count -ne 4) { throw "bad rect $spec" }
            $x0 = [Math]::Max(0, [int]$p[0])
            $y0 = [Math]::Max(0, [int]$p[1])
            $x1 = [Math]::Min($img.Width, [int]$p[0] + [int]$p[2])
            $y1 = [Math]::Min($img.Height, [int]$p[1] + [int]$p[3])
            $n = 0
            $dark = 0
            for ($y = $y0; $y -lt $y1; $y++) {
                for ($x = $x0; $x -lt $x1; $x++) {
                    $n += 1
                    if ($img.GetPixel($x, $y).GetBrightness() -lt $level) { $dark += 1 }
                }
            }
            if ($n -gt 0) { $out.Add([double]$dark / $n) } else { $out.Add(0.0) }
        }
        return ,$out.ToArray()
    } finally {
        $img.Dispose()
    }
}

if ($In) {
    $ErrorActionPreference = 'Stop'
    try {
        $ink = Measure-OcrInk ([System.IO.Path]::GetFullPath($In)) $Rects $Level
        [Console]::Out.Write((ConvertTo-Json -Compress -InputObject ([ordered]@{ ink = [double[]]$ink })))
        exit 0
    } catch {
        $msg = [string]$_.Exception.Message -replace '[^\x20-\x7e]', '?'
        [Console]::Out.Write((ConvertTo-Json -Compress -InputObject ([ordered]@{ error = $msg })))
        exit 1
    }
}

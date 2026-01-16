
# Resize-Image.ps1
param (
    [string]$SourceFile = "logo_header.png",
    [int[]]$Sizes = @(16, 48, 128)
)

Add-Type -AssemblyName System.Drawing

$sourcePath = Join-Path $PSScriptRoot $SourceFile

if (-not (Test-Path $sourcePath)) {
    Write-Error "Source file not found: $sourcePath"
    exit 1
}

$image = [System.Drawing.Image]::FromFile($sourcePath)

foreach ($size in $Sizes) {
    $targetPath = Join-Path $PSScriptRoot "icon$size.png"
    
    $bitmap = New-Object System.Drawing.Bitmap $size, $size
    $graph = [System.Drawing.Graphics]::FromImage($bitmap)
    
    $graph.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $graph.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
    $graph.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $graph.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
    
    $graph.DrawImage($image, 0, 0, $size, $size)
    
    $bitmap.Save($targetPath, [System.Drawing.Imaging.ImageFormat]::Png)
    
    $bitmap.Dispose()
    $graph.Dispose()
    
    Write-Host "Created $targetPath"
}

$image.Dispose()

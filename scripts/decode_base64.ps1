param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,
    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

$inputContent = Get-Content -Path $InputPath -Raw
$bytes = [System.Convert]::FromBase64String($inputContent)
[System.IO.File]::WriteAllBytes($OutputPath, $bytes)
Write-Output "Decoded $InputPath to $OutputPath"

param([Parameter(Mandatory=$true)][string]$Target)
$ErrorActionPreference = 'Stop'
$resolved = [IO.Path]::GetFullPath($Target)
$expected = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../authority-images/a3117c1630933eb7-xa1d5cpf'))
if ($resolved -ne $expected) { throw 'Target mismatch' }
$items = @(Get-ChildItem -LiteralPath $resolved -Force -Recurse -ErrorAction Stop)
if ($items.Count -gt 200) { throw 'Unexpected item count' }
$rows = @()
foreach ($item in $items) {
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse point' }
    $relative = [IO.Path]::GetRelativePath($resolved, $item.FullName).Replace('\','/')
    if ($item.PSIsContainer) { $rows += @{ path=$relative; directory=$true }; continue }
    $bytes = [IO.File]::ReadAllBytes($item.FullName)
    if ($bytes.Length -gt 1048576) { throw 'File limit' }
    $rows += @{ path=$relative; directory=$false; data=[Convert]::ToBase64String($bytes) }
}
ConvertTo-Json -InputObject $rows -Depth 4 -Compress

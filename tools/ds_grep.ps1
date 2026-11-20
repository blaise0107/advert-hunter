function ReadShared([string]$p){
  $fs=[IO.File]::Open((Resolve-Path $p),[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)
  $ms=New-Object IO.MemoryStream
  $fs.CopyTo($ms); $fs.Close()
  $bytes=$ms.ToArray()
  try {
    $u=New-Object Text.UTF8Encoding($false,$true)
    return $u.GetString($bytes)
  } catch {
    return [Text.Encoding]::GetEncoding(866).GetString($bytes)
  }
}
$pat='зелён|держим|выполнено \(|рано|кнопка|accepted|instructions_done|Открыть на|отлож'
foreach($f in @("runs\fab1701\resume.log","runs\238a1a84440b7ece\resume.log")){
  Write-Output "=== $f ==="
  $l=(ReadShared $f) -split "`r?`n"
  $tail=$l[-400..-1]
  $tail | Where-Object { $_ -match $pat } | Select-Object -Last 20
}

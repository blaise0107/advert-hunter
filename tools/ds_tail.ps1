param([string]$File,[int]$N=40)
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$fs=[IO.File]::Open((Resolve-Path $File),[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)
$ms=New-Object IO.MemoryStream
$fs.CopyTo($ms); $fs.Close()
$bytes=$ms.ToArray()
$texts=New-Object System.Collections.Generic.List[string]
$start=0
for($i=0;$i -lt $bytes.Length;$i++){
  if($bytes[$i] -eq 10){
    $len=$i-$start
    if($len -gt 0 -and $bytes[$i-1] -eq 13){ $len-- }
    $seg=New-Object byte[] $len
    [Array]::Copy($bytes,$start,$seg,0,$len)
    $s=$null
    try { $u=New-Object Text.UTF8Encoding($false,$true); $s=$u.GetString($seg) } catch { $s=[Text.Encoding]::GetEncoding(866).GetString($seg) }
    $texts.Add($s)
    $start=$i+1
  }
}
if($start -lt $bytes.Length){
  $seg=New-Object byte[] ($bytes.Length-$start)
  [Array]::Copy($bytes,$start,$seg,0,$seg.Length)
  try { $u=New-Object Text.UTF8Encoding($false,$true); $texts.Add($u.GetString($seg)) } catch { $texts.Add([Text.Encoding]::GetEncoding(866).GetString($seg)) }
}
$last=[Math]::Min($N,$texts.Count)
$texts.GetRange($texts.Count-$last,$last)

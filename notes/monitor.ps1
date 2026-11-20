$ErrorActionPreference = 'SilentlyContinue'
$out = 'C:\Users\RZE\AppData\Local\Temp\opencode\night_report.txt'
$lines = @()
$lines += "=== TIME: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="
$lines += '=== farm_status.json ==='
$lines += (Get-Content -LiteralPath 'farm_status.json' -Encoding UTF8)
$lines += '=== python processes (farm/worker) ==='
$lines += (Get-CimInstance Win32_Process -Filter "name='python.exe'" |
    Where-Object { $_.CommandLine -like '*farm.py*' -or $_.CommandLine -like '*strategy_engine.py*' } |
    Select-Object ProcessId, CreationDate, CommandLine | Format-Table -Wrap -AutoSize | Out-String).TrimEnd()
foreach ($p in @(
    'logs/strategy.log',
    'logs/farm.log',
    'runs/fab1701/resume.log',
    'runs/238a1a84440b7ece/resume.log',
    'runs/fab1701/hunter2.log',
    'runs/238a1a84440b7ece/hunter2.log'
)) {
    $lines += "=== $p (tail 30) ==="
    $lines += (Get-Content -LiteralPath $p -Tail 30 -Encoding UTF8)
}
foreach ($p in @('runs/fab1701/results.jsonl', 'runs/238a1a84440b7ece/results.jsonl')) {
    $lines += "=== $p (tail 5) ==="
    $lines += (Get-Content -LiteralPath $p -Tail 5 -Encoding UTF8)
}
$lines | Set-Content -LiteralPath $out -Encoding UTF8
Write-Output "OK $out"

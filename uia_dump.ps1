Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$proc = Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like 'AdvertApp Panel*' } | Select-Object -First 1
if (-not $proc) { "panel not found" | Set-Content -Path uia_dump.txt -Encoding UTF8; exit 1 }
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
$lines = New-Object System.Collections.Generic.List[string]
$r = $win.Current.BoundingRectangle
$lines.Add(("WINDOW x={0} y={1} w={2} h={3}" -f [int]$r.X, [int]$r.Y, [int]$r.Width, [int]$r.Height))
foreach ($el in $all) {
  try {
    $nm = $el.Current.Name
    if (-not $nm) { continue }
    $rr = $el.Current.BoundingRectangle
    if ($rr.Width -eq 0) { continue }
    $ct = $el.Current.ControlType.ProgrammaticName.Replace('ControlType.','')
    $lines.Add(("{0}|{1}|{2}|{3}|{4}|{5}" -f $ct, $nm, [int]$rr.X, [int]$rr.Y, [int]$rr.Width, [int]$rr.Height))
  } catch {}
}
$lines | Set-Content -Path uia_dump.txt -Encoding UTF8
"dumped {0} elements" -f $lines.Count

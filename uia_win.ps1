Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$proc = Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like 'AdvertApp Panel*' } | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
$r = $win.Current.BoundingRectangle
Write-Output ("WINDOW x={0:N0} y={1:N0} w={2:N0} h={3:N0}" -f $r.X, $r.Y, $r.Width, $r.Height)
$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
$n = 0
foreach ($el in $all) {
  try {
    $nm = $el.Current.Name
    if (-not $nm) { continue }
    if ($nm -notmatch '\p{IsCyrillic}') { continue }
    $rr = $el.Current.BoundingRectangle
    $ct = $el.Current.ControlType.ProgrammaticName.Replace('ControlType.','')
    Write-Output ("{0} | {1} | x={2:N0} y={3:N0} w={4:N0} h={5:N0}" -f $ct, $nm, $rr.X, $rr.Y, $rr.Width, $rr.Height)
    $n++
    if ($n -ge 120) { break }
  } catch {}
}

param([string]$Name = "")
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$proc = Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like 'AdvertApp Panel*' } | Select-Object -First 1
if (-not $proc) { Write-Output "panel not found"; exit 1 }
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
if (-not $win) { Write-Output "window not found"; exit 1 }
Write-Output ("window: " + $win.Current.Name)
$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
foreach ($el in $all) {
  try {
    $ct = $el.Current.ControlType.ProgrammaticName
    $nm = $el.Current.Name
    if (-not $nm) { continue }
    if ($ct -notmatch 'Button|ListItem|TabItem|MenuItem') { continue }
    $r = $el.Current.BoundingRectangle
    Write-Output ("{0} | {1} | x={2:N0} y={3:N0} w={4:N0} h={5:N0} | enabled={6}" -f $ct.Replace('ControlType.',''), $nm, $r.X, $r.Y, $r.Width, $r.Height, $el.Current.IsEnabled)
    if ($Name -and $nm -eq $Name) {
      $pat = $null
      if ($el.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$pat)) { $pat.Invoke(); Write-Output ("INVOKED " + $nm) }
      elseif ($el.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$pat)) { $pat.Select(); Write-Output ("SELECTED " + $nm) }
      else { Write-Output ("NO PATTERN " + $nm) }
    }
  } catch {}
}

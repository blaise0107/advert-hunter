Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
$sig = @"
[DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
[DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, int dwExtraInfo);
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
[DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr after, int X, int Y, int cx, int cy, uint flags);
"@
$m = Add-Type -MemberDefinition $sig -Name LiveSave -PassThru
$proc = Get-Process pythonw | Where-Object { $_.MainWindowTitle -like 'AdvertApp Panel*' } | Select-Object -First 1
if (-not $proc) { Write-Output "panel not found"; exit 1 }
Write-Output ("pid=" + $proc.Id)
$h = $proc.MainWindowHandle
$m::SetWindowPos($h, [IntPtr]::Zero, 0, 0, 0, 0, 0x0001 -bor 0x0004) | Out-Null
$m::SetForegroundWindow($h) | Out-Null
Start-Sleep -Milliseconds 600
$m::SetCursorPos(600, 570) | Out-Null
$m::mouse_event(0x0002,0,0,0,0); Start-Sleep -Milliseconds 60; $m::mouse_event(0x0004,0,0,0,0)
Start-Sleep -Milliseconds 400
[System.Windows.Forms.SendKeys]::SendWait("^a")
Start-Sleep -Milliseconds 150
[System.Windows.Forms.SendKeys]::SendWait("31")
Start-Sleep -Milliseconds 400
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
$btnCond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Button)
$btns = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $btnCond)
foreach ($b in $btns) {
  $r = $b.Current.BoundingRectangle
  if ($r.Y -gt 950 -and $r.X -lt 450) {
    $p = $null
    if ($b.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$p)) { $p.Invoke(); Write-Output ("invoked button at " + [int]$r.X + "," + [int]$r.Y) }
  }
}
Start-Sleep -Seconds 1
$j = Get-Content panel_settings.json -Raw -Encoding UTF8 | ConvertFrom-Json
Write-Output ("sleep=" + $j.cycle.sleep_seconds + " active_first=" + $j.cycle.active_first + " theme=" + $j.theme)
Write-Output ("mtime=" + (Get-Item panel_settings.json).LastWriteTime.ToString("HH:mm:ss"))

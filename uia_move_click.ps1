Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$sig = @"
[DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
[DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, int e);
[DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
"@
$m = Add-Type -MemberDefinition $sig -Name Click3 -PassThru
$proc = Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like 'AdvertApp Panel*' } | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
$hwnd = [IntPtr]$win.Current.NativeWindowHandle
$m::SetWindowPos($hwnd, [IntPtr]::Zero, 0, 0, 0, 0, 0x0001 -bor 0x0004) | Out-Null
Start-Sleep -Milliseconds 700
$r = $win.Current.BoundingRectangle
Write-Output ("moved: x={0} y={1} w={2} h={3}" -f [int]$r.X, [int]$r.Y, [int]$r.Width, [int]$r.Height)
$ws = New-Object -ComObject WScript.Shell
$ws.AppActivate($proc.Id) | Out-Null
Start-Sleep -Milliseconds 500
$m::SetCursorPos(114, 835) | Out-Null
Start-Sleep -Milliseconds 150
$m::mouse_event(0x0002,0,0,0,0); Start-Sleep -Milliseconds 90; $m::mouse_event(0x0004,0,0,0,0)
Start-Sleep -Seconds 2
$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
$lines = New-Object System.Collections.Generic.List[string]
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
$lines | Set-Content -Path uia_settings.txt -Encoding UTF8
Write-Output ("elements=" + $lines.Count)
Get-Date -Format 'HH:mm:ss'

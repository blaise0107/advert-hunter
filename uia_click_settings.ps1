Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type -AssemblyName System.Drawing
$sig = @"
[DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
[DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, int e);
[DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
"@
$m = Add-Type -MemberDefinition $sig -Name Click2 -PassThru
$proc = Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like 'AdvertApp Panel*' } | Select-Object -First 1
$ws = New-Object -ComObject WScript.Shell
$ws.AppActivate($proc.Id) | Out-Null
Start-Sleep -Milliseconds 700
$m::SetCursorPos(746, 1074) | Out-Null
Start-Sleep -Milliseconds 150
$m::mouse_event(0x0002,0,0,0,0); Start-Sleep -Milliseconds 90; $m::mouse_event(0x0004,0,0,0,0)
Start-Sleep -Seconds 2

$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
$all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
$lines = New-Object System.Collections.Generic.List[string]
foreach ($el in $all) {
  try {
    $nm = $el.Current.Name
    if (-not $nm) { continue }
    if ($el.Current.ControlType.ProgrammaticName -notmatch 'Text|Button|CheckBox|ComboBox|Spinner|Edit') { continue }
    $rr = $el.Current.BoundingRectangle
    if ($rr.Width -eq 0) { continue }
    $ct = $el.Current.ControlType.ProgrammaticName.Replace('ControlType.','')
    $lines.Add(("{0}|{1}|{2}|{3}|{4}|{5}" -f $ct, $nm, [int]$rr.X, [int]$rr.Y, [int]$rr.Width, [int]$rr.Height))
  } catch {}
}
$lines | Set-Content -Path uia_settings.txt -Encoding UTF8
Write-Output ("elements=" + $lines.Count)

$hwnd = [IntPtr]$win.Current.NativeWindowHandle
$r = $win.Current.BoundingRectangle
$w = [int]$r.Width; $h = [int]$r.Height
$bmp = New-Object System.Drawing.Bitmap $w, $h
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc()
$ok = $m::PrintWindow($hwnd, $hdc, 2)
$g.ReleaseHdc($hdc)
$g.Dispose()
$frames = Join-Path $PSScriptRoot 'frames'
New-Item -ItemType Directory -Force -Path $frames | Out-Null
$bmp.Save((Join-Path $frames 'panel_settings3.png'))
$bmp.Dispose()
Write-Output ("printwindow=" + $ok)
Get-Date -Format 'HH:mm:ss'

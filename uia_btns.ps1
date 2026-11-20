Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$proc = Get-Process pythonw | Where-Object { $_.MainWindowTitle -like 'AdvertApp Panel*' } | Select-Object -First 1
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
$r = $win.Current.BoundingRectangle
Write-Output ("WINDOW " + [int]$r.X + "," + [int]$r.Y + " " + [int]$r.Width + "x" + [int]$r.Height)
$btnCond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Button)
$btns = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $btnCond)
Write-Output ("buttons=" + $btns.Count)
foreach ($b in $btns) {
  $rr = $b.Current.BoundingRectangle
  $nm = $b.Current.Name
  if (-not $nm) { $nm = "<noname>" }
  Write-Output ("[" + $nm + "] " + [int]$rr.X + "," + [int]$rr.Y + " " + [int]$rr.Width + "x" + [int]$rr.Height)
}
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
$bnd = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap $bnd.Width, $bnd.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($bnd.Location, [System.Drawing.Point]::Empty, $bnd.Size)
$frames = Join-Path $PSScriptRoot 'frames'
New-Item -ItemType Directory -Force -Path $frames | Out-Null
$bmp.Save((Join-Path $frames 'panel_settings_live.png'))
$g.Dispose(); $bmp.Dispose()
Write-Output "shot saved"

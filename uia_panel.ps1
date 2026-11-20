Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$procs = Get-Process pythonw -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowTitle -like 'AdvertApp Panel*' }
if (-not $procs) {
    Write-Output "panel not found"
    exit 1
}
$panelPid = $procs[0].Id
Write-Output ("panel pid: " + $panelPid)

$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ProcessIdProperty, $panelPid)
$wins = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $cond)
Write-Output ("windows: " + $wins.Count)
if ($wins.Count -eq 0) { exit 1 }
$win = $wins[0]
Write-Output ("title: " + $win.Current.Name)

$btnCond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::Button)
$btns = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $btnCond)
Write-Output ("buttons: " + $btns.Count)

for ($i = 0; $i -lt $btns.Count; $i++) {
    $b = $btns[$i]
    $r = $b.Current.BoundingRectangle
    Write-Output ("{0}: '{1}' x={2} y={3} w={4} h={5}" -f $i, $b.Current.Name, [int]$r.X, [int]$r.Y, [int]$r.Width, [int]$r.Height)
}

$target = $null
for ($i = 0; $i -lt $btns.Count; $i++) {
    $r = $btns[$i].Current.BoundingRectangle
    if ($r.Y -gt 670 -and $r.Y -lt 700 -and $r.Width -gt 300) {
        $target = $btns[$i]
        break
    }
}
if (-not $target) {
    Write-Output "balance button not found"
    exit 2
}
$pattern = $target.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
$pattern.Invoke()
Write-Output ("invoked: " + $target.Current.Name)

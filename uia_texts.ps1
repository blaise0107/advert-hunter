Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$root = [System.Windows.Automation.AutomationElement]::RootElement
$win = $null
$all = $root.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
foreach ($w in $all) {
    if ($w.Current.Name -like 'AdvertApp Panel*') { $win = $w; break }
}
if (-not $win) { Write-Output 'panel not found'; exit 1 }
Write-Output ("panel: " + $win.Current.Name + " pid=" + $win.Current.ProcessId)

$cond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::Text)
$texts = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)
foreach ($t in $texts) {
    $r = $t.Current.BoundingRectangle
    $n = $t.Current.Name
    if ($n) {
        Write-Output ("text: '{0}' @ ({1},{2}) {3}x{4}" -f $n, [int]$r.X, [int]$r.Y, [int]$r.Width, [int]$r.Height)
    }
}

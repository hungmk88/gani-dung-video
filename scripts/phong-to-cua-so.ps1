# Phong to cua so ung dung GANI (Edge/Chrome --app) sau khi no mo.
# Ly do: --start-maximized bi bo qua khi Edge da co phien chay san, nen cua so van cao ~1050 px va bi cat khung
# kiem tra ben phai. Ta tim cua so co tieu de bat dau bang "GANI FOODY" roi gui lenh SW_MAXIMIZE.
# Khong co cua so sau ~20 giay thi thoat im lang (khong lam hong viec mo app).
param([string]$TitlePrefix = "GANI FOODY", [int]$TimeoutSec = 20)

Add-Type -Namespace Gani -Name Win -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern bool ShowWindowAsync(System.IntPtr hWnd, int nCmdShow);
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern bool SetForegroundWindow(System.IntPtr hWnd);
'@

$SW_MAXIMIZE = 3
$deadline = (Get-Date).AddSeconds($TimeoutSec)
while ((Get-Date) -lt $deadline) {
    $win = Get-Process -Name msedge, chrome -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -like "$TitlePrefix*" } |
        Select-Object -First 1
    if ($win) {
        [void][Gani.Win]::ShowWindowAsync($win.MainWindowHandle, $SW_MAXIMIZE)
        [void][Gani.Win]::SetForegroundWindow($win.MainWindowHandle)
        exit 0
    }
    Start-Sleep -Milliseconds 400
}
exit 0

# Copyright (c) 2026 alien0101x - DeepSeekBridge
# github.com/alien0101x/DeepSeekBridge - MIT License
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class WinMove {
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr a, int x, int y, int w, int ht, uint f);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
}
"@

$targetX = 60; $targetY = 60
if ($args.Count -ge 2) { $targetX = [int]$args[0]; $targetY = [int]$args[1] }

$moved = 0
# Try chrome, chromium, and any process with chrome in the name
$procs = Get-Process -Name chrome, chromium -ErrorAction SilentlyContinue
if (-not $procs) {
    $procs = Get-Process | Where-Object { $_.ProcessName -match 'chrome' -and $_.MainWindowHandle -ne 0 }
}
$procs | Where-Object { $_.MainWindowHandle -ne 0 } | ForEach-Object {
    $h = $_.MainWindowHandle
    if ($targetX -lt 0) {
        # Hide: move off-screen
        [WinMove]::ShowWindow($h, 6) | Out-Null  # SW_MINIMIZE
        [WinMove]::SetWindowPos($h, [IntPtr]::Zero, -32000, -32000, 0, 0, 0x0001) | Out-Null
    } else {
        # Show: restore and move
        [WinMove]::ShowWindow($h, 9) | Out-Null  # SW_RESTORE
        [WinMove]::SetWindowPos($h, [IntPtr]::Zero, $targetX, $targetY, 1280, 900, 0x0040) | Out-Null
    }
    $moved++
}

if ($moved -eq 0) {
    Write-Output "No Chrome window found. Is the bridge running?"
} else {
    $action = if ($targetX -lt 0) { "Hidden" } else { "Shown at ${targetX},${targetY}" }
    Write-Output "$action $moved Chrome window(s)"
}

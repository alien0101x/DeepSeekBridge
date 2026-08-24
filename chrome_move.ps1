# Copyright (c) 2026 alien0101x - DeepSeekBridge
# github.com/alien0101x/DeepSeekBridge - MIT License
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class WinMove {
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr a, int x, int y, int w, int ht, uint f);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
}
"@
$targetX = 60; $targetY = 60
if ($args.Count -ge 2) { $targetX = [int]$args[0]; $targetY = [int]$args[1] }

$moved = 0
Get-Process chrome -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | ForEach-Object {
    [WinMove]::ShowWindow($_.MainWindowHandle, 9) | Out-Null   # SW_RESTORE
    [WinMove]::SetWindowPos($_.MainWindowHandle, [IntPtr]::Zero, $targetX, $targetY, 1280, 900, 0x0040) | Out-Null
    $moved++
}
Write-Output ("Moved $moved Chrome window(s) to ${targetX},${targetY}")

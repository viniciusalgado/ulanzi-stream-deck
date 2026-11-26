<#
  Switch monitor inputs over DDC/CI (MCCS VCP code 0x60) using Windows' built-in dxva2 API.

  Monitors are found by the model in their DDC capabilities string, not by Windows'
  display order, because Windows labels both as "Generic PnP Monitor".

  Examples:
    .\MonitorInput.ps1 -List
    .\MonitorInput.ps1 -Target company
    .\MonitorInput.ps1 -Target personal
    .\MonitorInput.ps1 -Test 24G2 -HoldSeconds 6   # switch to the company input, wait, switch back
#>
[CmdletBinding(DefaultParameterSetName = 'List')]
param(
  [Parameter(ParameterSetName = 'List')][switch]$List,
  [Parameter(ParameterSetName = 'Target', Mandatory)][ValidateSet('personal', 'company')][string]$Target,
  [Parameter(ParameterSetName = 'Test', Mandatory)][ValidateSet('27G2', '24G2')][string]$Test,
  [Parameter(ParameterSetName = 'Test')][int]$HoldSeconds = 6
)

# Input codes per monitor. MCCS: 0x01 VGA (D-sub), 0x0F DisplayPort 1, 0x11 HDMI 1, 0x12 HDMI 2.
$Monitors = @{
  '27G2' = @{ CapsModel = '27G2';    personal = 0x0F; company = 0x12 }  # PC on DP, laptop on HDMI 2
  '24G2' = @{ CapsModel = '24G2HE5'; personal = 0x11; company = 0x01 }  # PC on HDMI, laptop on D-sub
}

if (-not ('Ddc' -as [type])) {
  Add-Type -TypeDefinition @'
using System; using System.Collections.Generic; using System.Runtime.InteropServices; using System.Text;
public static class Ddc {
  [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
  public struct PM { public IntPtr h; [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string d; }
  delegate bool EnumProc(IntPtr hMon, IntPtr hdc, IntPtr rc, IntPtr data);
  [DllImport("user32.dll")] static extern bool EnumDisplayMonitors(IntPtr hdc, IntPtr clip, EnumProc cb, IntPtr data);
  [DllImport("dxva2.dll")] static extern bool GetNumberOfPhysicalMonitorsFromHMONITOR(IntPtr h, out uint n);
  [DllImport("dxva2.dll")] static extern bool GetPhysicalMonitorsFromHMONITOR(IntPtr h, uint n, [Out] PM[] a);
  [DllImport("dxva2.dll")] static extern bool GetCapabilitiesStringLength(IntPtr h, out uint len);
  [DllImport("dxva2.dll", CharSet = CharSet.Ansi)] static extern bool CapabilitiesRequestAndCapabilitiesReply(IntPtr h, StringBuilder s, uint len);
  [DllImport("dxva2.dll")] static extern bool SetVCPFeature(IntPtr h, byte code, uint value);
  [DllImport("dxva2.dll")] static extern bool DestroyPhysicalMonitor(IntPtr h);

  // Returns "model|caps" for every physical monitor that answers DDC/CI.
  public static List<string> List() {
    var result = new List<string>();
    ForEach(delegate(IntPtr h, string caps) { result.Add(Model(caps) + "|" + caps); return false; });
    return result;
  }

  // Sets the input on the monitor whose caps string contains model. Returns false if not found or rejected.
  public static bool SetInput(string model, uint value) {
    bool ok = false;
    ForEach(delegate(IntPtr h, string caps) {
      if (caps.IndexOf("model(" + model + ")", StringComparison.OrdinalIgnoreCase) < 0) return false;
      ok = SetVCPFeature(h, 0x60, value); return true;
    });
    return ok;
  }

  static string Model(string caps) {
    int i = caps.IndexOf("model("); if (i < 0) return "?";
    int j = caps.IndexOf(')', i); return caps.Substring(i + 6, j - i - 6);
  }

  // Calls fn for each physical monitor with its caps string; fn returns true to stop.
  static void ForEach(Func<IntPtr, string, bool> fn) {
    bool stop = false;
    EnumDisplayMonitors(IntPtr.Zero, IntPtr.Zero, delegate(IntPtr hm, IntPtr a, IntPtr b, IntPtr c) {
      uint n; if (!GetNumberOfPhysicalMonitorsFromHMONITOR(hm, out n) || n == 0) return true;
      var arr = new PM[n]; if (!GetPhysicalMonitorsFromHMONITOR(hm, n, arr)) return true;
      foreach (var m in arr) {
        if (!stop) {
          uint len; string caps = "";
          if (GetCapabilitiesStringLength(m.h, out len)) {
            var s = new StringBuilder((int)len);
            if (CapabilitiesRequestAndCapabilitiesReply(m.h, s, len)) caps = s.ToString();
          }
          stop = fn(m.h, caps);
        }
        DestroyPhysicalMonitor(m.h);
      }
      return !stop;
    }, IntPtr.Zero);
  }
}
'@
}

function Set-Input([string]$name, [string]$side) {
  $m = $Monitors[$name]
  $ok = [Ddc]::SetInput($m.CapsModel, [uint32]$m[$side])
  Write-Host ('{0}: -> {1} (0x{2:X2}) {3}' -f $name, $side, $m[$side], $(if ($ok) { 'sent' } else { 'FAILED (monitor not found or command rejected)' }))
  return $ok
}

switch ($PSCmdlet.ParameterSetName) {
  'List' {
    foreach ($row in [Ddc]::List()) { $model, $caps = $row -split '\|', 2; "{0,-10} {1}" -f $model, $caps }
  }
  'Target' {
    foreach ($name in $Monitors.Keys) { [void](Set-Input $name $Target) }
  }
  'Test' {
    [void](Set-Input $Test 'company')
    Start-Sleep -Seconds $HoldSeconds
    $visible = [Ddc]::List() | Where-Object { $_ -like "$($Monitors[$Test].CapsModel)|*" }
    "{0}: still visible to this PC while on the other input: {1}" -f $Test, [bool]$visible
    # Retry the switch back a few times; a DP monitor may need a moment to reappear.
    $back = $false
    for ($i = 0; $i -lt 5 -and -not $back; $i++) {
      $back = Set-Input $Test 'personal'
      if (-not $back) { Start-Sleep -Seconds 2 }
    }
    if (-not $back) { "Could not switch back automatically. Use the monitor's own input button." }
  }
}

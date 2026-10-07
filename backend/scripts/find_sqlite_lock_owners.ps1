param([Parameter(Mandatory=$true)][string[]]$DatabasePaths)
# Read-only Restart Manager query. Never stop/restart an owning process.
Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
public static class MusicerLockOwners {
  [StructLayout(LayoutKind.Sequential)] public struct UniqueProcess {
    public int ProcessId; public FILETIME StartTime;
  }
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] public struct ProcessInfo {
    public UniqueProcess Process;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=256)] public string AppName;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=64)] public string ServiceName;
    public int AppType; public uint AppStatus; public uint SessionId;
    [MarshalAs(UnmanagedType.Bool)] public bool Restartable;
  }
  [DllImport("rstrtmgr.dll", CharSet=CharSet.Unicode)] static extern int RmStartSession(out uint session, uint flags, StringBuilder key);
  [DllImport("rstrtmgr.dll", CharSet=CharSet.Unicode)] static extern int RmRegisterResources(uint session, uint count, string[] files, uint apps, UniqueProcess[] processes, uint services, string[] serviceNames);
  [DllImport("rstrtmgr.dll")] static extern int RmGetList(uint session, out uint needed, ref uint count, [In,Out] ProcessInfo[] info, ref uint reasons);
  [DllImport("rstrtmgr.dll")] static extern int RmEndSession(uint session);
  public static ProcessInfo[] Query(string[] paths) {
    uint session; var key = new StringBuilder(33);
    int result = RmStartSession(out session, 0, key);
    if (result != 0) throw new Exception("RmStartSession: " + result);
    try {
      result = RmRegisterResources(session, (uint)paths.Length, paths, 0, null, 0, null);
      if (result != 0) throw new Exception("RmRegisterResources: " + result);
      uint needed, count = 0, reasons = 0;
      result = RmGetList(session, out needed, ref count, null, ref reasons);
      if (result == 0) return new ProcessInfo[0];
      if (result != 234) throw new Exception("RmGetList: " + result);
      var list = new ProcessInfo[needed]; count = needed;
      result = RmGetList(session, out needed, ref count, list, ref reasons);
      if (result != 0) throw new Exception("RmGetList: " + result);
      Array.Resize(ref list, (int)count); return list;
    } finally { RmEndSession(session); }
  }
}
'@
$resolved = @($DatabasePaths | ForEach-Object { (Resolve-Path -LiteralPath $_).Path })
[MusicerLockOwners]::Query($resolved) | ForEach-Object {
    [pscustomobject]@{ ProcessId=$_.Process.ProcessId; AppName=$_.AppName; ServiceName=$_.ServiceName }
} | Format-Table -AutoSize

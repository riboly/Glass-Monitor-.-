using System;
using System.Threading;
using OpenHardwareMonitor.Hardware;

public class CpuTempHelper {
  static float? ReadCpuTemp(Computer computer) {
    float? best = null;
    int bestScore = -1;
    foreach (IHardware hw in computer.Hardware) {
      Visit(hw, ref best, ref bestScore);
    }
    return best;
  }

  static void Visit(IHardware hw, ref float? best, ref int bestScore) {
    hw.Update();
    foreach (ISensor s in hw.Sensors) {
      if (s.SensorType != SensorType.Temperature || s.Value == null) continue;
      float v = s.Value.Value;
      if (v <= 0f || v > 125f) continue;
      string n = (s.Name ?? "").ToLowerInvariant();
      int score = 0;
      if (n.IndexOf("package") >= 0 || n.IndexOf("tctl") >= 0 || n.IndexOf("tdie") >= 0) score = 5;
      else if (n.IndexOf("core") >= 0) score = 1;
      else score = 2;
      if (hw.HardwareType == HardwareType.CPU) score += 10;
      if (score > bestScore) { bestScore = score; best = v; }
    }
    foreach (IHardware sub in hw.SubHardware) Visit(sub, ref best, ref bestScore);
  }

  public static int Main(string[] args) {
    string mode = args.Length > 0 ? args[0] : "once";
    Computer computer = new Computer();
    computer.CPUEnabled = true;
    computer.MainboardEnabled = true;
    computer.GPUEnabled = false;
    computer.RAMEnabled = false;
    computer.FanControllerEnabled = false;
    computer.HDDEnabled = false;
    try {
      computer.Open();
      if (mode == "serve") {
        while (true) {
          float? t = ReadCpuTemp(computer);
          Console.WriteLine(t.HasValue ? t.Value.ToString("0.0") : "NA");
          Console.Out.Flush();
          Thread.Sleep(2000);
        }
      } else {
        float? t = ReadCpuTemp(computer);
        if (t.HasValue) {
          Console.WriteLine(t.Value.ToString("0.0"));
          return 0;
        }
        Console.WriteLine("NA");
        return 2;
      }
    } catch (Exception ex) {
      Console.Error.WriteLine(ex.ToString());
      return 1;
    } finally {
      try { computer.Close(); } catch {}
    }
  }
}

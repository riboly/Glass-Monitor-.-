using System;
using System.Linq;
using LibreHardwareMonitor.Hardware;

class Program {
    static float? ReadCpuTemp(Computer computer) {
        float? best = null;
        int bestScore = -1;
        void Visit(IHardware hw) {
            hw.Update();
            foreach (var s in hw.Sensors) {
                if (s.SensorType != SensorType.Temperature || s.Value == null) continue;
                float v = s.Value.Value;
                if (v <= 0 || v > 125) continue;
                string n = (s.Name ?? "").ToLowerInvariant();
                int score = 0;
                if (n.Contains("package") || n.Contains("tctl") || n.Contains("tdie") || n.Contains("average")) score = 5;
                else if (n.Contains("ccd")) score = 4;
                else if (n == "cpu" || n.Contains("cpu total")) score = 3;
                else if (n.Contains("core")) score = 1;
                else score = 2;
                // prefer sensors under CPU hardware
                if (hw.HardwareType == HardwareType.Cpu) score += 10;
                if (score > bestScore) { bestScore = score; best = v; }
            }
            foreach (var sub in hw.SubHardware) Visit(sub);
        }
        foreach (var hw in computer.Hardware) Visit(hw);
        return best;
    }

    static int Main(string[] args) {
        // mode: once | serve (default once)
        string mode = args.Length > 0 ? args[0] : "once";
        var computer = new Computer { IsCpuEnabled = true, IsGpuEnabled = false, IsMemoryEnabled = false, IsMotherboardEnabled = true, IsControllerEnabled = false, IsNetworkEnabled = false, IsStorageEnabled = false };
        try {
            computer.Open();
            if (mode == "serve") {
                // print temp every 2s on stdout
                while (true) {
                    var t = ReadCpuTemp(computer);
                    Console.WriteLine(t.HasValue ? t.Value.ToString("0.0") : "NA");
                    Console.Out.Flush();
                    System.Threading.Thread.Sleep(2000);
                }
            } else {
                var t = ReadCpuTemp(computer);
                if (t.HasValue) { Console.WriteLine(t.Value.ToString("0.0")); return 0; }
                Console.WriteLine("NA");
                return 2;
            }
        } catch (Exception ex) {
            Console.Error.WriteLine(ex.Message);
            return 1;
        } finally {
            try { computer.Close(); } catch {}
        }
    }
}

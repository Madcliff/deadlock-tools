using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using UnityEngine;

/// <summary>
/// Per-jammer jamming effects.
///
/// Stock jamming flags a ship IsJammed and every reader applies the one global DataTables.JammingPenalty
/// (MaxVelocityPenalty, EvadePenalty, AccuracyPenalty), so all jammers have the same effect. With
/// scripts/merge_custom_modules.cs (InjectJammingHooks):
///   * Jammer's set_IsJammed calls go through SetJammed, which remembers which jammer jammed each ship;
///   * methods that check IsJammed and then read a JammingPenalty field go through IsJammed (remembering the ship
///     checked) and MaxVelocity / Evade / Accuracy, which return the jamming squadron's (or ship's) own penalty when
///     StreamingAssets/deadlock-jamming.txt has one, else the stock value. The ship-info tooltip keeps the stock text;
///   * BattleSimulation's Jam / JamCancel handlers check CanCommandJam instead of ShipInfo.IsProxy, so a capital ship
///     with a Jammer (weapons --jamming) can switch jamming on and off; stock, only squadrons could.
///
/// deadlock-jamming.txt, one line per jammer, written by the toolkit:
///   squadron 103 speed=-1.2 evade=-25 accuracy=-10     (squadron type, FighterSquadron.SquadronShipType)
///   ship 206 evade=-20                                 (capital ship type, ShipInfo.ShipType)
/// A missing key keeps the stock value.
/// </summary>
public static class CustomJamming
{
    public const string FileName = "deadlock-jamming.txt";
    private const BindingFlags Instance = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
    private static readonly MethodInfo JammedGetter = typeof(DestructableAssetInfo).GetMethod("get_IsJammed", Instance);
    private static readonly MethodInfo JammedSetter = typeof(DestructableAssetInfo).GetMethod("set_IsJammed", Instance);

    private static readonly Dictionary<DestructableAssetInfo, ShipInfo> jammedBy = new Dictionary<DestructableAssetInfo, ShipInfo>();
    private static DestructableAssetInfo lastChecked;
    private static Dictionary<string, float[]> overrides;     // "squadron 103" -> {speed, evade, accuracy} (NaN = stock)

    public static void SetJammed(DestructableAssetInfo target, bool value, Jammer jammer)
    {
        JammedSetter.Invoke(target, new object[] { value });
        if (target == null) return;
        if (value && jammer != null) jammedBy[target] = jammer.GetComponentInParent<ShipInfo>();
        else if (!value) jammedBy.Remove(target);
    }

    public static bool IsJammed(DestructableAssetInfo target)
    {
        lastChecked = target;
        return target != null && (bool)JammedGetter.Invoke(target, null);
    }

    /// <summary>
    /// Stands in for ShipInfo.IsProxy in BattleSimulation's Jam / JamCancel handlers: squadrons (IsProxy) as before,
    /// plus any ship carrying a Jammer, so a capital ship's Jamming button really switches its Jammer on and off.
    /// </summary>
    public static bool CanCommandJam(ShipInfo ship)
    {
        return ship != null && (ship.IsProxy || ship.GetComponent<Jammer>() != null);
    }

    public static float MaxVelocity(ref JammingPenaltyInfo info) { return Pick(0, info.MaxVelocityPenalty); }
    public static float Evade(ref JammingPenaltyInfo info) { return Pick(1, info.EvadePenalty); }
    public static float Accuracy(ref JammingPenaltyInfo info) { return Pick(2, info.AccuracyPenalty); }

    private static float Pick(int index, float stock)
    {
        ShipInfo source;
        if (lastChecked == null || !jammedBy.TryGetValue(lastChecked, out source) || source == null) return stock;
        float[] values;
        if (!Overrides().TryGetValue(Key(source), out values)) return stock;
        return float.IsNaN(values[index]) ? stock : values[index];
    }

    private static string Key(ShipInfo source)
    {
        FighterSquadron squadron = source.GetComponent<FighterSquadron>();
        return squadron != null ? "squadron " + (int)squadron.SquadronShipType : "ship " + (int)source.ShipType;
    }

    private static Dictionary<string, float[]> Overrides()
    {
        if (overrides != null) return overrides;
        overrides = new Dictionary<string, float[]>();
        try
        {
            string path = Path.Combine(Application.streamingAssetsPath, FileName);
            if (!File.Exists(path)) return overrides;
            foreach (string raw in File.ReadAllLines(path))
            {
                string line = raw.Trim();
                if (line.Length == 0 || line.StartsWith("#")) continue;
                string[] parts = line.Split((char[])null, StringSplitOptions.RemoveEmptyEntries);
                if (parts.Length < 2) continue;
                // no array initializer: the compiler stores those as a <PrivateImplementationDetails> data blob that the
                // merge does not copy (the merged DLL then needs the missing CustomModules assembly)
                float[] values = new float[3];
                values[0] = values[1] = values[2] = float.NaN;
                for (int i = 2; i < parts.Length; i++)
                {
                    string[] kv = parts[i].Split(new char[] { '=' });
                    float number;
                    if (kv.Length != 2 || !float.TryParse(kv[1], System.Globalization.NumberStyles.Float,
                            System.Globalization.CultureInfo.InvariantCulture, out number)) continue;
                    int slot = kv[0] == "speed" ? 0 : kv[0] == "evade" ? 1 : kv[0] == "accuracy" ? 2 : -1;
                    if (slot >= 0) values[slot] = number;
                }
                overrides[parts[0] + " " + parts[1]] = values;
            }
        }
        catch (Exception e)
        {
            Debug.LogError("CustomJamming: could not read " + FileName + ": " + e.Message);
        }
        return overrides;
    }
}

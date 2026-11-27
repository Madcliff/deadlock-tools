using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Runtime.CompilerServices;
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
    private static readonly FieldInfo CurrentPower = typeof(StealthModule).GetField("currentPower", Instance);
    private static readonly MethodInfo AliveGetter = typeof(DestructableAssetInfo).GetMethod("get_IsAlive", Instance);
    private static readonly FieldInfo JamActive = typeof(Jammer).GetField("jamActive", Instance);
    private static readonly MethodInfo JamActiveSetter = typeof(Jammer).GetMethod("set_JamActive", Instance);
    private const float BatteryDrainPerTick = 10f / 120f;
    private const float ReserveCapacity = 100f;
    private const float ReserveRechargePerTick = 0.2f;
    private static readonly ConditionalWeakTable<Jammer, float[]> reserves = new ConditionalWeakTable<Jammer, float[]>();

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

    private static ShipInfo CapitalShip(Jammer jammer)
    {
        // A missile's Jammer can report its launching ship as its owner. Only charge ship-mounted components.
        var ship = jammer.GetComponent<ShipInfo>();
        return ship != null && !ship.IsProxy ? ship : null;
    }

    private static float[] NewReserve(Jammer jammer)
    {
        var reserve = new float[1];
        reserve[0] = BatterySetting(CapitalShip(jammer), 4, ReserveCapacity);
        return reserve;
    }

    private static float BatterySetting(ShipInfo ship, int index, float fallback)
    {
        float[] values;
        return Overrides().TryGetValue("ship " + (int)ship.ShipType, out values) && !float.IsNaN(values[index])
            ? values[index] : fallback;
    }

    private static float DrainPerTick(ShipInfo ship)
    {
        return BatterySetting(ship, 3, BatteryDrainPerTick * 120f) / 120f;
    }

    public static bool AllowActivation(Jammer jammer, bool active)
    {
        if (!active) return false;
        var ship = CapitalShip(jammer);
        if (ship == null) return true;
        var battery = ship.GetComponent<StealthModule>();
        float drain = DrainPerTick(ship);
        return battery != null ? (float)CurrentPower.GetValue(battery) >= drain :
            reserves.GetValue(jammer, NewReserve)[0] >= drain;
    }

    public static bool PauseStealthRecharge(StealthModule battery)
    {
        var jammer = battery.GetComponent<Jammer>();
        var ship = jammer == null ? null : CapitalShip(jammer);
        return ship != null && (bool)JamActive.GetValue(jammer) && DrainPerTick(ship) > 0f;
    }

    public static void TickBattery(Jammer jammer)
    {
        var ship = CapitalShip(jammer);
        if (ship == null || !(bool)AliveGetter.Invoke(ship, null)) return;
        var battery = ship.GetComponent<StealthModule>();
        bool active = (bool)JamActive.GetValue(jammer);
        float drain = DrainPerTick(ship);
        if (active && drain <= 0f) return;
        float power;
        if (battery == null)
        {
            var reserve = reserves.GetValue(jammer, NewReserve);
            power = active ? Mathf.Max(0f, reserve[0] - drain) :
                Mathf.Min(BatterySetting(ship, 4, ReserveCapacity),
                    reserve[0] + BatterySetting(ship, 5, ReserveRechargePerTick * 120f) / 120f);
            reserve[0] = power;
        }
        else
        {
            if (!active) return;
            power = Mathf.Max(0f, (float)CurrentPower.GetValue(battery) - drain);
            CurrentPower.SetValue(battery, power);
        }

        if (active && power <= 0f)
            JamActiveSetter.Invoke(jammer, new object[] { false });
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
                float[] values = new float[6];
                for (int i = 0; i < values.Length; i++) values[i] = float.NaN;
                for (int i = 2; i < parts.Length; i++)
                {
                    string[] kv = parts[i].Split(new char[] { '=' });
                    float number;
                    if (kv.Length != 2 || !float.TryParse(kv[1], System.Globalization.NumberStyles.Float,
                            System.Globalization.CultureInfo.InvariantCulture, out number)) continue;
                    int slot = kv[0] == "speed" ? 0 : kv[0] == "evade" ? 1 : kv[0] == "accuracy" ? 2 :
                        kv[0] == "drain" ? 3 : kv[0] == "reserve" ? 4 : kv[0] == "recharge" ? 5 : -1;
                    if (slot >= 3 && (parts[0] != "ship" || !(number >= 0f && number <= 1000f) ||
                                     (slot == 4 && number == 0f))) continue;
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

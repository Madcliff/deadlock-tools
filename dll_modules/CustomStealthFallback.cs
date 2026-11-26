using System;
using System.Reflection;

/// <summary>
/// Lets StealthModule run on ships without a Tech Bay (TechLab subsystem), such as fighter squadrons.
///
/// StealthModule reads its battery drain and recharge from TechLab.StealthDrainRate / StealthRechargeRate and
/// cancels stealth when TechLab.IsOffline; StealthBatteryWidget reads the same rates. Without a TechLab every one of
/// those calls throws. scripts/merge_custom_modules.cs (InjectStealthFallbackHooks) turns each of them into a call
/// here, which gives the stock value when there is a TechLab and otherwise the Orion's Tech Bay at full health:
/// drain 0.1 and recharge 2 (StealthModule divides both by 10 per battle tick), never offline.
/// </summary>
public static class CustomStealthFallback
{
    public const float DrainRate = 0.1f;        // TechLab.stealthDrainRate (FloatCurveRange 8 -> 0.1) at full health
    public const float RechargeRate = 2f;       // TechLab.stealthRechargeRate (FloatRange 0.5 - 2) at full health

    private const BindingFlags Instance = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
    private static readonly MethodInfo DrainGetter = typeof(TechLab).GetMethod("get_StealthDrainRate", Instance);
    private static readonly MethodInfo RechargeGetter = typeof(TechLab).GetMethod("get_StealthRechargeRate", Instance);
    private static readonly MethodInfo OfflineGetter = typeof(SubSystemBase).GetMethod("get_IsOffline", Instance);

    public static float Drain(TechLab lab)
    {
        return lab != null && DrainGetter != null ? (float)DrainGetter.Invoke(lab, null) : DrainRate;
    }

    public static float Recharge(TechLab lab)
    {
        return lab != null && RechargeGetter != null ? (float)RechargeGetter.Invoke(lab, null) : RechargeRate;
    }

    public static bool IsOffline(SubSystemBase subsystem)
    {
        return subsystem != null && OfflineGetter != null && (bool)OfflineGetter.Invoke(subsystem, null);
    }
}

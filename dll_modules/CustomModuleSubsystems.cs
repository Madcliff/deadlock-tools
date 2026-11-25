using UnityEngine;

/// <summary>
/// Lets one or more ship subsystems drive the modules this toolkit adds.
///
/// Entries are parallel arrays (plain Unity vectors, as in CustomSubsystemModifiers). Entry i contributes
/// rateMin[i] + (rateMax[i] - rateMin[i]) * source.HealthPC percent to module modules[i], where source is the
/// ship subsystem selected by sources[i] (0 Fire Control .. 6 Tech Bay). The contributions of all entries for a
/// module are added. A module with no entry keeps its stock behaviour.
///
/// Hooks (scripts/merge_custom_modules.cs and the custom module sources):
///   RegenerativeHullResinModule: Engineering.GetHullRepairRate(hullPerTick) -> HullResin(stock, module, hullPerTick)
///   MunitionFactoryModule:       Armory.GetMunitionResupplyRate()            -> MunitionFactory(stock, module)
///   Custom armour repair / drone resupply / squadron replacement / magazine capacity: rate *= Factor(this, code)
/// </summary>
public sealed class CustomModuleSubsystems : MonoBehaviour
{
    public const int ModuleHullResin = 0;
    public const int ModuleMunitionFactory = 1;
    public const int ModuleArmourRepair = 2;
    public const int ModuleDroneResupply = 3;
    public const int ModuleSquadronReplacement = 4;
    public const int ModuleMagazineCapacity = 5;

    private const int SourceCount = 7;

    [SerializeField] public int[] modules = new int[0];
    [SerializeField] public int[] sources = new int[0];
    [SerializeField] public float[] rateMin = new float[0];
    [SerializeField] public float[] rateMax = new float[0];

    private SubSystemBase[] subsystems;

    /// <summary>Hull Resin tick: hull per tick = hullPerTick x factor / 100 when the module has entries.</summary>
    public static float HullResin(float stockRate, Component module, float hullPerTick)
    {
        float percent;
        return TryPercent(module, ModuleHullResin, out percent) ? hullPerTick * percent / 100f : stockRate;
    }

    /// <summary>Munition Factory tick: work per tick = factor / 100 when the module has entries.</summary>
    public static float MunitionFactory(float stockRate, Component module)
    {
        float percent;
        return TryPercent(module, ModuleMunitionFactory, out percent) ? percent / 100f : stockRate;
    }

    /// <summary>Multiplier for a custom module's rate: factor / 100, or 1 without entries.</summary>
    public static float Factor(Component module, int code)
    {
        float percent;
        return TryPercent(module, code, out percent) ? Mathf.Max(0f, percent / 100f) : 1f;
    }

    public static bool TryPercent(Component module, int code, out float percent)
    {
        percent = 0f;
        if (module == null) return false;
        CustomModuleSubsystems drivers = module.GetComponentInParent<CustomModuleSubsystems>();
        if (drivers == null) return false;
        return drivers.Percent(code, out percent);
    }

    public bool Percent(int code, out float percent)
    {
        percent = 0f;
        if (modules == null || sources == null || rateMin == null || rateMax == null) return false;
        int count = Mathf.Min(Mathf.Min(modules.Length, sources.Length), Mathf.Min(rateMin.Length, rateMax.Length));
        bool any = false;
        for (int i = 0; i < count; i++)
        {
            if (modules[i] != code) continue;
            any = true;                            // an entry counts even if its subsystem is missing (adds 0)
            SubSystemBase source = Source(sources[i]);
            if (source == null) continue;
            float health;
            try
            {
                health = source.JFGBMFEOOCJ; // obfuscated property name for get_HealthPC
            }
            catch (System.NullReferenceException)
            {
                health = 1f; // subsystem not initialised yet: treat as full health
            }
            percent += rateMin[i] + (rateMax[i] - rateMin[i]) * health;
        }
        if (percent < 0f) percent = 0f;
        return any;
    }

    private SubSystemBase Source(int code)
    {
        if (code < 0 || code >= SourceCount) return null;
        if (subsystems == null)
        {
            subsystems = new SubSystemBase[SourceCount];
            SubSystemBase[] found = GetComponentsInChildren<SubSystemBase>(true);
            for (int i = 0; i < found.Length; i++)
            {
                int slot = Code(found[i]);
                if (slot >= 0 && subsystems[slot] == null) subsystems[slot] = found[i];
            }
        }
        return subsystems[code];
    }

    private static int Code(SubSystemBase subsystem)
    {
        if (subsystem is FireControl) return 0;
        if (subsystem is Engineering) return 1;
        if (subsystem is Armory) return 2;
        if (subsystem is CIC) return 3;
        if (subsystem is Hangar) return 4;
        if (subsystem is BlackLab.BSG.Navigation) return 5;
        if (subsystem is TechLab) return 6;
        return -1;
    }
}

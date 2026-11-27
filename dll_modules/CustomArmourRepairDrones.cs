using System.Reflection;
using UnityEngine;

/// <summary>Switches a Hydra repair-drone bank to restoring the weakest eligible armour facing.</summary>
public sealed class CustomArmourRepairDrones : MonoBehaviour
{
    [SerializeField] public bool repairArmour = true;

    private const BindingFlags Instance = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
    private static readonly MethodInfo ModuleShip = typeof(HullRepairModule).GetMethod("get_Ship", Instance);
    private static readonly FieldInfo DroneModule = typeof(HullRepairDrone).GetField("GGOELGFOFIM", Instance);
    private static readonly MethodInfo ShipArmour = typeof(DestructableAssetInfo).GetMethod("get_Armour", Instance);
    private static readonly MethodInfo HullPoints = typeof(ShipInfo).GetMethod("get_HullPoints", Instance);
    private static readonly MethodInfo MaxArmour = typeof(ArmourInfo).GetMethod("get_CalculatedMaxArmor", Instance);
    private static readonly MethodInfo RepairHull = typeof(ShipInfo).GetMethod("RepairHull", Instance,
        null, new System.Type[] { typeof(float), typeof(bool), typeof(bool) }, null);

    public static HullRepairModule NativeBank(Component component)
    {
        if (component == null) return null;
        foreach (var module in component.GetComponentsInParent<HullRepairModule>(true))
            if (!(module is CustomArmourRepairDroneModule)) return module;
        return null;
    }

    private static bool UsesArmour(HullRepairModule module)
    {
        if (module is CustomArmourRepairDroneModule) return true;
        var ship = module == null ? null : ModuleShip.Invoke(module, null) as ShipInfo;
        var mode = ship == null ? null : ship.GetComponent<CustomArmourRepairDrones>();
        return mode != null && mode.repairArmour && ship.GetComponent<CustomArmourRepairDroneModule>() == null;
    }

    private static int DamagedFacing(ShipInfo target, out ArmourInfo armour, out float[] current, out float[] maximum)
    {
        armour = target == null ? null : ShipArmour.Invoke(target, null) as ArmourInfo;
        current = armour == null ? null : armour.FAEEKODJCFE();
        maximum = armour == null ? null : MaxArmour.Invoke(armour, null) as float[];
        if (current == null || maximum == null || current.Length != ArmourInfo.ARMOUR_SEGMENTS || maximum.Length != current.Length)
            return -1;
        int facing = -1;
        float weakest = 1f;
        for (int i = 0; i < current.Length; i++)
        {
            if (maximum[i] <= 0f) continue;
            float fraction = current[i] / maximum[i];
            if (fraction >= weakest) continue;
            weakest = fraction;
            facing = i;
        }
        return facing;
    }

    public static bool HasRepairDamage(HullRepairModule module, ShipInfo target)
    {
        if (!UsesArmour(module))
            return (float)HullPoints.Invoke(target, null) < target.MaxHullPoints;
        ArmourInfo armour;
        float[] current, maximum;
        return DamagedFacing(target, out armour, out current, out maximum) >= 0;
    }

    public static float ApplyRepair(ShipInfo target, float amount, bool first, bool second, HullRepairDrone drone)
    {
        if (!UsesArmour(DroneModule.GetValue(drone) as HullRepairModule))
            return (float)RepairHull.Invoke(target, new object[] { amount, first, second });
        ArmourInfo armour;
        float[] current, maximum;
        int facing = DamagedFacing(target, out armour, out current, out maximum);
        if (facing < 0 || !(amount > 0f)) return 0f;
        float before = current[facing];
        current[facing] = Mathf.Min(maximum[facing], before + amount);
        armour.OverrideCurrentArmour(current);
        return current[facing] - before;
    }

    public static string RepairLabel(string stock, HullRepairModule module)
    {
        return module != null && UsesArmour(module)
            ? "Armour Repair Drones" : stock;
    }
}

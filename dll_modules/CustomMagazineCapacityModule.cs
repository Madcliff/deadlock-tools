using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using BlackLab.Foundation;
using UnityEngine;

/// <summary>Raises the owning ship's munition capacities and starting load.</summary>
public sealed class CustomMagazineCapacityModule : EventReceiverBehaviour
{
    [SerializeField] public float capacityMultiplier = 10.0f;
    [SerializeField] public float modifierRate = 1.0f;
    [SerializeField] public int modifierField = (int)FEDNAJLFKEF.MunitionSalvoSize;

    private static readonly Dictionary<NDBFKAOICBG, float> LoadoutMultipliers =
        new Dictionary<NDBFKAOICBG, float>();
    private static readonly MethodInfo SlotShip = typeof(SlotInfoWidget).GetMethod("get_ShipWidget",
        BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
    private static readonly MethodInfo WidgetShip = typeof(ShipInfoWidget).GetMethod("get_Ship",
        BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
    private static readonly MethodInfo CopyStats = typeof(object).GetMethod("MemberwiseClone",
        BindingFlags.Instance | BindingFlags.NonPublic);

    public static MissileDataTableObject LoadoutStats(MissileDataTableObject stats, SlotInfoWidget slot)
    {
        if (stats == null || !CustomCampaignRefit.MagazineLoadoutDisplay()) return stats;
        var widget = SlotShip.Invoke(slot, null) as ShipInfoWidget;
        if (widget == null) return stats;
        var selected = WidgetShip.Invoke(widget, null) as FleetShip;
        if (selected == null) return stats;

        float multiplier;
        if (!LoadoutMultipliers.TryGetValue(selected.ShipType, out multiplier))
        {
            multiplier = 1f;
            string path = Path.Combine(Application.streamingAssetsPath,
                "deadlock-magazine-" + ((int)selected.ShipType).ToString(CultureInfo.InvariantCulture) + ".txt");
            try
            {
                float configured;
                if (File.Exists(path) && float.TryParse(File.ReadAllText(path), NumberStyles.Float,
                    CultureInfo.InvariantCulture, out configured) && configured > 0f && !float.IsInfinity(configured))
                    multiplier = configured;
            }
            catch (IOException) { }
            catch (System.UnauthorizedAccessException) { }
            LoadoutMultipliers[selected.ShipType] = multiplier;
        }

        if (multiplier == 1f) return stats;
        var preview = (MissileDataTableObject)CopyStats.Invoke(stats, null);
        preview.MaxMissiles = Mathf.Max(0, Mathf.RoundToInt(stats.MaxMissiles * multiplier));
        return preview;
    }

    private ShipInfo ship;
    private ShipDataTable shipData;
    private BattleManager battle;

    /// <summary>
    /// Returns the capacity multiplier configured on this ship, including its
    /// selected crew modifier. Called from ShipInfo's capacity and resupply
    /// paths so vanilla missile table values remain global and unchanged.
    /// </summary>
    public static int GetScaledCapacity(ShipInfo owner, int baseCapacity)
    {
        if (owner == null) return baseCapacity;
        CustomMagazineCapacityModule module = owner.GetComponent<CustomMagazineCapacityModule>();
        if (module == null || module.capacityMultiplier <= 0f) return baseCapacity;

        return Mathf.Max(0, Mathf.RoundToInt(baseCapacity * module.GetEffectiveMultiplier()));
    }

    public static int ScaleStartingCount(int stock, ShipInfo owner)
    {
        return GetScaledCapacity(owner, stock);
    }

    private float GetEffectiveMultiplier()
    {
        float factor = 1f;
        if (modifierRate != 0f && shipData != null && battle != null && ship != null)
        {
            float crewFactor = shipData.CalculateCrewVeterancyPercentageBonus(
                ship, (FEDNAJLFKEF)modifierField, battle, 1f);
            factor = Mathf.Max(0f, 1f + (crewFactor - 1f) * modifierRate);
        }
        factor *= CustomModuleSubsystems.Factor(this, CustomModuleSubsystems.ModuleMagazineCapacity);
        return capacityMultiplier * factor;
    }

    protected override void Awake()
    {
        base.Awake();
        ship = GetComponent<ShipInfo>();
        shipData = RefMan.GetBehaviour<ShipDataTable>(false, false, false);
        battle = RefMan.GetBehaviour<BattleManager>(false, false, false);
    }
}

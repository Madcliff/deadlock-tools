using System.Reflection;
using BlackLab.Foundation;
using UnityEngine;

/// <summary>Passively restores Deploy Plating and opted-in repair drone reserves.</summary>
public sealed class CustomDroneResupplyModule : EventReceiverBehaviour
{
    [SerializeField] public float basePlatingPerTick = 1f;
    [SerializeField] public float modifierRate = 1.0f;
    [SerializeField] public int modifierField = (int)FEDNAJLFKEF.MunitionResupplyRate;

    [SerializeField] public bool resupplyHullRepairDrones;
    [SerializeField] public bool resupplyArmourRepairDrones;

    private static readonly FieldInfo RepairStock = typeof(HullRepairModule).GetField(
        "EFGOKLIAOEL", BindingFlags.Instance | BindingFlags.NonPublic);
    private static readonly FieldInfo RepairCapacity = typeof(HullRepairModule).GetField(
        "droneStack", BindingFlags.Instance | BindingFlags.NonPublic);

    // The game exposes full-restock only. This private reserve is therefore
    // updated directly; the field name is tied to the inspected game build.
    private static readonly FieldInfo ReserveField = typeof(DeployPlatingModule).GetField(
        "OOCHPELKMJE", BindingFlags.Instance | BindingFlags.NonPublic);
    private static readonly MethodInfo CapacityGetter = typeof(DeployPlatingModule).GetMethod(
        "get_PlatingCapacity", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
    private static readonly MethodInfo TotalCurrentGetter = typeof(DeployPlatingModule).GetMethod(
        "get_TotalCurrentPlating", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);

    private ShipInfo ship;
    private DeployPlatingModule plating;
    private HullRepairModule hullDrones;
    private CustomArmourRepairDroneModule armourDrones;
    private float hullCredit;
    private float armourCredit;
    private ShipDataTable shipData;
    private BattleManager battle;

    protected override void Awake()
    {
        base.Awake();
        ship = GetComponent<ShipInfo>();
        if (ship != null)
        {
            plating = ship.GetComponent<DeployPlatingModule>();
            armourDrones = ship.GetComponent<CustomArmourRepairDroneModule>();
            foreach (var bank in ship.GetComponents<HullRepairModule>())
                if (!(bank is CustomArmourRepairDroneModule))
                {
                    hullDrones = bank;
                    break;
                }
        }
        shipData = RefMan.GetBehaviour<ShipDataTable>(false, false, false);
        battle = RefMan.GetBehaviour<BattleManager>(false, false, false);
        if (IDIKLNMNBIC != null)
            IDIKLNMNBIC.Subscribe((HMFMLKKCBDN)31, this);
    }

    public override void OnGameEvent(HMFMLKKCBDN eventId, object payload)
    {
        base.OnGameEvent(eventId, payload);
        if (eventId != (HMFMLKKCBDN)31 || ship == null || basePlatingPerTick <= 0f)
            return;

        float factor = 1f;
        if (modifierRate != 0f && shipData != null && battle != null)
        {
            float crewFactor = shipData.CalculateCrewVeterancyPercentageBonus(
                ship, (FEDNAJLFKEF)modifierField, battle, 1f);
            factor = Mathf.Max(0f, 1f + (crewFactor - 1f) * modifierRate);
        }
        factor *= CustomModuleSubsystems.Factor(this, CustomModuleSubsystems.ModuleDroneResupply);

        float amount = basePlatingPerTick * factor;
        if (resupplyHullRepairDrones) ReplenishBank(hullDrones, amount, ref hullCredit);
        if (resupplyArmourRepairDrones) ReplenishBank(armourDrones, amount, ref armourCredit);

        if (plating == null || ReserveField == null || CapacityGetter == null || TotalCurrentGetter == null)
            return;

        float reserve = (float)ReserveField.GetValue(plating);
        float maxCapacity = (float)CapacityGetter.Invoke(plating, null);
        float totalCurrent = (float)TotalCurrentGetter.Invoke(plating, null);
        float missing = Mathf.Max(0f, maxCapacity - totalCurrent);
        float addition = Mathf.Min(missing, amount);
        if (addition > 0f) ReserveField.SetValue(plating, Mathf.Min(maxCapacity, reserve + addition));
    }

    private static void ReplenishBank(HullRepairModule bank, float amount, ref float credit)
    {
        if (bank == null || RepairStock == null || RepairCapacity == null || !(amount > 0f)) return;
        int stock = (int)RepairStock.GetValue(bank);
        int missing = (int)RepairCapacity.GetValue(bank) - stock;
        if (missing <= 0)
        {
            credit = 0f;
            return;
        }

        credit = Mathf.Min(missing, credit + amount);
        int wholeDrones = Mathf.FloorToInt(credit);
        if (wholeDrones == 0) return;
        RepairStock.SetValue(bank, stock + wholeDrones);
        credit -= wholeDrones;
    }
}

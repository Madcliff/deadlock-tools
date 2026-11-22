using System.Reflection;
using BlackLab.Foundation;
using UnityEngine;

/// <summary>Passively restores Deploy Plating reserve on each battle tick.</summary>
public sealed class CustomDroneResupplyModule : EventReceiverBehaviour
{
    [SerializeField] public float basePlatingPerTick = 1f;
    [SerializeField] public float modifierRate = 1.0f;
    [SerializeField] public int modifierField = (int)FEDNAJLFKEF.MunitionResupplyRate;

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
    private ShipDataTable shipData;
    private BattleManager battle;

    protected override void Awake()
    {
        base.Awake();
        ship = GetComponent<ShipInfo>();
        if (ship != null) plating = ship.GetComponent<DeployPlatingModule>();
        shipData = RefMan.GetBehaviour<ShipDataTable>(false, false, false);
        battle = RefMan.GetBehaviour<BattleManager>(false, false, false);
        if (IDIKLNMNBIC != null)
            IDIKLNMNBIC.Subscribe((HMFMLKKCBDN)31, this);
    }

    public override void OnGameEvent(HMFMLKKCBDN eventId, object payload)
    {
        base.OnGameEvent(eventId, payload);
        if (eventId != (HMFMLKKCBDN)31 || ship == null || plating == null ||
            ReserveField == null || CapacityGetter == null || TotalCurrentGetter == null || basePlatingPerTick <= 0f)
            return;

        float factor = 1f;
        if (modifierRate != 0f && shipData != null && battle != null)
        {
            float crewFactor = shipData.CalculateCrewVeterancyPercentageBonus(
                ship, (FEDNAJLFKEF)modifierField, battle, 1f);
            factor = Mathf.Max(0f, 1f + (crewFactor - 1f) * modifierRate);
        }

        float reserve = (float)ReserveField.GetValue(plating);
        float maxCapacity = (float)CapacityGetter.Invoke(plating, null);
        float totalCurrent = (float)TotalCurrentGetter.Invoke(plating, null);
        float missing = Mathf.Max(0f, maxCapacity - totalCurrent);
        float addition = Mathf.Min(missing, basePlatingPerTick * factor);
        if (addition > 0f) ReserveField.SetValue(plating, Mathf.Min(maxCapacity, reserve + addition));
    }
}

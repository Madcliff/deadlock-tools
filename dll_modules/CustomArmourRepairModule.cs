using System;
using System.Reflection;
using BlackLab.Foundation;
using UnityEngine;

/// <summary>Repairs armour once per battle tick, using an independently selected crew modifier.</summary>
public sealed class CustomArmourRepairModule : EventReceiverBehaviour
{
    [SerializeField] public float baseRate = 0.2f;
    [SerializeField] public float modifierRate = 1.0f;
    [SerializeField] public int modifierField = (int)FEDNAJLFKEF.HullRegen;

    private static readonly MethodInfo MaxArmourGetter = typeof(ArmourInfo).GetMethod(
        "get_CalculatedMaxArmor", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
    private static readonly MethodInfo AliveGetter = typeof(ShipInfo).GetMethod(
        "get_IsAlive", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);

    private ShipInfo ship;
    private ArmourInfo armour;
    private ShipDataTable shipData;
    private BattleManager battle;

    protected override void Awake()
    {
        base.Awake();
        ship = GetComponent<ShipInfo>();
        if (ship == null) return;
        armour = ship.GetComponent<ArmourInfo>();
        shipData = RefMan.GetBehaviour<ShipDataTable>(false, false, false);
        battle = RefMan.GetBehaviour<BattleManager>(false, false, false);
        if (IDIKLNMNBIC != null)
            IDIKLNMNBIC.Subscribe((HMFMLKKCBDN)31, this);
    }

    public float GetEffectiveRate()
    {
        float rate = Mathf.Max(0f, baseRate);
        if (modifierRate != 0f && shipData != null && battle != null)
        {
            float crewFactor = shipData.CalculateCrewVeterancyPercentageBonus(
                ship, (FEDNAJLFKEF)modifierField, battle, 1f);
            rate *= Mathf.Max(0f, 1f + (crewFactor - 1f) * modifierRate);
        }

        return rate;
    }

    public override void OnGameEvent(HMFMLKKCBDN eventId, object payload)
    {
        base.OnGameEvent(eventId, payload);
        if (eventId != (HMFMLKKCBDN)31 || ship == null ||
            !(bool)AliveGetter.Invoke(ship, null) || armour == null)
            return;

        float rate = GetEffectiveRate();
        float[] current = armour.FAEEKODJCFE();
        float[] maximum = MaxArmourGetter == null ? null : MaxArmourGetter.Invoke(armour, null) as float[];
        if (current == null || maximum == null || current.Length != ArmourInfo.ARMOUR_SEGMENTS ||
            maximum.Length != current.Length || rate <= 0f)
            return;

        bool changed = false;
        for (int i = 0; i < current.Length; i++)
        {
            if (current[i] >= maximum[i]) continue;
            current[i] = Mathf.Min(maximum[i], current[i] + rate);
            changed = true;
        }
        if (changed) armour.OverrideCurrentArmour(current);
    }
}

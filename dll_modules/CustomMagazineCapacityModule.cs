using BlackLab.Foundation;
using UnityEngine;

/// <summary>Raises the owning ship's munition capacities and starting load.</summary>
public sealed class CustomMagazineCapacityModule : EventReceiverBehaviour
{
    [SerializeField] public float capacityMultiplier = 10.0f;
    [SerializeField] public float modifierRate = 1.0f;
    [SerializeField] public int modifierField = (int)FEDNAJLFKEF.MunitionSalvoSize;

    private ShipInfo ship;
    private ShipDataTable shipData;
    private BattleManager battle;
    private bool applied;

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
        if (IDIKLNMNBIC != null)
            IDIKLNMNBIC.Subscribe((HMFMLKKCBDN)31, this);
    }

    public override void OnGameEvent(HMFMLKKCBDN eventId, object payload)
    {
        base.OnGameEvent(eventId, payload);
        if (applied || eventId != (HMFMLKKCBDN)31 || ship == null || capacityMultiplier <= 0f)
            return;

        float multiplier = GetEffectiveMultiplier();
        HCJINHPGLML[] slots = ship.GetMunitionsSlotStatusData();
        if (slots == null) return;

        for (int i = 0; i < slots.Length; i++)
        {
            HCJINHPGLML slot = slots[i];
            int scaled = Mathf.Max(0, Mathf.RoundToInt(slot.NICCCKDHJIK * multiplier));
            if (scaled == slot.NICCCKDHJIK) continue;
            LJPOLGLDKBA type = (LJPOLGLDKBA)slot.IMNMCMOLABA;
            ship.ChangeMunitionSlot(i, type, scaled, slot.HKIDMMBENPE, slot.MMFLJLCBOCN, 0);
        }
        applied = true;
    }
}

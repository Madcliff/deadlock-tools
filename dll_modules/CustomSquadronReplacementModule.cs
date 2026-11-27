using System.Reflection;
using BlackLab.Foundation;
using UnityEngine;

/// <summary>Rebuilds destroyed child squadrons after a configurable number of battle ticks.</summary>
public sealed class CustomSquadronReplacementModule : EventReceiverBehaviour
{
    private const BindingFlags InstanceMethods = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
    private static readonly MethodInfo AliveGetter = typeof(ShipInfo).GetMethod("get_IsAlive", InstanceMethods);
    private static readonly MethodInfo SimulationGetter = typeof(BattleManager).GetMethod("get_BattleSimulation", InstanceMethods);
    private static readonly MethodInfo LaunchPointGetter = typeof(FighterSquadron).GetMethod("get_LaunchPoint", InstanceMethods);
    private static readonly MethodInfo ActionSetter = typeof(FighterSquadron).GetMethod("set_Action", InstanceMethods);
    private static readonly MethodInfo StatusSetter = typeof(FighterSquadron).GetMethod("set_Status", InstanceMethods);
    private static readonly MethodInfo PendingActionSetter = typeof(FighterSquadron).GetMethod("set_PendingAction", InstanceMethods);
    private static readonly MethodInfo PendingTargetSetter = typeof(FighterSquadron).GetMethod("set_PendingActionTarget", InstanceMethods);
    private static readonly MethodInfo ActionTargetSetter = typeof(FighterSquadron).GetMethod("SetActionTarget", InstanceMethods);
    private static readonly FieldInfo Orders = typeof(FighterSquadron).GetField("NNEDKAEKNJC", InstanceMethods);
    private static readonly MethodInfo ReviveShip = typeof(ShipInfo).GetMethod("Revive", InstanceMethods);
    private static readonly MethodInfo ShipDisabledSetter = typeof(ShipInfo).GetMethod("set_IsDisabled", InstanceMethods);
    private static readonly MethodInfo PendingWeaponTargetSetter = typeof(ShipInfo).GetMethod("set_PendingWeaponTarget", InstanceMethods);
    private static readonly MethodInfo SetWeaponMode = typeof(ShipInfo).GetMethod("ChangeWeaponsMode", InstanceMethods);
    private const int StatusUnlaunched = 1;              // FighterSquadron status enum: 1 Unlaunched (docked), 5 Destroyed
    private const int EventFightersDocked = 26;          // HMFMLKKCBDN.FightersDocked
    [SerializeField] public float baseTicksPerReplacement = 5f;
    [SerializeField] public float modifierRate = 1.0f;
    [SerializeField] public int modifierField = (int)FEDNAJLFKEF.SquadronRepair;

    private float replacementProgress;
    private ShipInfo ship;
    private ShipDataTable shipData;
    private BattleManager battle;

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
        if (eventId != (HMFMLKKCBDN)31 || ship == null || battle == null ||
            !(bool)AliveGetter.Invoke(ship, null) || ship.Children == null)
            return;

        float rateFactor = 1f;
        if (modifierRate != 0f && shipData != null && battle != null)
        {
            float crewFactor = shipData.CalculateCrewVeterancyPercentageBonus(
                ship, (FEDNAJLFKEF)modifierField, battle, 1f);
            rateFactor = Mathf.Max(0.01f, 1f + (crewFactor - 1f) * modifierRate);
        }
        rateFactor = Mathf.Max(0.01f, rateFactor * CustomModuleSubsystems.Factor(this, CustomModuleSubsystems.ModuleSquadronReplacement));

        var simulation = SimulationGetter.Invoke(battle, null) as BlackLab.WEGO.BattleSimulation;
        if (simulation == null) return;

        foreach (ShipInfo child in ship.Children)
        {
            if (child == null || child.Mothership != ship ||
                child.FighterSquadron == null || (bool)AliveGetter.Invoke(child, null))
                continue;

            FighterSquadron squadron = child.FighterSquadron;
            float hull = OneFighterHull(child.MaxHullPoints, squadron.MaxShipCount, squadron.ShipLoss);
            if (!(hull > 0f)) continue;

            float required = Mathf.Max(1f, baseTicksPerReplacement);
            replacementProgress = Mathf.Min(required, replacementProgress + rateFactor);
            if (replacementProgress < required) return;

            LaunchPoint bay = LaunchPointGetter.Invoke(squadron, null) as LaunchPoint;
            Transform hangar = bay != null && bay.transform.IsChildOf(ship.transform)
                ? bay.transform : ship.transform;
            child.transform.SetPositionAndRotation(hangar.position, hangar.rotation);

            // ReviveSquadron is Survival-only. Revive resets the ship's native dead state and effects.
            ReviveShip.Invoke(child, null);
            child.SetInitialHullPoints(hull);
            if (!(bool)AliveGetter.Invoke(child, null)) return;
            ResetCombatState(child, squadron);
            if (StatusSetter != null)
                StatusSetter.Invoke(squadron, new object[] {
                    System.Enum.ToObject(StatusSetter.GetParameters()[0].ParameterType, StatusUnlaunched) });
            if (IDIKLNMNBIC != null)
                IDIKLNMNBIC.Publish((HMFMLKKCBDN)EventFightersDocked, child.gameObject);
            replacementProgress = 0f;

            // Finish the same teardown as a normal landing so regular hangar repair can take over.
            simulation.RemoveShip(child);
            foreach (ShipInfo fighter in squadron.GetActiveFighters())
                squadron.RegisterDockedShip(fighter);
            Recordable recorder = child.GetComponent<Recordable>();
            if (recorder != null) recorder.SetActive(false, 0);
            child.gameObject.SetActive(false);
            return;
        }

        replacementProgress = 0f;
    }

    private static void ResetCombatState(ShipInfo child, FighterSquadron squadron)
    {
        squadron.HKEFOEDOADO = false;
        object orders = Orders.GetValue(squadron);
        if (orders != null) Orders.FieldType.GetMethod("Clear").Invoke(orders, null);
        ActionSetter.Invoke(squadron, new object[] { (FighterSquadron.KDLNAOHHENN)0 });
        PendingActionSetter.Invoke(squadron, new object[] { (FighterSquadron.KDLNAOHHENN)0 });
        ActionTargetSetter.Invoke(squadron, new object[] { null });
        PendingTargetSetter.Invoke(squadron, new object[] { null });

        ResetShipCombatState(child);
        foreach (ShipInfo fighter in squadron.GetAllFighters())
            if (fighter != null) ResetShipCombatState(fighter);
        // AutoFire = 0. The native setter also clears the current target on child craft.
        SetWeaponMode.Invoke(child, new object[] {
            System.Enum.ToObject(SetWeaponMode.GetParameters()[0].ParameterType, 0), null });
    }

    private static void ResetShipCombatState(ShipInfo fighter)
    {
        fighter.IsJammed = false;
        ShipDisabledSetter.Invoke(fighter, new object[] { false });
        PendingWeaponTargetSetter.Invoke(fighter, new object[] { null });
    }

    private static float OneFighterHull(float maxHull, int maxFighters, float[] lossThresholds)
    {
        if (maxFighters == 1) return maxHull;
        if (lossThresholds == null || maxFighters < 1 || !(maxHull > 0f)) return 0f;

        // The game counts one fighter for positive hull and another for each threshold exceeded.
        float hull = maxHull;
        foreach (float threshold in lossThresholds)
            if (threshold < hull) hull = threshold;
        return hull == maxHull ? maxHull * 0.5f : hull;
    }
}

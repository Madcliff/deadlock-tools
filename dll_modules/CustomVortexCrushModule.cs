using System.Collections.Generic;
using System.Reflection;
using BlackLab.Foundation;
using UnityEngine;

/// <summary>A point-targeted gravity field active during turn movement.</summary>
public sealed class CustomVortexCrushModule : EventReceiverBehaviour, ADEMJMJBKOI, FNHOFNEHIPA
{
    [SerializeField] public float targetRange = 4000f;
    [SerializeField] public float radius = 500f;
    [SerializeField] public float pullPerTick = 2f;
    [SerializeField] public float squadronPullMultiplier = 2f;
    [SerializeField] public float collapseDamage = 40f;
    [SerializeField] public int cooldownTurns = 2;
    [SerializeField] public bool friendlyFire = true;
    [SerializeField] public float hullOverflowPercent = 25f;

    private static readonly List<CustomVortexCrushModule> Active = new List<CustomVortexCrushModule>();
    private static readonly BindingFlags Instance = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
    private static readonly FieldInfo PathField = typeof(MovementAgent).GetField("Path", Instance);
    private static readonly MethodInfo PathPoints = PathField?.FieldType.GetMethod("get_Points", Instance);
    private static readonly MethodInfo ResetPosition = typeof(ShipMovementAgent).GetMethod("ResetPosition", Instance);
    private static readonly MethodInfo MiniJumping = typeof(ShipMovementAgent).GetMethod("get_IsMiniJumping", Instance);
    private static readonly MethodInfo ArmourDamage = typeof(ArmourInfo).GetMethod("Damage", Instance);
    private static readonly FieldInfo BattleShips = typeof(BattleManager).GetField("FBAEBBODOMB", Instance);
    private ShipInfo source;
    private BattleManager battle;
    private StartEndSimulationEventProxy simulationEvents;
    private Vector3 centre;
    private int lastUsedRound = int.MinValue;
    private bool selecting;
    private bool queued;

    public bool IsQueued => queued;

    protected override void Awake()
    {
        base.Awake();
        source = GetComponent<ShipInfo>();
        simulationEvents = RefMan.GetBehaviour<StartEndSimulationEventProxy>(false, false, false);
        if (simulationEvents != null) simulationEvents.Subscribe(this);
    }

    protected override void OnDestroy()
    {
        Active.Remove(this);
        if (selecting) ClearSelection();
        if (simulationEvents != null) simulationEvents.UnSubscribe(this);
        base.OnDestroy();
    }

    public bool CanSelect(BattleManager manager)
    {
        return source != null && source.LGGHLAEAKEP && !selecting && !queued && manager != null &&
            (lastUsedRound == int.MinValue || manager.PCFKLFGBAHP - lastUsedRound >= Mathf.Max(1, cooldownTurns));
    }

    public bool BeginSelection(BattleManager manager)
    {
        if (!CanSelect(manager)) return false;
        selecting = true;
        battle = manager;
        source.ShowPCMVisualisers(true);
        float range = Mathf.Max(0f, targetRange);
        if (battle.StartSelectPCMLocation(source.transform, this, range, range, LJPOLGLDKBA.ColonialPCM))
        {
            Notify("Vortex Crush: select the centre. Pull radius: " + Mathf.Max(0f, radius) + ".");
            return true;
        }
        ClearSelection();
        return false;
    }

    public bool LocationChanged(Vector3 location) => true;

    public void LocationSelected(Vector3 location)
    {
        if (!selecting) return;
        ClearSelection();
        centre = location;
        queued = true;
        Notify("Vortex Crush queued for the next movement phase.");
    }

    public void LocationCancelled() => ClearSelection();

    private void ClearSelection()
    {
        selecting = false;
        if (source != null) source.ShowPCMVisualisers(false);
        var input = RefMan.GetBehaviour<InputManagerBattle>(false, false, false);
        if (input != null) input.SetInputContext((FEHMNJFCLOI)0);
    }

    public void CancelQueued() => queued = false;

    public void OnStartSimulation()
    {
        if (!queued) return;
        queued = false;
        if (source == null || !source.LGGHLAEAKEP || battle == null || !(radius > 0f)) return;
        lastUsedRound = battle.PCFKLFGBAHP;
        if (!Active.Contains(this)) Active.Add(this);
    }

    public void OnEndSimulation()
    {
        if (!Active.Remove(this) || source == null || battle == null) return;
        var ships = BattleShips?.GetValue(battle) as IEnumerable<ShipInfo> ?? battle.GetAllShips(true, false);
        int affected = 0;
        foreach (var target in new List<ShipInfo>(ships))
        {
            if (!CanAffect(target) || target.IsProxy) continue;
            float falloff = Falloff(target.transform.position);
            var armour = target.GetComponent<ArmourInfo>();
            if (armour == null || falloff <= 0f || !(collapseDamage > 0f)) continue;
            Vector3 hit = centre == target.transform.position ? target.transform.position + target.transform.forward : centre;
            var facing = armour.DetermineQuadrant(target.transform, hit);
            float damage = collapseDamage * falloff;
            // Preview armour absorption, then submit both damage amounts through the native hit path.
            var remainder = (float)ArmourDamage.Invoke(armour, new object[] { damage, facing, false });
            remainder = Mathf.Clamp(remainder, 0f, damage);
            float armourDamage = damage - remainder;
            float hullDamage = remainder * (Mathf.Clamp(hullOverflowPercent, 0f, 100f) / 100f);
            if (!(armourDamage > 0f || hullDamage > 0f)) continue;
            target.ApplyDamageFromRemote(facing, armourDamage, hullDamage, source, (ACMKEOOAIHD)(-1));
            affected++;
        }
        Notify("Vortex Crush collapsed. Damaged " + affected + " target(s).");
    }

    public static void ApplyMovementPull(ShipInfo target, MovementAgent movement)
    {
        if (Active.Count == 0) return;
        var capital = movement as ShipMovementAgent;
        if (capital != null && (bool)MiniJumping.Invoke(capital, null)) return;
        Vector3 position = target.transform.position;
        Vector3 next = position;
        foreach (var vortex in Active)
        {
            if (vortex == null || !vortex.CanAffect(target)) continue;
            float strength = vortex.pullPerTick * vortex.Falloff(next);
            if (capital == null) strength *= Mathf.Max(0f, vortex.squadronPullMultiplier);
            if (strength > 0f) next = Vector3.MoveTowards(next, vortex.centre, strength);
        }
        if (next == position) return;
        if (capital != null)
        {
            // The capital movement tick reads absolute path points. Translate its path
            // with the ship so the next tick preserves the accumulated displacement.
            Vector3 displacement = next - position;
            object path = PathField?.GetValue(movement);
            var points = path == null ? null : PathPoints?.Invoke(path, null) as IList<Vector3>;
            if (points != null)
                for (int i = 0; i < points.Count; i++) points[i] += displacement;
            ResetPosition.Invoke(capital, new object[] { next });
        }
        target.transform.position = next;
    }

    private float Falloff(Vector3 position) => Mathf.Clamp01(1f - Vector3.Distance(centre, position) / radius);

    private bool CanAffect(ShipInfo target)
    {
        if (source == null || target == null || !target.LGGHLAEAKEP || !target.gameObject.activeInHierarchy) return false;
        if (friendlyFire) return true;
        var owner = FleetOwner(target);
        return owner != source && target.OHJMJFMNIDE != source.OHJMJFMNIDE &&
            owner.OHJMJFMNIDE != source.OHJMJFMNIDE && target.Faction != source.Faction && owner.Faction != source.Faction;
    }

    private static ShipInfo FleetOwner(ShipInfo ship)
    {
        for (int i = 0; i < 8; i++)
        {
            var parent = ship.Mothership;
            if (parent == null || parent == ship) break;
            ship = parent;
        }
        return ship;
    }

    private static void Notify(string message)
    {
        var panel = RefMan.GetBehaviour<ShipInfoPanel>(false, false, false);
        if (panel != null) panel.DisplayNotification(message);
    }
}

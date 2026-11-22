using System.Collections.Generic;
using System.Reflection;
using BlackLab.Foundation;
using UnityEngine;

/// <summary>Enemy-only EMP blast for hostile ship systems, fighters, and missiles.</summary>
public class CustomEmpPulseModule : EventReceiverBehaviour, GFHJMNJPKPC, FNHOFNEHIPA
{
    [SerializeField] public float range = 1000f;
    [SerializeField] public int cooldownTurns = 1;
    [SerializeField] public int detonationDelayTicks = 0;
    [SerializeField] public float subsystemDamage = 8f;
    [SerializeField] public int recoveryTicks = 240;
    [SerializeField] public int disableSquadronTicks = 360;
    [SerializeField] public int fighterEvadeDebuffTicks = 360;

    protected int lastUsedRound = int.MinValue;
    protected int lastPulseMissilesDestroyed;
    private int squadronTicksLeft;
    private int detonationTicksLeft;
    private bool awaitingStartSimulation;
    private bool tickSubscribed;
    private ShipInfo pendingSource;
    private BattleManager pendingBattle;
    private TickEventProxy tickEvents;
    private StartEndSimulationEventProxy simulationEvents;
    private readonly List<FighterSquadron> stunnedSquadrons = new List<FighterSquadron>();
    private readonly Dictionary<ShipInfo, int> jammedFighters = new Dictionary<ShipInfo, int>();
    private static FieldInfo activeMissilesField;
    private static bool activeMissilesFieldChecked;
    protected virtual string AbilityLabel => "Experimental EMP";

    protected override void Awake()
    {
        base.Awake();
        if (IDIKLNMNBIC != null)
            IDIKLNMNBIC.Subscribe(HMFMLKKCBDN.StartSimulation, this);
        simulationEvents = RefMan.GetBehaviour<StartEndSimulationEventProxy>(false, false, false);
        if (simulationEvents != null) simulationEvents.Subscribe(this);
    }

    public int TurnsUntilReady(int round)
    {
        if (lastUsedRound == int.MinValue || round < lastUsedRound) return 0;
        return Mathf.Max(0, Mathf.Clamp(cooldownTurns, 1, 999) - (round - lastUsedRound));
    }

    public bool DetonationPending => awaitingStartSimulation || detonationTicksLeft > 0;
    public bool CanCancelQueuedPulse => awaitingStartSimulation;

    public bool QueuePulse(ShipInfo source, BattleManager battle, int round)
    {
        if (source == null || battle == null || DetonationPending || TurnsUntilReady(round) > 0)
            return false;
        pendingSource = source;
        pendingBattle = battle;
        awaitingStartSimulation = true;
        return true;
    }

    public bool CancelQueuedPulse()
    {
        if (!CanCancelQueuedPulse) return false;
        CancelPending();
        FinishTickSubscriptionIfIdle();
        return true;
    }

    public override void OnGameEvent(HMFMLKKCBDN gameEvent, object payload)
    {
        base.OnGameEvent(gameEvent, payload);
        if (gameEvent == HMFMLKKCBDN.StartSimulation) BeginQueuedPulse();
    }

    public virtual void OnStartSimulation() => BeginQueuedPulse();
    public virtual void OnEndSimulation() { }

    private void BeginQueuedPulse()
    {
        if (!awaitingStartSimulation) return;
        awaitingStartSimulation = false;
        detonationTicksLeft = Mathf.Clamp(detonationDelayTicks, 0, 99999);
        if (detonationTicksLeft == 0)
        {
            DetonatePending();
            return;
        }
        if (!EnsureTickSubscription())
        {
            CancelPending();
            return;
        }
    }

    public int ApplyPulse(ShipInfo source, BattleManager battle, int round)
    {
        return ApplyPulseAt(source, battle, source == null ? Vector3.zero : source.transform.position,
            range, round, true, true);
    }

    protected int ApplyPulseAt(ShipInfo source, BattleManager battle, Vector3 centre, float radius,
        int round, bool checkCooldown, bool consumeCooldown, float shipQueryRange = -1f,
        HashSet<int> missilesAlreadyProcessed = null)
    {
        if (source == null || battle == null || (checkCooldown && TurnsUntilReady(round) > 0)) return 0;

        radius = Mathf.Max(0f, radius);
        float damage = Mathf.Max(0f, subsystemDamage);
        int duration = Mathf.Max(1, recoveryTicks);
        if (radius <= 0f) return 0;
        if (consumeCooldown) lastUsedRound = round;

        // Query enemy ships around the firing ship out to the requested search
        // distance, then use the selected centre for the actual blast radius.
        float queryRange = centre == source.transform.position || shipQueryRange < 0f
            ? radius : Mathf.Max(radius, shipQueryRange);
        // Include capital ships. The fourth selector argument excludes them
        // when false, which made battleship EMP pulses appear to do nothing.
        List<DestructableAssetInfo> targets = battle.GetShipTargets(source, true, queryRange, true, true);
        int affected = 0;

        if (targets != null)
        {
            foreach (DestructableAssetInfo asset in targets)
            {
                ShipInfo target = asset as ShipInfo;
                if (target == null || target == source || !target.LGGHLAEAKEP) continue;
                if (target.OHJMJFMNIDE == source.OHJMJFMNIDE || target.Faction == source.Faction) continue;
                if (Vector3.Distance(centre, target.transform.position) > radius) continue;

                bool hit = false;
                if (damage > 0f)
                {
                    int count = target.GetSubSystemCount();
                    for (int index = 0; index < count; index++)
                    {
                        SubSystemBase subsystem = target.GetSubSystem(index);
                        if (subsystem == null || subsystem.MOMPABLNGOA == (NCICOBNCMNO)0) continue;
                        // DFIPECIGNPJ value 0 is the game's EMP temporary max-health effect.
                        subsystem.AddMaxHealthEffect((DFIPECIGNPJ)0, -damage, duration, true, false);
                        hit = true;
                    }
                }

                FighterSquadron squadron = target.FighterSquadron;
                if (squadron != null)
                {
                    if (!squadron.HKEFOEDOADO && disableSquadronTicks > 0)
                    {
                        squadron.HKEFOEDOADO = true;
                        if (!stunnedSquadrons.Contains(squadron)) stunnedSquadrons.Add(squadron);
                        squadronTicksLeft = Mathf.Max(squadronTicksLeft, disableSquadronTicks);
                        hit = true;
                    }
                    if (fighterEvadeDebuffTicks > 0 &&
                        (jammedFighters.ContainsKey(target) || !target.IsJammed))
                    {
                        // Virus mine's JammingFieldHazard marks a target jammed;
                        // the game's WeaponUtils then applies DataTables.JammingPenalty,
                        // including its configured fighter evade penalty.
                        target.IsJammed = true;
                        jammedFighters[target] = Mathf.Max(
                            jammedFighters.TryGetValue(target, out int remaining) ? remaining : 0,
                            fighterEvadeDebuffTicks);
                        hit = true;
                    }
                }
                if (hit) affected++;
            }
        }

        // Missiles are tracked separately by MissileEventProxy; they are not
        // included in BattleManager.NonShipDestructables. Read that live list,
        // then fall back to Unity's active scene objects if the game build
        // changes the proxy's private field name.
        var missiles = new List<Missile>();
        var seenMissiles = new HashSet<int>();
        var missileProxy = RefMan.GetBehaviour<MissileEventProxy>(false, false, false);
        if (missileProxy != null)
        {
            if (!activeMissilesFieldChecked)
            {
                activeMissilesField = typeof(MissileEventProxy).GetField(
                    "LGAGHKJMEFE", BindingFlags.Instance | BindingFlags.NonPublic);
                activeMissilesFieldChecked = true;
            }
            var active = activeMissilesField == null ? null : activeMissilesField.GetValue(missileProxy) as IEnumerable<Missile>;
            if (active != null)
            {
                foreach (Missile missile in active)
                    AddMissileCandidate(source, centre, radius, missile, seenMissiles, missiles);
            }
        }

        // This also handles a future game build where the proxy list is renamed
        // or a missile has spawned but has not yet registered with the proxy.
        foreach (Missile missile in UnityEngine.Object.FindObjectsOfType<Missile>())
            AddMissileCandidate(source, centre, radius, missile, seenMissiles, missiles);

        int missilesDestroyed = 0;
        foreach (Missile missile in missiles)
        {
            if (missile == null || !missile.LGGHLAEAKEP) continue;
            if (missilesAlreadyProcessed != null && !missilesAlreadyProcessed.Add(missile.GetInstanceID())) continue;
            missile.Kill(source, (BlackLab.Foundation.ACMKEOOAIHD)(-1));
            missilesDestroyed++;
            affected++;
        }
        lastPulseMissilesDestroyed = missilesDestroyed;

        if (squadronTicksLeft > 0 || jammedFighters.Count > 0) SubscribeTicks();
        // Show every detonation point, even when no enemy happens to be in
        // range, so a multi-point salvo visibly produces one burst per point.
        PlayPulseVisual(centre, radius);
        var panel = RefMan.GetBehaviour<ShipInfoPanel>(false, false, false);
        if (consumeCooldown && panel != null)
            panel.DisplayNotification(AbilityLabel + " destroyed " + missilesDestroyed + " enemy missile(s).");
        return affected;
    }

    private void AddMissileCandidate(ShipInfo source, Vector3 centre, float radius, Missile missile, HashSet<int> seen, List<Missile> candidates)
    {
        if (missile == null || !missile.LGGHLAEAKEP || !seen.Add(missile.GetInstanceID())) return;
        ShipInfo launcher = missile.JLKJLJBOJOG();
        if (launcher == null || launcher.OHJMJFMNIDE == source.OHJMJFMNIDE || launcher.Faction == source.Faction) return;
        if (Vector3.Distance(centre, missile.transform.position) <= radius)
            candidates.Add(missile);
    }

    private static void PlayPulseVisual(Vector3 position, float radius)
    {
        var effects = RefMan.GetBehaviour<FXFactory>(false, false, false);
        if (effects != null) effects.CreatePulseWave((HOEKCCMCILH)2, position, radius);
        RecorderAgent.RecordPulseWaveEffect((HOEKCCMCILH)2, position, radius);
    }

    private void SubscribeTicks()
    {
        if (EnsureTickSubscription()) return;
        RestoreStunnedSquadrons();
        RestoreJammedFighters();
    }

    protected bool EnsureTickSubscription()
    {
        if (tickSubscribed) return true;
        if (tickEvents == null) tickEvents = RefMan.GetBehaviour<TickEventProxy>(false, false, false);
        if (tickEvents == null) return false;
        tickEvents.Subscribe(this);
        tickSubscribed = true;
        return true;
    }

    private void DetonatePending()
    {
        ShipInfo source = pendingSource;
        BattleManager battle = pendingBattle;
        CancelPending();
        if (source == null || battle == null || !source.LGGHLAEAKEP) return;
        int affected = ApplyPulse(source, battle, battle.PCFKLFGBAHP);
        if (affected > 0)
        {
            var panel = RefMan.GetBehaviour<ShipInfoPanel>(false, false, false);
            if (panel != null) panel.DisplayNotification(AbilityLabel + " affected " + affected + " enemy target(s).");
        }
    }

    private void CancelPending()
    {
        awaitingStartSimulation = false;
        detonationTicksLeft = 0;
        pendingSource = null;
        pendingBattle = null;
    }

    protected virtual bool HasAdditionalPendingTicks => false;

    protected void FinishTickSubscriptionIfIdle()
    {
        if (awaitingStartSimulation || detonationTicksLeft > 0 || squadronTicksLeft > 0 || jammedFighters.Count > 0 || HasAdditionalPendingTicks || !tickSubscribed) return;
        tickEvents.UnSubscribe(this);
        tickSubscribed = false;
    }

    private void RestoreStunnedSquadrons()
    {
        foreach (FighterSquadron squadron in stunnedSquadrons)
            if (squadron != null) squadron.HKEFOEDOADO = false;
        stunnedSquadrons.Clear();
        squadronTicksLeft = 0;
    }

    private void RestoreJammedFighters()
    {
        foreach (ShipInfo fighter in new List<ShipInfo>(jammedFighters.Keys))
            if (fighter != null) fighter.IsJammed = false;
        jammedFighters.Clear();
    }

    public virtual void OnTick()
    {
        if (detonationTicksLeft > 0)
        {
            detonationTicksLeft--;
            if (detonationTicksLeft == 0) DetonatePending();
        }
        if (squadronTicksLeft > 0) squadronTicksLeft--;
        if (squadronTicksLeft == 0 && stunnedSquadrons.Count > 0)
            RestoreStunnedSquadrons();
        if (jammedFighters.Count > 0)
        {
            var expired = new List<ShipInfo>();
            var refreshed = new List<KeyValuePair<ShipInfo, int>>();
            foreach (var entry in jammedFighters)
            {
                if (entry.Key == null || entry.Value <= 1) expired.Add(entry.Key);
                else refreshed.Add(new KeyValuePair<ShipInfo, int>(entry.Key, entry.Value - 1));
            }
            foreach (var entry in refreshed) jammedFighters[entry.Key] = entry.Value;
            foreach (ShipInfo fighter in expired)
            {
                if (fighter != null) fighter.IsJammed = false;
                jammedFighters.Remove(fighter);
            }
        }
        FinishTickSubscriptionIfIdle();
    }
}

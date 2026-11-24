using System.Collections.Generic;
using BlackLab.Foundation;
using UnityEngine;

/// <summary>Configurable EMP with one independently targeted charge per use.</summary>
public sealed class CustomTargetedEmpModule : CustomEmpPulseModule, ADEMJMJBKOI
{
    [SerializeField] public int chargesPerCooldown = 4;
    [SerializeField] public float targetRange = 4000f;
    [SerializeField] public int burstIntervalTicks = 10;

    private readonly List<Vector3> queuedLocations = new List<Vector3>();
    private readonly HashSet<int> processedSalvoMissiles = new HashSet<int>();
    private ShipInfo targetedSource;
    private BattleManager targetedBattle;
    private int targetedDelayTicksLeft;
    private int burstIntervalTicksLeft;
    private int salvoBurstIndex;
    private int salvoAffected;
    private int salvoMissilesDestroyed;
    private int salvoFightersStunned;
    private int salvoFightersJammed;
    private int chargesUsedThisTurn;
    private ShipInfo selectingSource;
    private BattleManager selectingBattle;
    private bool selectingPoint;
    private bool awaitingTurnStart;
    private bool salvoStarted;

    protected override string AbilityLabel => "Targeted EMP";
    protected override bool HasAdditionalPendingTicks => targetedDelayTicksLeft > 0 ||
        burstIntervalTicksLeft > 0 || salvoStarted;

    public bool SalvoPending => queuedLocations.Count > 0 || awaitingTurnStart ||
        targetedDelayTicksLeft > 0 || salvoStarted;
    public bool CanCancelSalvo => awaitingTurnStart;
    public bool SelectionPending => selectingPoint;
    public bool DetonationInProgress => targetedDelayTicksLeft > 0 || salvoStarted;
    public int QueuedPointCount => queuedLocations.Count;
    public int ChargesRemaining => Mathf.Max(0, Mathf.Clamp(chargesPerCooldown, 1, 16) - chargesUsedThisTurn);

    public bool BeginPointSelection(ShipInfo source, BattleManager battle)
    {
        if (source == null || battle == null || selectingPoint || ChargesRemaining <= 0 || salvoStarted)
            return false;
        selectingSource = source;
        selectingBattle = battle;
        selectingPoint = true;
        source.ShowPCMVisualisers(true);
        float targetRange = Mathf.Max(0f, this.targetRange);
        if (!battle.StartSelectPCMLocation(source.transform, this, targetRange,
            targetRange, LJPOLGLDKBA.ColonialPCM))
        {
            source.ShowPCMVisualisers(false);
            ClearPointSelection();
            return false;
        }
        Notify("Targeted EMP: choose one 3D point. " + Mathf.Max(0, ChargesRemaining - 1) +
            " charge(s) will remain after this selection.");
        return true;
    }

    public void LocationSelected(Vector3 location)
    {
        ShipInfo source = selectingSource;
        BattleManager battle = selectingBattle;
        if (source != null) source.ShowPCMVisualisers(false);
        if (!selectingPoint) return;
        if (!ResetTargetingContext())
        {
            ClearPointSelection();
            Notify("Targeted EMP could not close the point selector; no charge was used.");
            return;
        }
        ClearPointSelection();
        if (source == null || battle == null || !source.LGGHLAEAKEP || ChargesRemaining <= 0)
        {
            Notify("Targeted EMP could not queue that point; no charge was used.");
            return;
        }
        queuedLocations.Add(location);
        targetedSource = source;
        targetedBattle = battle;
        chargesUsedThisTurn++;
        awaitingTurnStart = true;
        Notify("Targeted EMP point queued (" + queuedLocations.Count + "). " + ChargesRemaining +
            " charge(s) remain this turn; queued points fire when the turn starts.");
    }

    public void LocationCancelled()
    {
        if (selectingSource != null) selectingSource.ShowPCMVisualisers(false);
        ResetTargetingContext();
        ClearPointSelection();
        Notify("Targeted EMP point selection cancelled; no charge was used.");
    }

    public bool LocationChanged(Vector3 location) => true;

    private void ClearPointSelection()
    {
        selectingPoint = false;
        selectingSource = null;
        selectingBattle = null;
    }

    private static bool ResetTargetingContext()
    {
        var input = RefMan.GetBehaviour<InputManagerBattle>(false, false, false);
        if (input == null) return false;
        input.SetInputContext((FEHMNJFCLOI)0);
        return true;
    }

    private static void Notify(string message)
    {
        var panel = RefMan.GetBehaviour<ShipInfoPanel>(false, false, false);
        if (panel != null) panel.DisplayNotification(message);
    }

    public bool CancelQueuedSalvo()
    {
        if (!CanCancelSalvo) return false;
        ClearSalvo();
        chargesUsedThisTurn = 0;
        FinishTickSubscriptionIfIdle();
        return true;
    }

    public override void OnGameEvent(HMFMLKKCBDN gameEvent, object payload)
    {
        base.OnGameEvent(gameEvent, payload);
        if (gameEvent == HMFMLKKCBDN.StartSimulation) BeginQueuedSalvo();
    }

    public override void OnStartSimulation()
    {
        base.OnStartSimulation();
        BeginQueuedSalvo();
    }

    public override void OnEndSimulation()
    {
        base.OnEndSimulation();
        chargesUsedThisTurn = 0;
    }

    private void BeginQueuedSalvo()
    {
        if (!awaitingTurnStart || queuedLocations.Count == 0) return;

        awaitingTurnStart = false;
        targetedDelayTicksLeft = Mathf.Clamp(detonationDelayTicks, 0, 99999);
        if (targetedDelayTicksLeft == 0)
        {
            StartSalvo();
            return;
        }
        if (!EnsureTickSubscription()) ClearSalvo();
    }

    public override void OnTick()
    {
        base.OnTick();
        if (targetedDelayTicksLeft > 0)
        {
            targetedDelayTicksLeft--;
            if (targetedDelayTicksLeft == 0) StartSalvo();
        }
        else if (burstIntervalTicksLeft > 0)
        {
            burstIntervalTicksLeft--;
            if (burstIntervalTicksLeft == 0) DetonateNextBurst();
        }
        FinishTickSubscriptionIfIdle();
    }

    private void StartSalvo()
    {
        ShipInfo source = targetedSource;
        BattleManager battle = targetedBattle;
        if (source == null || battle == null || !source.LGGHLAEAKEP)
        {
            ClearSalvo();
            FinishTickSubscriptionIfIdle();
            return;
        }

        salvoStarted = true;
        salvoBurstIndex = 0;
        salvoAffected = 0;
        salvoMissilesDestroyed = 0;
        processedSalvoMissiles.Clear();
        DetonateNextBurst();
    }

    private void DetonateNextBurst()
    {
        ShipInfo source = targetedSource;
        BattleManager battle = targetedBattle;
        if (!salvoStarted || source == null || battle == null || !source.LGGHLAEAKEP)
        {
            ClearSalvo();
            FinishTickSubscriptionIfIdle();
            return;
        }

        Vector3 location = queuedLocations[salvoBurstIndex];
        salvoAffected += ApplyPulseAt(source, battle, location, range,
            battle.PCFKLFGBAHP, false, false, targetRange + range, processedSalvoMissiles);
        salvoMissilesDestroyed += lastPulseMissilesDestroyed;
        salvoBurstIndex++;

        if (salvoBurstIndex < queuedLocations.Count)
        {
            // Separate pulse events in simulation time so each selected point
            // produces a distinct visible wave and independent detonation.
            burstIntervalTicksLeft = Mathf.Clamp(burstIntervalTicks, 1, 99999);
            if (!EnsureTickSubscription())
            {
                while (salvoBurstIndex < queuedLocations.Count)
                {
                    location = queuedLocations[salvoBurstIndex++];
                    salvoAffected += ApplyPulseAt(source, battle, location, range,
                        battle.PCFKLFGBAHP, false, false, targetRange + range, processedSalvoMissiles);
                    salvoMissilesDestroyed += lastPulseMissilesDestroyed;
                    salvoFightersStunned += lastPulseFightersStunned;
                    salvoFightersJammed += lastPulseFightersJammed;
                }
                FinishSalvo();
            }
            return;
        }

        FinishSalvo();
    }

    private void FinishSalvo()
    {
        int burstCount = salvoBurstIndex;
        int affected = salvoAffected;
        int missilesDestroyed = salvoMissilesDestroyed;
        int fightersStunned = salvoFightersStunned;
        int fightersJammed = salvoFightersJammed;
        ClearSalvo();
        var panel = RefMan.GetBehaviour<ShipInfoPanel>(false, false, false);
        if (panel != null)
            panel.DisplayNotification("Targeted EMP fired " + burstCount + " separate burst(s), affecting " +
                affected + " enemy target(s): stunned " + fightersStunned + " fighter squadron(s), applied evade debuff to " +
                fightersJammed + " fighter(s), destroyed " + missilesDestroyed + " enemy missile(s).");
        FinishTickSubscriptionIfIdle();
    }

    private void ClearSalvo()
    {
        awaitingTurnStart = false;
        targetedDelayTicksLeft = 0;
        burstIntervalTicksLeft = 0;
        targetedSource = null;
        targetedBattle = null;
        salvoStarted = false;
        salvoBurstIndex = 0;
        salvoAffected = 0;
        salvoMissilesDestroyed = 0;
        salvoFightersStunned = 0;
        salvoFightersJammed = 0;
        processedSalvoMissiles.Clear();
        queuedLocations.Clear();
    }

}

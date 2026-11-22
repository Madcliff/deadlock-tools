using BlackLab.Foundation;
using UnityEngine;

/// <summary>Adds damage-depletable armour to each of this ship's six facings.</summary>
public sealed class CustomInstantPlatingModule : EventReceiverBehaviour, GFHJMNJPKPC, FNHOFNEHIPA
{
    [SerializeField] public float portPlating = 200f;
    [SerializeField] public float starboardPlating = 200f;
    [SerializeField] public float forePlating = 200f;
    [SerializeField] public float aftPlating = 200f;
    [SerializeField] public float dorsalPlating = 200f;
    [SerializeField] public float ventralPlating = 200f;

    [SerializeField] public int cooldownTurns = 1;
    [SerializeField] public int activationDelayTicks = 0;

    private int lastAppliedRound = int.MinValue;
    private bool waitingForStartSimulation;
    private int activationTicksLeft;
    private bool tickSubscribed;
    private TickEventProxy tickEvents;
    private StartEndSimulationEventProxy simulationEvents;

    public bool IsQueued => waitingForStartSimulation || activationTicksLeft > 0;
    public bool CanCancelQueued => waitingForStartSimulation;

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
        if (lastAppliedRound == int.MinValue || round < lastAppliedRound) return 0;
        return Mathf.Max(0, Mathf.Clamp(cooldownTurns, 1, 999) - (round - lastAppliedRound));
    }

    public bool ApplyPlating(int round)
    {
        if (TurnsUntilReady(round) > 0) return false;
        ArmourInfo armour = GetComponent<ArmourInfo>();
        float[] extra = armour == null ? null : armour.ELBIKDCDLKL;
        if (extra == null || extra.Length < 6)
            return false;

        bool changed = Add(armour, PPMHACCLPHE.Port, portPlating);
        changed |= Add(armour, PPMHACCLPHE.Starboard, starboardPlating);
        changed |= Add(armour, PPMHACCLPHE.Fore, forePlating);
        changed |= Add(armour, PPMHACCLPHE.Aft, aftPlating);
        changed |= Add(armour, PPMHACCLPHE.Top, dorsalPlating);
        changed |= Add(armour, PPMHACCLPHE.Bottom, ventralPlating);
        if (changed) lastAppliedRound = round;
        return changed;
    }

    public bool QueuePlating(int round)
    {
        if (IsQueued || TurnsUntilReady(round) > 0) return false;
        waitingForStartSimulation = true;
        return true;
    }

    public bool CancelQueuedPlating()
    {
        if (!CanCancelQueued) return false;
        waitingForStartSimulation = false;
        activationTicksLeft = 0;
        return true;
    }

    public override void OnGameEvent(HMFMLKKCBDN gameEvent, object payload)
    {
        base.OnGameEvent(gameEvent, payload);
        if (gameEvent == HMFMLKKCBDN.StartSimulation) BeginQueuedPlating();
    }

    public void OnStartSimulation() => BeginQueuedPlating();
    public void OnEndSimulation() { }

    private void BeginQueuedPlating()
    {
        if (!waitingForStartSimulation) return;
        waitingForStartSimulation = false;
        activationTicksLeft = Mathf.Clamp(activationDelayTicks, 0, 99999);
        if (activationTicksLeft == 0)
        {
            ApplyQueuedPlating();
            return;
        }
        if (!SubscribeTicks())
        {
            activationTicksLeft = 0;
            var panel = RefMan.GetBehaviour<ShipInfoPanel>(false, false, false);
            if (panel != null) panel.DisplayNotification("Instant Plating could not start its configured delay.");
        }
    }

    private bool SubscribeTicks()
    {
        if (tickSubscribed) return true;
        if (tickEvents == null) tickEvents = RefMan.GetBehaviour<TickEventProxy>(false, false, false);
        if (tickEvents == null) return false;
        tickEvents.Subscribe(this);
        tickSubscribed = true;
        return true;
    }

    public void OnTick()
    {
        if (activationTicksLeft > 0) activationTicksLeft--;
        if (activationTicksLeft > 0) return;
        ApplyQueuedPlating();
        if (tickSubscribed && tickEvents != null)
        {
            tickEvents.UnSubscribe(this);
            tickSubscribed = false;
        }
    }

    private void ApplyQueuedPlating()
    {
        var battle = RefMan.GetBehaviour<BattleManager>(false, false, false);
        ShipInfo ship = GetComponent<ShipInfo>();
        bool applied = battle != null && ApplyPlating(battle.PCFKLFGBAHP);
        var panel = RefMan.GetBehaviour<ShipInfoPanel>(false, false, false);
        CustomBattleHud.RefreshArmour(panel, ship);
        if (panel != null)
            panel.DisplayNotification(applied ? "Plating applied." : "Instant Plating found no damaged facings to reinforce.");
    }

    private static bool Add(ArmourInfo armour, PPMHACCLPHE facing, float amount)
    {
        if (amount <= 0f) return false;
        float before = armour.ELBIKDCDLKL[(int)facing];
        armour.AddExtraArmour(facing, amount, before + amount, true, false);
        return armour.ELBIKDCDLKL[(int)facing] > before;
    }
}

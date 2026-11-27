using System.Reflection;
using BlackLab.Foundation;

/// <summary>An independently stocked armour-drone bank using native drone flight and launch timing.</summary>
public sealed class CustomArmourRepairDroneModule : HullRepairModule, FNHOFNEHIPA
{
    private static readonly FieldInfo CurrentStock = typeof(HullRepairModule).GetField(
        "EFGOKLIAOEL", BindingFlags.Instance | BindingFlags.NonPublic);
    private ShipInfo queuedTarget;

    public int Stock => (int)CurrentStock.GetValue(this);

    public bool IsQueued => queuedTarget != null;

    protected override void Awake()
    {
        base.Awake();
        var simulation = RefMan.GetBehaviour<StartEndSimulationEventProxy>(false, false, false);
        if (simulation != null) simulation.Subscribe(this);
    }

    public void SelectTarget(ShipInfo target) => queuedTarget = target;
    public void CancelLaunch() => queuedTarget = null;

    public void OnStartSimulation()
    {
        ShipInfo target = queuedTarget;
        queuedTarget = null;
        if (target != null && CanTargetAlly(target))
            StartLaunchingDrones(target);
    }

    public void OnEndSimulation() { }
}

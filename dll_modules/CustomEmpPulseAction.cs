using BlackLab.Foundation;

/// <summary>Battle-menu action that activates the ship's enemy-only EMP pulse.</summary>
public abstract class CustomEmpPulseAction : ActionBase
{
    public override string HIFLBAKPOGJ => "Experimental EMP";
    public override string BNLIHIIPLMO => HIFLBAKPOGJ;
    public override DJKHHACPGBM AIJFDENNDDM => DJKHHACPGBM.Repairs;
    public override IHILBAMLPLF LAELNIOCMDC => (IHILBAMLPLF)46;
    public override FBJNDNDGFCA MMENEAIAMCB => FBJNDNDGFCA.None;
    public override bool GFEAJGDEHNH => true;
    public override int CJPBJLHPKLE => 0;
    public override bool PIALNNPAOPK => false;

    private CustomEmpPulseModule GetModule()
    {
        ShipInfo ship = FGEBNOMICKK;
        return ship == null ? null : ship.GetComponent<CustomEmpPulseModule>();
    }

    public override OMPANDNANLB PNMMLHOKDFM
    {
        get
        {
            var availability = base.PNMMLHOKDFM;
            var battle = RefMan.GetBehaviour<BattleManager>(false, false, false);
            var module = GetModule();
            if (battle == null || module == null) return availability;
            if (module.DetonationPending)
                return new OMPANDNANLB(module.CanCancelQueuedPulse, DJKHHACPGBM.Repairs,
                    module.CanCancelQueuedPulse ? "Experimental EMP queued; activate again before the turn starts to cancel."
                                                : "Experimental EMP delay is underway.");
            int remaining = module.TurnsUntilReady(battle.PCFKLFGBAHP);
            return remaining > 0
                ? new OMPANDNANLB(false, DJKHHACPGBM.Repairs,
                    remaining == 1 ? "Experimental EMP is ready next turn." : "Experimental EMP is ready in " + remaining + " turns.")
                : availability;
        }
    }

    public override bool DoAction()
    {
        if (!base.DoAction())
        {
            Notify("Experimental EMP was rejected by the action availability check.");
            return false;
        }
        var battle = RefMan.GetBehaviour<BattleManager>(false, false, false);
        var module = GetModule();
        if (module == null || battle == null)
        {
            Notify(module == null ? "Experimental EMP module is missing from this ship." : "Battle manager is unavailable.");
            return false;
        }
        if (module.DetonationPending)
        {
            if (!module.CancelQueuedPulse()) return false;
            Notify("Experimental EMP queue cancelled.");
            return true;
        }
        if (!module.QueuePulse(FGEBNOMICKK, battle, battle.PCFKLFGBAHP))
        {
            Notify("Experimental EMP could not queue: it is pending or on cooldown.");
            return false;
        }
        var panel = RefMan.GetBehaviour<ShipInfoPanel>(false, false, false);
        if (panel != null)
        {
            int delay = UnityEngine.Mathf.Max(0, module.detonationDelayTicks);
            string note = delay == 0 ? " at the start of the turn." : " " + delay + " ticks after the turn starts.";
            panel.DisplayNotification("Experimental EMP queued" + note);
        }
        return true;
    }

    private static void Notify(string message)
    {
        var panel = RefMan.GetBehaviour<ShipInfoPanel>(false, false, false);
        if (panel != null) panel.DisplayNotification(message);
    }
}

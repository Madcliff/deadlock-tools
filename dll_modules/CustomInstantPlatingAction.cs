using UnityEngine;
using BlackLab.Foundation;

/// <summary>Activates the ship's instant plating module from the Actions menu.</summary>
public abstract class CustomInstantPlatingAction : ActionBase
{
    public override string HIFLBAKPOGJ => "Instant Plating";
    public override string BNLIHIIPLMO => HIFLBAKPOGJ;
    public override DJKHHACPGBM AIJFDENNDDM => DJKHHACPGBM.Repairs;
    public override IHILBAMLPLF LAELNIOCMDC => (IHILBAMLPLF)45;
    public override FBJNDNDGFCA MMENEAIAMCB => FBJNDNDGFCA.None;
    public override bool GFEAJGDEHNH => true;
    public override int CJPBJLHPKLE => 0;
    public override bool PIALNNPAOPK => false;

    private CustomInstantPlatingModule GetPlating()
    {
        ShipInfo ship = FGEBNOMICKK;
        return ship == null ? null : ship.GetComponent<CustomInstantPlatingModule>();
    }

    public override OMPANDNANLB PNMMLHOKDFM
    {
        get
        {
            var availability = base.PNMMLHOKDFM;
            var battle = RefMan.GetBehaviour<BattleManager>(false, false, false);
            var plating = GetPlating();
            if (battle == null || plating == null) return availability;
            if (plating.IsQueued)
                return new OMPANDNANLB(plating.CanCancelQueued, DJKHHACPGBM.Repairs,
                    plating.CanCancelQueued ? "Instant Plating queued; activate again before the turn starts to cancel."
                                            : "Instant Plating delay is underway.");
            int remaining = plating.TurnsUntilReady(battle.PCFKLFGBAHP);
            return remaining > 0
                ? new OMPANDNANLB(false, DJKHHACPGBM.Repairs, ReadyMessage(remaining))
                : availability;
        }
    }

    private static string ReadyMessage(int turns)
    {
        return turns == 1 ? "Instant Plating is ready next turn."
                          : "Instant Plating is ready in " + turns + " turns.";
    }

    public override bool DoAction()
    {
        if (!base.DoAction())
        {
            Notify("Instant Plating was rejected by the action availability check.");
            return false;
        }
        var battle = RefMan.GetBehaviour<BattleManager>(false, false, false);
        var plating = GetPlating();
        if (plating == null || battle == null)
        {
            Notify(plating == null ? "Instant Plating module is missing from this ship." : "Battle manager is unavailable.");
            return false;
        }
        if (plating.IsQueued)
        {
            if (!plating.CancelQueuedPlating()) return false;
            Notify("Instant Plating queue cancelled.");
            return true;
        }
        if (!plating.QueuePlating(battle.PCFKLFGBAHP))
        {
            Notify("Instant Plating could not queue: it is pending or on cooldown.");
            return false;
        }
        var panel = RefMan.GetBehaviour<ShipInfoPanel>(false, false, false);
        if (panel != null)
        {
            int delay = UnityEngine.Mathf.Max(0, plating.activationDelayTicks);
            string note = delay == 0 ? " at the start of the turn." : " " + delay + " ticks after the turn starts.";
            panel.DisplayNotification("Instant Plating queued" + note);
        }
        return true;
    }

    private static void Notify(string message)
    {
        var panel = RefMan.GetBehaviour<ShipInfoPanel>(false, false, false);
        if (panel != null) panel.DisplayNotification(message);
    }
}

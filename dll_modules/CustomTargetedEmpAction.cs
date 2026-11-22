using BlackLab.Foundation;
using UnityEngine;

/// <summary>Uses one per-turn Targeted EMP charge to select a single 3D point.</summary>
public abstract class CustomTargetedEmpAction : ActionBase
{
    public override string HIFLBAKPOGJ => "Targeted EMP";
    public override string BNLIHIIPLMO => HIFLBAKPOGJ;
    public override DJKHHACPGBM AIJFDENNDDM => DJKHHACPGBM.Repairs;
    public override IHILBAMLPLF LAELNIOCMDC => (IHILBAMLPLF)47;
    public override FBJNDNDGFCA MMENEAIAMCB => FBJNDNDGFCA.None;
    public override bool GFEAJGDEHNH => true;
    public override int CJPBJLHPKLE => 0;
    public override bool PIALNNPAOPK => false;

    private ShipInfo GetSource() => FGEBNOMICKK;

    private CustomTargetedEmpModule GetModule()
    {
        ShipInfo ship = GetSource();
        return ship == null ? null : ship.GetComponent<CustomTargetedEmpModule>();
    }

    public override OMPANDNANLB PNMMLHOKDFM
    {
        get
        {
            var availability = base.PNMMLHOKDFM;
            var battle = RefMan.GetBehaviour<BattleManager>(false, false, false);
            var module = GetModule();
            if (battle == null || module == null) return availability;
            if (module.SelectionPending || module.DetonationInProgress)
                return new OMPANDNANLB(false, DJKHHACPGBM.Repairs,
                    module.SelectionPending ? "Targeted EMP point selection is in progress."
                                            : "Targeted EMP salvo is detonating.");
            if (module.ChargesRemaining <= 0)
                return new OMPANDNANLB(false, DJKHHACPGBM.Repairs,
                    "Targeted EMP has no charges left this turn. Charges reset next turn.");
            return availability;
        }
    }

    public override bool DoAction()
    {
        if (!base.DoAction()) return false;
        ShipInfo source = GetSource();
        var battle = RefMan.GetBehaviour<BattleManager>(false, false, false);
        var module = GetModule();
        if (source == null || battle == null || module == null)
        {
            Notify("Targeted EMP module or battle manager is unavailable.");
            return false;
        }
        if (!module.BeginPointSelection(source, battle))
        {
            Notify(module.ChargesRemaining <= 0
                ? "Targeted EMP has no charges left this turn. Charges reset next turn."
                : "Targeted EMP could not open its 3D point selector.");
            return false;
        }
        return true;
    }

    private static void Notify(string message)
    {
        var panel = RefMan.GetBehaviour<ShipInfoPanel>(false, false, false);
        if (panel != null) panel.DisplayNotification(message);
    }
}

using BlackLab.Foundation;

public abstract class CustomVortexCrushAction : ActionBase
{
    public override string HIFLBAKPOGJ => GetModule()?.IsQueued == true ? "Cancel Vortex Crush" : "Vortex Crush";
    public override string BNLIHIIPLMO => "Vortex Crush";
    public override DJKHHACPGBM AIJFDENNDDM => DJKHHACPGBM.Repairs;
    public override IHILBAMLPLF LAELNIOCMDC => (IHILBAMLPLF)49;
    public override FBJNDNDGFCA MMENEAIAMCB => FBJNDNDGFCA.None;
    public override bool GFEAJGDEHNH => true;
    public override int CJPBJLHPKLE => 0;
    public override bool PIALNNPAOPK => false;

    private CustomVortexCrushModule GetModule()
    {
        ShipInfo ship = FGEBNOMICKK;
        return ship == null ? null : ship.GetComponent<CustomVortexCrushModule>();
    }

    public override OMPANDNANLB PNMMLHOKDFM
    {
        get
        {
            var module = GetModule();
            var battle = RefMan.GetBehaviour<BattleManager>(false, false, false);
            if (module == null || (!module.IsQueued && !module.CanSelect(battle)))
                return new OMPANDNANLB(false, DJKHHACPGBM.Repairs, "Vortex Crush is unavailable or recharging.");
            return base.PNMMLHOKDFM;
        }
    }

    public override bool DoAction()
    {
        if (!base.DoAction()) return false;
        var module = GetModule();
        if (module == null) return false;
        if (!module.IsQueued) return module.BeginSelection(RefMan.GetBehaviour<BattleManager>(false, false, false));
        module.CancelQueued();
        return true;
    }
}

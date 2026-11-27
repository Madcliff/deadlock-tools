using BlackLab.Foundation;

/// <summary>Opens the native friendly-ship picker for the armour bank.</summary>
public abstract class CustomArmourRepairDroneAction : ActionBase, IOELPDOJADA
{
    public override string HIFLBAKPOGJ => GetModule()?.IsQueued == true ? "Cancel Armour Repair Drones" : "Armour Repair Drones";
    public override string BNLIHIIPLMO => "Armour Repair Drones";
    public override DJKHHACPGBM AIJFDENNDDM => DJKHHACPGBM.Repairs;
    public override IHILBAMLPLF LAELNIOCMDC => (IHILBAMLPLF)48;
    public override FBJNDNDGFCA MMENEAIAMCB => FBJNDNDGFCA.None;
    public override bool GFEAJGDEHNH => true;
    public override int CJPBJLHPKLE => 0;
    public override bool PIALNNPAOPK => false;

    private CustomArmourRepairDroneModule GetModule()
    {
        ShipInfo owner = FGEBNOMICKK;
        return owner == null ? null : owner.GetComponent<CustomArmourRepairDroneModule>();
    }

    public override OMPANDNANLB PNMMLHOKDFM
    {
        get
        {
            var module = GetModule();
            if (module == null || (!module.IsQueued && module.Stock <= 0))
                return new OMPANDNANLB(false, DJKHHACPGBM.Repairs, "No armour repair drones available.");
            return base.PNMMLHOKDFM;
        }
    }

    public override bool DoAction()
    {
        if (!base.DoAction()) return false;
        var module = GetModule();
        if (module == null) return false;
        if (module.IsQueued)
        {
            module.CancelLaunch();
            return true;
        }
        var battle = RefMan.GetBehaviour<BattleManager>(false, false, false);
        return battle != null && battle.StartSelectRepairHullTarget(this, FGEBNOMICKK.Faction, module);
    }

    public void TargetSelectionCancelled() { }

    public void TargetSelected(DestructableAssetInfo target, int index)
    {
        var ship = target as ShipInfo;
        if (ship != null) GetModule()?.SelectTarget(ship);
    }
}

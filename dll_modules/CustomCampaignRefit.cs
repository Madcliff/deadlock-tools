using System;
using System.IO;
using System.Reflection;
using BlackLab.Foundation;
using UnityEngine;
using UnityEngine.EventSystems;

/// <summary>Campaign-only refits from the ship name control.</summary>
public sealed class CustomCampaignRefit : MonoBehaviour, IPointerClickHandler
{
    private static readonly string OptionsPath = Path.Combine(Application.streamingAssetsPath, "deadlock-mechanics.txt");
    private static readonly bool RefitEnabled = ReadOption("campaign_refit on");
    private static readonly bool SelfTargetEnabled = !ReadOption("drone_self_targeting off");
    private static readonly bool MagazinePreviewEnabled = !ReadOption("magazine_loadout_display off");
    private const BindingFlags InstanceMembers = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
    private static readonly MethodInfo AllowEditing = typeof(ShipInfoWidget).GetMethod("get_AllowEditing", InstanceMembers);
    private static readonly MethodInfo GetFleet = typeof(ShipInfoWidget).GetMethod("get_Fleet", InstanceMembers);
    private static readonly MethodInfo GetShip = typeof(ShipInfoWidget).GetMethod("get_Ship", InstanceMembers);
    private static readonly MethodInfo SetShip = typeof(ShipInfoWidget).GetMethod("set_Ship", InstanceMembers);
    private static readonly MethodInfo GetCampaign = typeof(GameData).GetMethod("get_ActiveCampaign", InstanceMembers);
    private static readonly MethodInfo CopyShip = typeof(object).GetMethod("MemberwiseClone", InstanceMembers);
    private ShipInfoWidget widget;
    private FleetShip pendingShip;
    private CampaignState pendingCampaign;

    private static bool ReadOption(string line)
    {
        try { return File.Exists(OptionsPath) && Array.IndexOf(File.ReadAllLines(OptionsPath), line) >= 0; }
        catch (IOException) { return false; }
        catch (UnauthorizedAccessException) { return false; }
    }

    public static bool DroneSelfTargeting()
    {
        return SelfTargetEnabled;
    }

    public static bool MagazineLoadoutDisplay()
    {
        return MagazinePreviewEnabled;
    }

    public static void Attach(ShipInfoWidget owner, GameObject nameButton)
    {
        if (!RefitEnabled || nameButton == null) return;
        var control = nameButton.GetComponent<CustomCampaignRefit>();
        if (control == null) control = nameButton.AddComponent<CustomCampaignRefit>();
        control.widget = owner;
    }

    private ShipDataTableObject GetTarget(FleetShip ship, CampaignState campaign, ShipDataTable ships)
    {
        if (ship == null || campaign == null || ships == null || !(bool)AllowEditing.Invoke(widget, null))
            return null;
        var fleet = (FleetGroup)GetFleet.Invoke(widget, null);
        var fleets = campaign.FGKEIHENBAP(HNJPANPFAEO.Colonial);
        if (fleet == null || fleets == null || !fleets.Contains(fleet) ||
            fleet.FleetShips == null || !fleet.FleetShips.Contains(ship) || ships.Find(ship.ShipType) == null)
            return null;

        NDBFKAOICBG target;
        if (ship.ShipType == NDBFKAOICBG.ColonialBattlestarJupiter)
            target = NDBFKAOICBG.ColonialBattlestarJupiterMk2;
        else if (ship.ShipType == NDBFKAOICBG.ColonialBattlestarMercury)
            target = (NDBFKAOICBG)206;
        else return null;

        if (!Enum.IsDefined(typeof(NDBFKAOICBG), target) || campaign.UnlockedShips == null ||
            Array.IndexOf(campaign.UnlockedShips, target) < 0) return null;
        var data = ships.Find(target);
        var drm = RefMan.GetBehaviour<DRMManager>(false, false, false);
        if (data == null || drm == null || !drm.IsDLCAuthorised(data.DLCRequired)) return null;
        return data;
    }

    public void OnPointerClick(PointerEventData eventData)
    {
        if (eventData.button != PointerEventData.InputButton.Right || widget == null) return;
        var game = RefMan.GetBehaviour<GameData>(false, false, false);
        var campaign = game == null ? null : (CampaignState)GetCampaign.Invoke(game, null);
        var ships = RefMan.GetBehaviour<ShipDataTable>(false, false, false);
        var ship = (FleetShip)GetShip.Invoke(widget, null);
        var target = GetTarget(ship, campaign, ships);
        if (target == null) return;

        var dialog = RefMan.GetBehaviour<DialogPanel>(false, false, false);
        if (dialog == null) return;
        int cost = Mathf.Max(0, target.BuildCost - ships.Find(ship.ShipType).BuildCost);
        string variant = ship.ShipType == NDBFKAOICBG.ColonialBattlestarJupiter ? "Jupiter Mk II" : "Mercury Mk II";
        if (campaign.Tylium < cost)
        {
            dialog.ShowConfirmationDialog("Refit to " + variant, "Requires " + cost + " Tylium.");
            return;
        }
        if (!FitsFleet(ship, target, ships))
        {
            dialog.ShowConfirmationDialog("Refit to " + variant, "This upgrade would exceed the fleet's point limit.");
            return;
        }
        pendingShip = ship;
        pendingCampaign = campaign;
        dialog.ShowConfirmationDialog("Refit to " + variant,
            "Upgrade " + ship.Name + " in place for " + cost + " Tylium?", ConfirmRefit, true, "Refit", "Cancel");
    }

    private bool FitsFleet(FleetShip ship, ShipDataTableObject target, ShipDataTable ships)
    {
        var fleet = (FleetGroup)GetFleet.Invoke(widget, null);
        var warRoom = RefMan.GetBehaviour<WarRoomDataTables>(false, false, false);
        var tables = RefMan.GetBehaviour<DataTables>(false, false, false);
        if (fleet == null || warRoom == null || tables == null) return false;
        int pointIncrease = target.FleetPointValue - ships.Find(ship.ShipType).FleetPointValue;
        return pointIncrease <= 0 || fleet.IKIMFLEMJBL(ships) + pointIncrease <= fleet.FFMAPOBJLPC(warRoom, tables);
    }

    private static float[] RefitSquadronHealth(FleetShip oldShip, FleetShip upgraded,
        ShipDataTable ships, SquadronDataTable squadrons)
    {
        var oldHealth = oldShip.SquadronHullHealths;
        var oldSlots = oldShip.ActiveSquadrons;
        var newSlots = upgraded.ActiveSquadrons;
        // The spawner appends one squadron proxy per configured carrier slot in slot order.
        if (oldHealth == null || oldSlots == null || oldHealth.Length != oldSlots.Length) return null;
        var health = new float[newSlots.Length];
        for (int i = 0; i < newSlots.Length; i++)
        {
            if (i < oldSlots.Length && oldSlots[i] == newSlots[i])
            {
                health[i] = oldHealth[i];
                continue;
            }
            if (newSlots[i] == MEAAHENKKPG.None) continue;
            var squadron = squadrons.Find(newSlots[i]);
            var data = squadron == null ? null : ships.Find(squadron.ShipType);
            if (data == null) return null;
            health[i] = data.MaxHullPoints;
        }
        return health;
    }

    private void ConfirmRefit(EKJNDOPKAPA result)
    {
        var ship = pendingShip;
        var campaign = pendingCampaign;
        pendingShip = null;
        pendingCampaign = null;
        if (result != EKJNDOPKAPA.Confirmed || widget == null || (FleetShip)GetShip.Invoke(widget, null) != ship) return;
        var game = RefMan.GetBehaviour<GameData>(false, false, false);
        if (game == null || (CampaignState)GetCampaign.Invoke(game, null) != campaign) return;
        var ships = RefMan.GetBehaviour<ShipDataTable>(false, false, false);
        var target = GetTarget(ship, campaign, ships);
        if (target == null) return;
        int cost = Mathf.Max(0, target.BuildCost - ships.Find(ship.ShipType).BuildCost);
        var events = RefMan.GetBehaviour<GameEvents>(false, false, false);
        var missiles = RefMan.GetBehaviour<MissileDataTable>(false, false, false);
        var squadrons = RefMan.GetBehaviour<SquadronDataTable>(false, false, false);
        var drm = RefMan.GetBehaviour<DRMManager>(false, false, false);
        if (campaign.Tylium < cost || events == null || missiles == null || squadrons == null ||
            drm == null || !FitsFleet(ship, target, ships)) return;

        // Native validation can resize or rewrite assignments. Prepare them before committing to the live ship.
        var upgraded = (FleetShip)CopyShip.Invoke(ship, null);
        upgraded.ShipType = ship.ShipType == NDBFKAOICBG.ColonialBattlestarJupiter
            ? NDBFKAOICBG.ColonialBattlestarJupiterMk2 : (NDBFKAOICBG)206;
        upgraded.ActiveMunitions = ship.ActiveMunitions == null ? null : (LJPOLGLDKBA[])ship.ActiveMunitions.Clone();
        upgraded.ActiveSquadrons = ship.ActiveSquadrons == null ? null : (MEAAHENKKPG[])ship.ActiveSquadrons.Clone();
        upgraded.SquadronGroupNumbers = ship.SquadronGroupNumbers == null ? null : (int[])ship.SquadronGroupNumbers.Clone();
        upgraded.StockData = null;
        upgraded.PEDBAHFNIAC(ships, missiles, squadrons, drm, false, false);

        var remaining = ship.MunitionStockCounts;
        if (remaining != null)
        {
            remaining = new int[upgraded.ActiveMunitions.Length];
            for (int i = 0; i < remaining.Length && ship.ActiveMunitions != null &&
                i < ship.ActiveMunitions.Length && i < ship.MunitionStockCounts.Length; i++)
            {
                if (ship.ActiveMunitions[i] == upgraded.ActiveMunitions[i])
                    remaining[i] = ship.MunitionStockCounts[i];
            }
        }

        var squadronHealth = RefitSquadronHealth(ship, upgraded, ships, squadrons);
        ship.ShipType = upgraded.ShipType;
        ship.ActiveMunitions = upgraded.ActiveMunitions;
        ship.ActiveSquadrons = upgraded.ActiveSquadrons;
        ship.SquadronGroupNumbers = upgraded.SquadronGroupNumbers;
        ship.SquadronHullHealths = squadronHealth;
        ship.MunitionStockCounts = remaining;
        ship.StockData = ship.StockData ?? upgraded.StockData;
        if (cost > 0) campaign.OANDBLPPDLB(-cost, events);
        SetShip.Invoke(widget, new object[] { ship });
    }
}

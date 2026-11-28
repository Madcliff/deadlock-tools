using System.Collections.Generic;
using System.Reflection;
using BlackLab.Foundation;
using UnityEngine;

/// <summary>Runtime bridge for user-defined commander stat bonuses.</summary>
public static class CustomCommanderStatModifiers
{
    private const int IdBase = 1000;
    private const int SlotsPerSetting = 10;

    private const int TyliumSkillBase = 4000;
    private static readonly MethodInfo GetColonialFleets = typeof(CampaignState).GetMethod("FGKEIHENBAP");
    private static readonly MethodInfo CreditTylium = typeof(CampaignState).GetMethod("LEFGKHDBICN");

    public static void GrantTurnTylium(CampaignState campaign, DataTables tables, GameEvents events)
    {
        if (campaign == null || campaign.BalanceSheet == null || tables == null ||
            tables.CommanderSpecialisationBonusAmount == null) return;
        var fleets = GetColonialFleets.Invoke(campaign, new object[] { (HNJPANPFAEO)0 }) as List<FleetGroup>;
        if (fleets == null) return;
        var paid = new HashSet<CommanderData>();
        foreach (var fleet in fleets)
        {
            if (fleet == null || fleet.FleetShips == null || fleet.FleetShips.Count == 0) continue;
            var officer = fleet.Commander;
            if (officer == null || officer.Specialisations == null || !paid.Add(officer)) continue;
            int income = 0;
            foreach (var bonus in tables.CommanderSpecialisationBonusAmount)
            {
                int id = (int)bonus.Specialisation;
                if (id < TyliumSkillBase || id >= TyliumSkillBase + SlotsPerSetting ||
                    !(bonus.BonusAmount > 0f) || !officer.Specialisations.Contains(bonus.Specialisation)) continue;
                income += Mathf.RoundToInt(Mathf.Min(1000000f, bonus.BonusAmount));
            }
            income = (int)System.Math.Min(income, int.MaxValue - (long)campaign.Tylium);
            if (income > 0)
                CreditTylium.Invoke(campaign, new object[] { "Officer income: " + officer.Name, income, 0, events, 0, (FDINMFBDPPG)0 });
        }
    }

    /// <summary>
    /// Adds the custom modifier rows targeting one of the game's existing commander stat
    /// calculation paths. New ids are stored in CommanderData.Specialisations and the
    /// matching amounts are read from DataTables by the native FleetCommander method.
    /// </summary>
    public static float Apply(FleetCommander commander, EAMANNMIMLO stat, float value)
    {
        if (commander == null) return value;
        int target = (int)stat;
        // 0–2 are fleet economy/leadership/XP bonuses rather than ship stat paths.
        if (target < 3 || target > 20) return value;

        ShipInfo owner = commander.GetComponent<ShipInfo>();
        bool isFlagship = owner != null && owner.IsFlagShip;
        for (int mode = 0; mode <= 1; mode++)
        {
            for (int flagshipOnly = 0; flagshipOnly <= 1; flagshipOnly++)
            {
                if (flagshipOnly == 1 && !isFlagship) continue;
                for (int slot = 0; slot < SlotsPerSetting; slot++)
                {
                    int id = IdBase + target * 100 + mode * 20 + flagshipOnly * 10 + slot;
                    EAMANNMIMLO customId = (EAMANNMIMLO)id;
                    if (!commander.HasCommandingOfficerSpeciality(customId)) continue;
                    float amount = commander.CalculateFleetFixedBonus(customId, 0f);
                    value += mode == 1 ? value * amount : amount;
                }
            }
        }
        return value;
    }
}

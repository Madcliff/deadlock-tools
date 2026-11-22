/// <summary>Runtime bridge for user-defined commander stat bonuses.</summary>
public static class CustomCommanderStatModifiers
{
    private const int IdBase = 1000;
    private const int SlotsPerSetting = 10;

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

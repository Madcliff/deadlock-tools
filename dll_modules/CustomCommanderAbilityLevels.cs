/// <summary>Per-ability unlock-rank overrides for the commander specialisation tree.</summary>
public static class CustomCommanderAbilityLevels
{
    /// <summary>Returns a configured 1-based commander rank, or zero when the game default should apply.</summary>
    public static int GetAssignedRank(int abilityId)
    {
        switch (abilityId)
        {
            // GENERATED_RANK_CASES
            default: return 0;
        }
    }

    /// <summary>
    /// The vanilla DLL stores only three rank gates and indexes them by the choice's position.
    /// Use a per-ability override when assigned; otherwise preserve those original gates and
    /// reuse the final vanilla gate for choices beyond the original three.
    /// </summary>
    public static int GetUnlockRank(int abilityId, int choiceIndex, int[] vanillaRanks)
    {
        int assigned = GetAssignedRank(abilityId);
        if (assigned > 0) return assigned + 1; // game compares CommanderData.Rank + 1
        if (vanillaRanks == null || vanillaRanks.Length == 0) return 1;
        if (choiceIndex < 0) choiceIndex = 0;
        if (choiceIndex >= vanillaRanks.Length) choiceIndex = vanillaRanks.Length - 1;
        return vanillaRanks[choiceIndex];
    }
}

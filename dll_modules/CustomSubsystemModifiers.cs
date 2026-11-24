using UnityEngine;

/// <summary>
/// Lets a ship subsystem contribute to a crew-veterancy stat it does not drive
/// stock, e.g. Engineering adding to Turret accuracy.
///
/// The merge script patches every return of
/// ShipDataTable.GetCrewVeterancyBonus(ship, stat, battle) to pass through
/// <see cref="Apply"/>. That method is the single funnel for crew-veterancy
/// bonuses: CalculateCrewVeterancyPercentageBonus (value * (1 + bonus)),
/// CalculateCrewVeterancyFixedBonus (value + bonus) and the subsystem tooltip
/// helper SubSystemBase.INJINIKABHG all call it. A bonus is therefore in the
/// same unit as a veterancy rank's BonusAmount: a fraction for percentage
/// stats (0.25 = +25%) and an absolute amount for fixed stats.
///
/// Entries are parallel arrays so the component serializes with plain Unity
/// vectors (no nested serializable type to merge). Entry i adds
/// bonus = FloatRange(bonusMin[i], bonusMax[i]).Evaluate(source.HealthPC)
/// to stat stats[i], where source is the ship's subsystem selected by
/// sources[i]. This is the same health-to-effect evaluation stock subsystems
/// use for their own FloatRange settings (unclamped lerp on HealthPC).
/// </summary>
public sealed class CustomSubsystemModifiers : MonoBehaviour
{
    // Source subsystem codes. They match the hundreds digit of the stat enum.
    public const int SourceFireControl = 0;
    public const int SourceEngineering = 1;
    public const int SourceArmory = 2;
    public const int SourceCic = 3;
    public const int SourceHangar = 4;
    public const int SourceNavigation = 5;
    public const int SourceTechLab = 6;
    private const int SourceCount = 7;

    [SerializeField] public int[] stats = new int[0];
    [SerializeField] public int[] sources = new int[0];
    [SerializeField] public float[] bonusMin = new float[0];
    [SerializeField] public float[] bonusMax = new float[0];

    private SubSystemBase[] subsystems;

    /// <summary>
    /// Hook target for ShipDataTable.GetCrewVeterancyBonus. Returns the stock
    /// bonus unchanged when the ship has no component or no entry for the stat.
    /// </summary>
    public static float Apply(ShipInfo ship, FEDNAJLFKEF stat, float bonus)
    {
        if (ship == null) return bonus;
        CustomSubsystemModifiers modifiers = ship.GetComponent<CustomSubsystemModifiers>();
        if (modifiers == null) return bonus;
        return bonus + modifiers.Bonus(stat);
    }

    /// <summary>Total extra bonus this ship's subsystems add to one stat.</summary>
    public float Bonus(FEDNAJLFKEF stat)
    {
        if (stats == null || sources == null || bonusMin == null || bonusMax == null) return 0f;
        int count = Mathf.Min(Mathf.Min(stats.Length, sources.Length), Mathf.Min(bonusMin.Length, bonusMax.Length));
        float total = 0f;
        for (int i = 0; i < count; i++)
        {
            if (stats[i] != (int)stat) continue;
            SubSystemBase source = Source(sources[i]);
            if (source == null) continue;
            float health;
            try
            {
                health = source.JFGBMFEOOCJ; // obfuscated property name for get_HealthPC
            }
            catch (System.NullReferenceException)
            {
                continue; // subsystem not initialised yet
            }
            total += bonusMin[i] + (bonusMax[i] - bonusMin[i]) * health;
        }
        return total;
    }

    private SubSystemBase Source(int code)
    {
        if (code < 0 || code >= SourceCount) return null;
        if (subsystems == null)
        {
            subsystems = new SubSystemBase[SourceCount];
            SubSystemBase[] found = GetComponentsInChildren<SubSystemBase>(true);
            for (int i = 0; i < found.Length; i++)
            {
                int slot = Code(found[i]);
                if (slot >= 0 && subsystems[slot] == null) subsystems[slot] = found[i];
            }
        }
        return subsystems[code];
    }

    private static int Code(SubSystemBase subsystem)
    {
        if (subsystem is FireControl) return SourceFireControl;
        if (subsystem is Engineering) return SourceEngineering;
        if (subsystem is Armory) return SourceArmory;
        if (subsystem is CIC) return SourceCic;
        if (subsystem is Hangar) return SourceHangar;
        if (subsystem is BlackLab.BSG.Navigation) return SourceNavigation;
        if (subsystem is TechLab) return SourceTechLab;
        return -1;
    }
}

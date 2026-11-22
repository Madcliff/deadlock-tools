using System.Collections.Generic;

/// <summary>Adds the 32,000-point option to skirmish fleet-cap choices.</summary>
public static class CustomSkirmishFleetCap
{
    public static List<int> Include32k(List<int> options)
    {
        if (options != null && !options.Contains(32000))
            options.Add(32000);
        return options;
    }
}

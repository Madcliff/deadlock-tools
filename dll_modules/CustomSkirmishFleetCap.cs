using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;

/// <summary>
/// Fleet limits for the custom-module DLL (DLL page / `dll patch --skirmish-ships N --fleet-caps A,B --campaign-ships N`).
///
/// * Fleet-cap choices: MPDataTables.GetFleetPointOptionsPvP / GetFleetPointOptionsCoOp return through
///   IncludeLargeCaps, which adds the extra caps (default 32,000 and 64,000). The fleet editor refuses to save a fleet
///   above the largest PvP option, so the largest extra cap is also the save limit.
/// * Ships per skirmish / multiplayer fleet: every read of SkirmishDataTables.maxShips goes through MaxShips, which
///   returns the configured count instead of the level data's (stock 10) when one is set.
///
/// * Capital ships a campaign fleet may take into battle: every read of WarRoomDataTables.FleetInfo.MaxShips goes
///   through CampaignMaxShips (level data: stock 7). The single support slot is separate and fixed by the game.
///
/// All come from StreamingAssets/deadlock-fleet.txt, written by the toolkit:
///   ships 20
///   fleet_caps 32000 64000
///   campaign_ships 14
/// Without the file, or a line, the defaults apply (level-data ship counts; caps 32,000 and 64,000).
/// Both players of a multiplayer game need the same file.
/// </summary>
public static class CustomSkirmishFleetCap
{
    public const string FileName = "deadlock-fleet.txt";
    public static readonly int[] DefaultCaps = MakeDefaultCaps();

    private static int[] MakeDefaultCaps()        // no array initializer (see CustomJamming): explicit stores
    {
        int[] caps = new int[2];
        caps[0] = 32000;
        caps[1] = 64000;
        return caps;
    }

    private static bool loaded;
    private static int ships;              // 0 = keep the level data's value
    private static int campaignShips;      // 0 = keep the level data's value
    private static int[] caps;

    public static List<int> IncludeLargeCaps(List<int> options)
    {
        if (options == null) return options;
        Load();
        foreach (int cap in caps)
            if (cap > 0 && !options.Contains(cap)) options.Add(cap);
        options.Sort();
        return options;
    }

    public static int MaxShips(int levelValue)
    {
        Load();
        return ships > 0 ? ships : levelValue;
    }

    public static int CampaignMaxShips(int levelValue)
    {
        Load();
        return campaignShips > 0 ? campaignShips : levelValue;
    }

    private static void Load()
    {
        if (loaded) return;
        loaded = true;
        caps = DefaultCaps;
        try
        {
            string path = Path.Combine(Application.streamingAssetsPath, FileName);
            if (!File.Exists(path)) return;
            foreach (string raw in File.ReadAllLines(path))
            {
                string line = raw.Split(new char[] { '#' })[0].Trim();
                if (line.Length == 0) continue;
                string[] parts = line.Split((char[])null, StringSplitOptions.RemoveEmptyEntries);
                if (parts[0] == "ships" && parts.Length > 1)
                {
                    int value;
                    if (int.TryParse(parts[1], out value) && value > 0 && value < 1000) ships = value;
                }
                else if (parts[0] == "campaign_ships" && parts.Length > 1)
                {
                    int value;
                    if (int.TryParse(parts[1], out value) && value > 0 && value < 1000) campaignShips = value;
                }
                else if (parts[0] == "fleet_caps")
                {
                    var list = new List<int>();
                    for (int i = 1; i < parts.Length; i++)
                    {
                        int value;
                        if (int.TryParse(parts[i].Replace(",", ""), out value) && value > 0) list.Add(value);
                    }
                    caps = list.ToArray();
                }
            }
        }
        catch (Exception e)
        {
            Debug.LogError("CustomSkirmishFleetCap: could not read " + FileName + ": " + e.Message);
        }
    }
}

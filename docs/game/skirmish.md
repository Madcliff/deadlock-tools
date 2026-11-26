# Skirmish fleets

Read from the stock `Assembly-CSharp.dll` (1.5.113) and level files.

## Ships per fleet

`SkirmishDataTables` (a MonoBehaviour in level6, level7, level8 and level9, with the 15 skirmish maps) has:

- `maxShips` (stock 10): "the maximum number of ships allowed in skirmish and multiplayer on PC";
- `maxShipsConsole` (stock 8): the console limit.

`get_MaxShips` is read by:

- `FleetGroupPanelConnector`, which passes it to `FleetGroupPanel.MaxShips`. The panel hides the add-ship menu and
  disables the buy button once the fleet has that many ships. The panel has no fixed number of slots.
- `FleetGroupSaveLoadManager`:
  - on load, it **deletes** any saved fleet with more ships than the limit ("Fleet {0} is beyond the maximum ship
    count, removing the fleet..."), so lowering the limit later removes larger fleets;
  - it also passes the limit to each `FleetGroup`.

Both players of a multiplayer game need the same value. Two ways to change it:

- `shipdb setskirmishinfo maxShips N` edits the level data;
- `dll patch --skirmish-ships N` (DLL page → Fleet limits) needs the custom-module DLL, whose merge routes all three
  `maxShips` getters through `CustomSkirmishFleetCap.MaxShips`. It wins over the level data when set.

Other objects in mission levels (240-byte objects in level41, 44, 76, …) also parse as this layout, reading
`maxShips` as 1220. The tool only accepts a table whose maps are named and have a `skirmish…` environment. Not
checked: whether every skirmish map deploys 20 ships cleanly. Deployment uses each ship's
`CollisionExclusionZoneRadius` inside the map's deployment zone.

## Fleet point caps

`MPDataTables.GetFleetPointOptionsPvP` / `GetFleetPointOptionsCoOp` return the cap choices. The custom-module DLL
appends the extra caps to both (`CustomSkirmishFleetCap.IncludeLargeCaps`, hooked by `merge_custom_modules.cs`): by
default 32,000 and 64,000, or the list from `dll patch --fleet-caps A,B`.

`FleetSelectAndEditWidget` refuses to save a fleet worth more than the largest PvP option (text 0x306, "cannot save
a fleet of more than {0} points"), so the 64,000 option also lifts that limit.

## Campaign fleets

`WarRoomDataTables.FleetInfo.MaxShips` (level5/level85, stock 7) is the number of capital ships a campaign fleet takes
into battle. `BattleQueueShipList.Configure` shows "capital n/MaxShips" and "support n/1". The **1** is a constant: a
fleet has exactly one support slot. `FleetGroup.GNFCEDFIAJB` picks the best support candidate among the fleet's ships,
and about a dozen battle-queue, fleet-panel and deployment methods assume that single slot. Further support ships
count as ordinary fleet ships. Raising the support count would mean rewriting those methods, so no option does it.

Set the capital count with `shipdb setfleetinfo MaxShips N` (level data) or `dll patch --campaign-ships N`. The
custom-module DLL routes all 20 `FleetInfo.MaxShips` reads (battle queue, fleet panels, `FleetGroup`) through
`CustomSkirmishFleetCap.CampaignMaxShips`.

## The settings file

`StreamingAssets/deadlock-fleet.txt`, one setting per line; a missing line keeps the default:

    ships 20
    fleet_caps 32000 64000
    campaign_ships 14

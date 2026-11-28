# Campaign Mk II refit

Enable **Campaign Mk II refit** under **DLL → Mechanics**. Install the custom-module DLL and
`StreamingAssets/deadlock-mechanics.txt`, then start the game.

Right-click the ship name in an editable Colonial campaign fleet panel. The refit prompt is available
for a Jupiter or Mercury once its Mk II is unlocked and usable in that campaign. It requires enough
fleet points for the upgrade and charges the difference in ship build costs, with a minimum of zero.

| Base hull | Refit | Ship type IDs |
|---|---|---|
| Jupiter | Jupiter Mk II | 0 → 24 |
| Mercury | Mercury Mk II | 29 → 206 |

Mercury Mk II is a custom ship. The campaign needs its corresponding ship table entry, factory registrations,
bundle, enum member and unlock. Existing campaigns retain their research state.

The refit changes the existing `FleetShip` record so its ID, name, crew experience, officer and fleet
association are preserved. Native `FleetShip.PEDBAHFNIAC` normalizes the Mk II's squadron and munition
slots. Remaining per-slot missile counts carry forward when the slot still has the same munition.
Saved squadron health follows compatible slots. New or changed squadron assignments start with full health.
Save the campaign normally to persist the refit.

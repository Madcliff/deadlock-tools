# Deadlock Tools v0.6.3

## Battle abilities

- **Custom magazine capacity** applies during battle setup, so fresh loads show the expanded ammunition count immediately. The munition picker also shows the selected ship's base capacity, refreshed on bundle edits, clones, and imports.
- **Campaign Mk II refit** is optional under DLL → Mechanics. Right-click a Jupiter or Mercury's ship name to upgrade it after unlocking the corresponding Mk II. Compatible squadron slots keep their saved health.
- **Drone self-targeting** can be toggled under DLL → Mechanics.

- **Vortex Crush** targets a 3D point during planning, pulls during movement, and applies distance-scaled armour damage at the end of movement. Its control panel includes a friendly-fire toggle and a configurable hull overflow percentage, defaulting to 25%.
- **Hull and Armour Repair Drones** show their remaining and total stock directly on their ability buttons.

- **Armour Repair Drones** have their own action and stock and can be installed alongside Hull Repair Drones. Both abilities can target their own ship. Drone Resupply can also replenish either repair drone bank through two independent switches and per-bank rates in the same module as Deploy Plating. Existing modules default to Deploy Plating resupply only.
- The hull-repair drone button ignores Deploy Plating launch events.
- **Capital Jamming battery settings** are editable per ship in the editor and CLI.
- **RSpam AI ship priorities** can be reordered, replaced, or cleared in the editor or CLI.

- **Coordinated Reload, Machine Learning Matrix, and RSpam** can be granted from stock donors in the editor or CLI.
- **Hull Repair Drones** can be added to a ship from the Cylon Hydra, with configurable capacity, salvo size, repair rate, lifetime and launch delay.
- **Rebuilt squadrons start with clean combat state.** Replacement clears stale disabling effects, jamming, orders, and weapon targets before the squadron returns to the hangar.
- **EMP checks fighter and carrier allegiance.** Experimental and Targeted EMP protect friendly fighters even if a child craft reports different allegiance from its carrier.
- **Deploy Plating can target its own ship.** The custom DLL includes eligible casters in the target picker.
- **Capital jamming consumes battery power.** Any capital ship with both abilities uses one shared battery, regardless of which ability was added first. Capital ships without Stealth use a recharging reserve. **Save ship** prepares the DLL even when Jamming is the ship's only added ability.

## Campaign

- Commander trees can offer a **Tylium per turn** skill. A trained officer assigned to a Colonial fleet with ships adds the configured amount to the campaign income report each turn.

## Ship editor

- **Custom ship modules** show whether a module is installed, available to add, or queued for the next save.

- **Turnaround repair bays** can be increased under **Installed Abilities → Turnaround Repair Bay → Total turnaround repair bays**. In battle they have a dedicated **Fighters → Repair Bay** submenu.
- **Launch bays** now expose local rotation and let you edit, add, and remove launch path points in **Weapon layout**.
- **Save ship** validates all level edits before replacing work-tree files. If the DLL/UI update fails after the bundle is written, **Save ship** retries the update using the saved bundle. Errors stay visible beside the remaining changes.

## Battle UI
- **Four-column slot grid by ship type.** Add `slots4 SHIP_ID` to `deadlock-battle-hud.txt` to fit four squadron, munition, or indexed repair-bay buttons in each row with extra space for count labels. Two-column ability buttons also have more room for their labels.
- **Force two columns by ship type.** Add a ship ID to `StreamingAssets/deadlock-battle-hud.txt` to use the two-column ability layout at any screen height. See [game/battle-ui.md](game/battle-ui.md).

- Ship types can display Hull Repair Drones as an ability button using `hullrepairability SHIP_ID` in the battle HUD config.

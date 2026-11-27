# Unreleased changes

## Battle abilities

- **Armour Repair Drones** have their own action and stock and can be installed alongside Hull Repair Drones. Both abilities can target their own ship. Drone Resupply can also replenish either repair drone bank through two independent switches. Existing modules default to Deploy Plating resupply only.
- The hull-repair drone button ignores Deploy Plating launch events.
- **Capital Jamming battery settings** are editable per ship in the editor and CLI.
- **RSpam AI ship priorities** can be reordered, replaced, or cleared in the editor or CLI.

- **Coordinated Reload, Machine Learning Matrix, and RSpam** can be granted from stock donors in the editor or CLI.
- **Hull Repair Drones** can be added to a ship from the Cylon Hydra, with configurable capacity, salvo size, repair rate, lifetime and launch delay.
- **Rebuilt squadrons start with clean combat state.** Replacement clears stale disabling effects, jamming, orders, and weapon targets before the squadron returns to the hangar.
- **EMP checks fighter and carrier allegiance.** Experimental and Targeted EMP protect friendly fighters even if a child craft reports different allegiance from its carrier.
- **Deploy Plating can target its own ship.** The custom DLL includes eligible casters in the target picker.
- **Capital jamming consumes battery power.** Any capital ship with both abilities uses one shared battery, regardless of which ability was added first. Capital ships without Stealth use a recharging reserve. **Save ship** prepares the DLL even when Jamming is the ship's only added ability.

## Ship editor

- **Turnaround repair bays** can be increased under **Installed Abilities → Turnaround Repair Bay → Total turnaround repair bays**. In battle they have a dedicated **Fighters → Repair Bay** submenu.
- **Launch bays** now expose local rotation and let you edit, add, and remove launch path points in **Weapon layout**.
- **Save ship** validates all level edits before replacing work-tree files. If the DLL/UI update fails after the bundle is written, **Save ship** retries the update using the saved bundle. Errors stay visible beside the remaining changes.

## Battle UI
- **Four-column slot grid by ship type.** Add `slots4 SHIP_ID` to `deadlock-battle-hud.txt` to fit four squadron, munition, or indexed repair-bay buttons in each row with extra space for count labels.
- **Force two columns by ship type.** Add a ship ID to `StreamingAssets/deadlock-battle-hud.txt` to use the two-column ability layout at any screen height. See [game/battle-ui.md](game/battle-ui.md).

# Battle ship panel (right-hand side)

Read from the stock `Assembly-CSharp.dll` (1.5.113) and `level9` (the battle scene's UI).

## Layout

`ShipInfoPanel/Overscan/root/StackedPanels/RightPanels` is the right-hand column:

- anchored bottom-right;
- a vertical layout (spacing 4, bottom padding 20) with a ContentSizeFitter (vertical preferred), so it grows upward
  as content is added.

Its `MainPanels` hold, among others:

- **`SquadronMunitionSlots/SlotList`** (`ShipInfoPanel.slotList`): squadron launch slots, munition slots and repair
  bays. A grid with 2 columns of 140×50 cells (spacing 10×5). Slot widgets are added as needed, so the grid has no
  hard limit.
- **`CommandButtons/Keyboard/CommandButtonWidgetList`** (`commandButtonWidgetList`): the ability buttons (Focus Fire,
  Stealth, Flak, …). A vertical layout (spacing 5) inside a 292-wide list.
  - `Awake` creates a pool of **20** `ShipInfoPanelCommandButtonWidget`s.
  - The panel collects the ship's `ActionBase` components with `VisibleInShipInfoPanel`, sorts them by
    `ShipInfoPanelOrder` and assigns them to the pool in order (`set_AttachedAction`). Actions beyond 20 get no
    button.
  - Munition and launch buttons are not here: `FireMissileAction` and `LaunchFightersAction` return false for
    `VisibleInShipInfoPanel`; they are slots in `SlotList`.

The status cards to the left of the column are in `StackedPanels`: jamming, Hull Resin, stealth battery, munition
supply and so on.

## Ability buttons in two columns for configured ships

A ship with many squadrons, munitions and abilities can make `RightPanels` taller than the screen, so the top slots
or the bottom buttons end up off-screen (the Mercury Mk II: 14 launch slots, 6 munitions, 2 repair bays, 10
abilities). The custom-module DLL hooks both `ShipInfoPanel` methods that call `set_AttachedAction` (one is an
obfuscation decoy) and calls `CustomBattleHud.FitCommandButtons` at their end. That method:

- swaps the list's `VerticalLayoutGroup` for a two-column `GridLayoutGroup` (cells half the list width, the buttons'
  own height) when the selected ship type has an override, and shrinks the
  labels to fit (best fit, minimum 8);
- puts the stock column and label settings back when a ship without an override is selected.

To force two columns for a specific ship, install the updated custom-module DLL and put its numeric
`ShipInfo.ShipType` in `BSG_Data/StreamingAssets/deadlock-battle-hud.txt`, one ID per line. For the local
Mercury Mk II, use `206`. Lines may include comments starting with `#`. The game reads the file once per
launch. Remove the ID and restart the game to return that ship to one column.

Add a line such as `slots4 206` to the same file to give the Mercury Mk II four columns for squadron,
munition, and indexed repair-bay slots. Four-column mode widens the panel and the individual buttons to give count labels more space.
The ability-button layout has its own setting.

Turnaround repair bays are grouped under **Fighters → Repair Bay**. Each bay has an independent action and shortcut.
At eight bays, the last two appear under **Other**.

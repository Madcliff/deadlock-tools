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

## Ability buttons in two columns when needed

A ship with many squadrons, munitions and abilities can make `RightPanels` taller than the screen, so the top slots
or the bottom buttons end up off-screen (the Mercury Mk II: 14 launch slots, 6 munitions, 2 repair bays, 10
abilities). The custom-module DLL hooks both `ShipInfoPanel` methods that call `set_AttachedAction` (one is an
obfuscation decoy) and calls `CustomBattleHud.FitCommandButtons` at their end. That method:

- works out how tall `RightPanels` would be with the stock one-column list;
- swaps the list's `VerticalLayoutGroup` for a two-column `GridLayoutGroup` (cells half the list width, the buttons'
  own height) **only when that height exceeds the screen**, and shrinks the labels to fit (best fit, minimum 8);
- puts the stock column and label settings back when a ship that fits is selected.

Stock ships are not affected.

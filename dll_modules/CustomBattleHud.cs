using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using UnityEngine;
using UnityEngine.UI;
using Object = UnityEngine.Object;

/// <summary>
/// Battle ship panel additions: refreshes local armour, adds the armour repair card to the vanilla status stack, and
/// lays the ability buttons out in two columns for configured ship types.
/// </summary>
public static class CustomBattleHud
{
    private const string CardName = "__DeadlockArmourRepair";
    private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public;
    private static readonly FieldInfo Selected = typeof(ShipInfoPanel).GetField("ECPOPMDBACP", Private);
    private static readonly MethodInfo ArmourRefresh = typeof(ShipInfoPanel).GetMethod("KNABGBMAIKE", Private);

    private static readonly FieldInfo CommandList = typeof(ShipInfoPanel).GetField("commandButtonWidgetList", Private);
    private static readonly FieldInfo SlotList = typeof(ShipInfoPanel).GetField("slotList", Private);
    private static readonly FieldInfo RightPanels = typeof(ShipInfoPanel).GetField("rightPanels", Private);
    private static readonly Dictionary<Text, bool> labelBestFit = new Dictionary<Text, bool>();
    private static float stockSpacing = -1f;
    private const string LayoutFile = "deadlock-battle-hud.txt";
    private static HashSet<int> doubleColumnShips;
    private static readonly HashSet<int> fourSlotColumnShips = new HashSet<int>();
    private static readonly Dictionary<GridLayoutGroup, Vector2> stockSlotSizes = new Dictionary<GridLayoutGroup, Vector2>();
    private static readonly Dictionary<RectTransform, float> stockWidths = new Dictionary<RectTransform, float>();
    private static readonly Dictionary<RectTransform, float> stockPositionsX = new Dictionary<RectTransform, float>();

    private const float FourSlotExtra = 288f;
    private const float DoubleAbilityExtra = 144f;
    private static readonly MethodInfo HullRepairHidden = typeof(ActionBase).GetMethod("get_HideInMenu", Private);
    private static readonly HashSet<int> hullRepairAbilityShips = new HashSet<int>();

    public static bool HullRepairAsAbility(HullRepairAction action)
    {
        LoadLayouts();
        var ship = action.GetComponentInParent<ShipInfo>();
        return ship != null && hullRepairAbilityShips.Contains((int)ship.ShipType);
    }

    public static bool HideHullRepairIcon(HullRepairAction action)
    {
        return (bool)HullRepairHidden.Invoke(action, null) || HullRepairAsAbility(action);
    }

    private static float StockWidth(RectTransform rect)
    {
        float width;
        if (!stockWidths.TryGetValue(rect, out width))
        {
            width = rect.rect.width;
            if (width <= 0f) return 0f;
            stockWidths.Add(rect, width);
        }
        return width;
    }

    private static void ResizeWidth(RectTransform rect, float extra)
    {
        float width = StockWidth(rect);
        if (width > 0f)
            rect.SetSizeWithCurrentAnchors(RectTransform.Axis.Horizontal, width + extra);
    }

    private static void OffsetX(RectTransform rect, float offset)
    {
        float x;
        if (!stockPositionsX.TryGetValue(rect, out x))
        {
            x = rect.anchoredPosition.x;
            stockPositionsX.Add(rect, x);
        }
        rect.anchoredPosition = new Vector2(x + offset, rect.anchoredPosition.y);
    }

    // These children use fixed left anchors. Keep their right edges at the stock margins.
    private static void ResizeLeftAnchored(RectTransform rect, float extra, float parentExtra)
    {
        ResizeWidth(rect, extra);
        OffsetX(rect, parentExtra - (1f - rect.pivot.x) * extra);
    }

    private static void LoadLayouts()
    {
        if (doubleColumnShips == null)
        {
            doubleColumnShips = new HashSet<int>();
            try
            {
                string path = Path.Combine(Application.streamingAssetsPath, LayoutFile);
                if (File.Exists(path))
                {
                    foreach (string raw in File.ReadAllLines(path))
                    {
                        int comment = raw.IndexOf('#');
                        string line = (comment < 0 ? raw : raw.Substring(0, comment)).Trim();
                        const string slotsPrefix = "slots4 ";
                        const string hullPrefix = "hullrepairability ";
                        bool slots = line.StartsWith(slotsPrefix, StringComparison.Ordinal);
                        bool hull = line.StartsWith(hullPrefix, StringComparison.Ordinal);
                        string value = slots ? line.Substring(slotsPrefix.Length) :
                            hull ? line.Substring(hullPrefix.Length) : line;
                        int id;
                        if (int.TryParse(value.Trim(),
                            NumberStyles.None, CultureInfo.InvariantCulture, out id))
                            (slots ? fourSlotColumnShips : hull ? hullRepairAbilityShips : doubleColumnShips).Add(id);
                    }
                }
            }
            catch (IOException error)
            {
                Debug.LogWarning("CustomBattleHud: " + error.Message);
            }
            catch (UnauthorizedAccessException error)
            {
                Debug.LogWarning("CustomBattleHud: " + error.Message);
            }
        }
    }

    private static void FitSlots(ShipInfoPanel panel, RectTransform right, bool fourColumns, bool doubleAbilities)
    {
        var list = SlotList == null ? null : SlotList.GetValue(panel) as Component;
        if (list == null) return;
        var grid = list.GetComponent<GridLayoutGroup>();
        var rect = list.transform as RectTransform;
        if (grid == null || rect == null) return;
        var slotPanel = rect.parent as RectTransform;
        var main = slotPanel == null ? null : slotPanel.parent as RectTransform;
        var status = right.parent == null ? null : right.parent.Find("HackingAndBoarding") as RectTransform;
        if (slotPanel != null && main != null && main.parent == right && status != null)
        {
            StockWidth(main);
            StockWidth(slotPanel);
            StockWidth(rect);
            float extra = fourColumns ? FourSlotExtra : doubleAbilities ? DoubleAbilityExtra : 0f;
            float slotExtra = fourColumns ? FourSlotExtra : 0f;
            ResizeWidth(right, extra);
            ResizeLeftAnchored(main, extra, extra);
            ResizeLeftAnchored(slotPanel, slotExtra, extra);
            ResizeLeftAnchored(rect, slotExtra, slotExtra);
            OffsetX(status, -extra);
        }
        Vector2 stockSize;
        if (!stockSlotSizes.TryGetValue(grid, out stockSize))
        {
            stockSize = grid.cellSize;
            stockSlotSizes.Add(grid, stockSize);
        }
        grid.constraintCount = fourColumns ? 4 : 2;
        float usableWidth = rect.rect.width - grid.padding.horizontal;
        if (usableWidth <= 3f * grid.spacing.x) usableWidth = 2f * stockSize.x + grid.spacing.x;
        grid.cellSize = fourColumns
            ? new Vector2(Mathf.Min(stockSize.x, (usableWidth - 3f * grid.spacing.x) / 4f), stockSize.y)
            : stockSize;
    }

    /// <summary>Called after the panel assigns actions to its ability buttons (merge: InjectBattleHud).</summary>
    public static void FitCommandButtons(ShipInfoPanel panel)
    {
        if (panel == null || CommandList == null || RightPanels == null) return;
        LoadLayouts();
        var ship = Selected == null ? null : Selected.GetValue(panel) as ShipInfo;
        var list = CommandList.GetValue(panel) as Component;
        var right = RightPanels.GetValue(panel) as Component;
        if (list == null || right == null) return;
        RectTransform listRect = list.transform as RectTransform;
        RectTransform rightRect = right.transform as RectTransform;
        if (listRect == null || rightRect == null) return;
        bool needed = ship != null && doubleColumnShips.Contains((int)ship.ShipType);
        bool fourColumns = ship != null && fourSlotColumnShips.Contains((int)ship.ShipType);
        var keyboard = listRect.parent as RectTransform;
        var commands = keyboard == null ? null : keyboard.parent as RectTransform;
        if (commands != null)
        {
            StockWidth(commands);
            StockWidth(keyboard);
            StockWidth(listRect);
        }
        FitSlots(panel, rightRect, fourColumns, needed);
        if (commands != null)
        {
            float panelExtra = fourColumns ? FourSlotExtra : needed ? DoubleAbilityExtra : 0f;
            float commandExtra = needed ? panelExtra : 0f;
            ResizeLeftAnchored(commands, commandExtra, panelExtra);
            ResizeLeftAnchored(keyboard, commandExtra, commandExtra);
            ResizeLeftAnchored(listRect, commandExtra, commandExtra);
        }

        var buttons = new List<RectTransform>();
        foreach (Transform child in listRect)
            if (child.gameObject.activeSelf && child is RectTransform) buttons.Add((RectTransform)child);
        var grid = list.GetComponent<GridLayoutGroup>();
        var column = list.GetComponent<VerticalLayoutGroup>();
        if (column != null) stockSpacing = column.spacing;
        float spacing = stockSpacing >= 0f ? stockSpacing : 5f;
        float buttonHeight = 0f;
        foreach (var button in buttons) buttonHeight = Mathf.Max(buttonHeight, LayoutUtility.GetPreferredHeight(button));
        if (buttonHeight <= 0f) buttonHeight = 30f;

        if (needed && grid == null)
        {
            if (column != null) Object.DestroyImmediate(column);
            column = null;
            grid = list.gameObject.AddComponent<GridLayoutGroup>();
            grid.constraint = GridLayoutGroup.Constraint.FixedColumnCount;
            grid.constraintCount = 2;
            grid.startAxis = GridLayoutGroup.Axis.Horizontal;
            grid.childAlignment = TextAnchor.UpperLeft;
            grid.spacing = new Vector2(spacing, spacing);
        }
        else if (!needed && grid != null)
        {
            Object.DestroyImmediate(grid);
            grid = null;
            column = list.gameObject.AddComponent<VerticalLayoutGroup>();
            column.spacing = spacing;
            column.childAlignment = TextAnchor.UpperLeft;
            column.childControlHeight = true;
            column.childForceExpandHeight = false;
        }
        if (column != null)
        {
            // The grid writes its cell width to the pooled buttons. Let the column
            // size them again when selection switches back to a single-column ship.
            column.childControlWidth = true;
            column.childForceExpandWidth = true;
        }
        if (grid != null)
            grid.cellSize = new Vector2((listRect.rect.width - spacing) / 2f, buttonHeight);

        // labels shrink to fit the half-width buttons, and get their stock setting back in one column
        foreach (var button in buttons)
            foreach (var label in button.GetComponentsInChildren<Text>(true))
            {
                if (!labelBestFit.ContainsKey(label)) labelBestFit[label] = label.resizeTextForBestFit;
                if (grid != null)
                {
                    label.resizeTextMaxSize = Mathf.Max(label.resizeTextMaxSize, label.fontSize);
                    label.resizeTextMinSize = Mathf.Min(label.resizeTextMinSize > 0 ? label.resizeTextMinSize : 8, 8);
                    label.resizeTextForBestFit = true;
                }
                else label.resizeTextForBestFit = labelBestFit[label];
            }
        LayoutRebuilder.ForceRebuildLayoutImmediate(rightRect);
    }

    public static void RefreshArmour(ShipInfoPanel panel, ShipInfo ship)
    {
        if (panel != null && ship != null && Selected != null && ArmourRefresh != null &&
            Selected.GetValue(panel) as ShipInfo == ship)
            ArmourRefresh.Invoke(panel, null);
    }

    public static void RefreshRepairCard(ShipInfo ship, RegenerativeHullResinWidget template, BattleManager battle)
    {
        if (template == null || template.transform.parent == null) return;
        Transform parent = template.transform.parent;
        Transform card = parent.Find(CardName);
        var repair = ship == null ? null : ship.GetComponent<CustomArmourRepairModule>();
        if (repair == null)
        {
            if (card != null) card.gameObject.SetActive(false);
            return;
        }
        if (card == null)
        {
            GameObject clone = UnityEngine.Object.Instantiate(template.gameObject, parent, false);
            clone.name = CardName;
            card = clone.transform;
            card.SetSiblingIndex(template.transform.GetSiblingIndex() + 1);
            var hullBehaviour = clone.GetComponent<RegenerativeHullResinWidget>();
            hullBehaviour.enabled = false;
            UnityEngine.Object.Destroy(hullBehaviour);
        }
        Transform titleObject = card.Find("TitleLabel");
        Transform stats = card.Find("Stats");
        Transform rateObject = stats == null ? null : stats.Find("HullPerTurnLabel");
        Text title = titleObject == null ? null : titleObject.GetComponent<Text>();
        Text rateLabel = rateObject == null ? null : rateObject.GetComponent<Text>();
        if (title == null || rateLabel == null)
        {
            card.gameObject.SetActive(false);
            return;
        }
        var hullLabel = stats.Find("HullLabel");
        if (hullLabel != null) hullLabel.gameObject.SetActive(false);
        foreach (var bar in stats.GetComponentsInChildren<HealthBarWidget>(true))
            bar.gameObject.SetActive(false);
        RectTransform rect = rateLabel.rectTransform;
        rect.anchorMax = Vector2.one;
        rect.offsetMin = Vector2.zero;
        rect.offsetMax = Vector2.zero;
        title.text = "Armour Repair";
        float rate = repair.GetEffectiveRate();
        int ticks = battle == null || battle.EAJEIKEBKKE == null ? 120 : battle.EAJEIKEBKKE.CNNFCKEKJPM;
        rateLabel.text = string.Format(CultureInfo.InvariantCulture,
            "+{0:0.##} / turn per facing  ·  +{1:0.##} / sec", rate * ticks, rate * 10f);
        card.gameObject.SetActive(true);
    }
}

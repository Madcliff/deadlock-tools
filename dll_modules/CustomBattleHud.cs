using System.Collections.Generic;
using System.Globalization;
using System.Reflection;
using UnityEngine;
using UnityEngine.UI;

/// <summary>
/// Battle ship panel additions: refreshes local armour, adds the armour repair card to the vanilla status stack, and
/// lays the ability buttons out in two columns only when the stock single column would push the right-hand panel off
/// the screen (ships with many abilities, squadrons and munitions, such as a heavily modded carrier).
/// </summary>
public static class CustomBattleHud
{
    private const string CardName = "__DeadlockArmourRepair";
    private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public;
    private static readonly FieldInfo Selected = typeof(ShipInfoPanel).GetField("ECPOPMDBACP", Private);
    private static readonly MethodInfo ArmourRefresh = typeof(ShipInfoPanel).GetMethod("KNABGBMAIKE", Private);

    private static readonly FieldInfo CommandList = typeof(ShipInfoPanel).GetField("commandButtonWidgetList", Private);
    private static readonly FieldInfo RightPanels = typeof(ShipInfoPanel).GetField("rightPanels", Private);
    private static readonly Dictionary<Text, bool> labelBestFit = new Dictionary<Text, bool>();
    private static float stockSpacing = -1f;
    private const float ScreenMargin = 8f;

    /// <summary>Called after the panel assigns actions to its ability buttons (merge: InjectBattleHud).</summary>
    public static void FitCommandButtons(ShipInfoPanel panel)
    {
        if (panel == null || CommandList == null || RightPanels == null) return;
        var list = CommandList.GetValue(panel) as Component;
        var right = RightPanels.GetValue(panel) as Component;
        if (list == null || right == null) return;
        RectTransform listRect = list.transform as RectTransform;
        RectTransform rightRect = right.transform as RectTransform;
        RectTransform screen = rightRect == null ? null : rightRect.parent as RectTransform;
        if (listRect == null || rightRect == null || screen == null) return;

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

        // height of the button list in one column vs. as laid out now; the right-hand panel grows by the difference
        int count = buttons.Count;
        float oneColumn = count == 0 ? 0f : count * buttonHeight + (count - 1) * spacing;
        LayoutRebuilder.ForceRebuildLayoutImmediate(rightRect);
        float withOneColumn = rightRect.rect.height - LayoutUtility.GetPreferredHeight(listRect) + oneColumn;
        bool needed = withOneColumn > screen.rect.height - ScreenMargin;

        if (needed && grid == null)
        {
            if (column != null) Object.DestroyImmediate(column);
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
            column.childControlWidth = false;
            column.childControlHeight = true;
            column.childForceExpandWidth = false;
            column.childForceExpandHeight = false;
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

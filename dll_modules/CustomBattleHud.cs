using System.Globalization;
using System.Reflection;
using UnityEngine;
using UnityEngine.UI;

/// <summary>Refreshes local armour and adds the armour repair card to the vanilla status stack.</summary>
public static class CustomBattleHud
{
    private const string CardName = "__DeadlockArmourRepair";
    private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public;
    private static readonly FieldInfo Selected = typeof(ShipInfoPanel).GetField("ECPOPMDBACP", Private);
    private static readonly MethodInfo ArmourRefresh = typeof(ShipInfoPanel).GetMethod("KNABGBMAIKE", Private);

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

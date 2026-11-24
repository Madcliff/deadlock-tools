using System.Reflection;
using UnityEngine;
using UnityEngine.UI;

/// <summary>Per-ability unlock-rank overrides for the commander specialisation tree.</summary>
public static class CustomCommanderAbilityLevels
{
    public const int ComponentBoostLevels = 30;

    /// <summary>Returns a configured 1-based commander rank, or zero when the game default should apply.</summary>
    public static int GetAssignedRank(int abilityId)
    {
        switch (abilityId)
        {
            // GENERATED_RANK_CASES
            default: return 0;
        }
    }

    /// <summary>
    /// The vanilla DLL stores only three rank gates and indexes them by the choice's position.
    /// Use a per-ability override when assigned; otherwise preserve those original gates and
    /// reuse the final vanilla gate for choices beyond the original three.
    /// </summary>
    public static int GetUnlockRank(int abilityId, int choiceIndex, int[] vanillaRanks)
    {
        int assigned = GetAssignedRank(abilityId);
        if (assigned > 0) return assigned + 1; // game compares CommanderData.Rank + 1
        if (vanillaRanks == null || vanillaRanks.Length == 0) return 1;
        if (choiceIndex < 0) choiceIndex = 0;
        if (choiceIndex >= vanillaRanks.Length) choiceIndex = vanillaRanks.Length - 1;
        return vanillaRanks[choiceIndex];
    }
    /// <summary>Resolves the saved skill ID against the serialized skill records.</summary>
    public static CaptainSkill FindCaptainSkill(CaptainSkill[] skills, EAMANNMIMLO id)
    {
        if (skills != null)
        {
            int index = (int)id;
            if (index >= 0 && index < skills.Length && skills[index].Skill == id)
                return skills[index];
            foreach (var skill in skills)
                if (skill.Skill == id)
                    return skill;
        }
        return default(CaptainSkill);
    }

    public static void ConfigureSkillsScroll(Component list)
    {
        if (list == null) return;
        var content = list.transform as RectTransform;
        var parent = list.transform.parent as RectTransform;
        if (content == null || parent == null || parent.name != "Content") return;
        var scrollField = list.GetType().GetField("scrollRect",
            BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
        if (scrollField == null || scrollField.FieldType != typeof(ScrollRect)) return;

        var oldScroll = list.GetComponent<ScrollRect>();
        if (oldScroll != null)
        {
            oldScroll.enabled = false;
            oldScroll.verticalScrollbar = null;
            oldScroll.horizontalScrollbar = null;
        }

        scrollField.SetValue(list, CreateSkillsScroll(content, parent, "Commander Skills Scroll"));
    }

    public static void ConfigureRosterScroll(FleetGroupAcademyPanel academy)
    {
        if (academy == null) return;
        var roster = academy.transform.Find("root/ActiveComanders/Content/Panels");
        if (roster == null) return;

        foreach (Transform tab in roster)
        {
            var view = tab.Find("Scroll View");
            var scroll = view == null ? null : view.GetComponent<ScrollRect>();
            if (scroll == null || scroll.content == null || scroll.viewport == null) continue;
            scroll.horizontal = false;
            scroll.vertical = true;
            scroll.movementType = ScrollRect.MovementType.Clamped;
            var bar = scroll.verticalScrollbar;
            if (bar == null || !bar.transform.IsChildOf(view)) continue;
            var rect = (RectTransform)bar.transform;
            rect.SetSizeWithCurrentAnchors(RectTransform.Axis.Horizontal, 10f);
        }
    }

    public static void ConfigurePromotionScroll(PromotionSelectWidget tree)
    {
        if (tree == null) return;
        var parent = tree.transform.Find("Content") as RectTransform;
        var columns = parent == null ? null : parent.Find("Columns") as RectTransform;
        if (columns == null || parent.GetComponent<VerticalLayoutGroup>() == null) return;

        ExtendRankRuler(columns);
        // Keep the headings fixed and move the rank ruler and skill columns together.
        CreateSkillsScroll(columns, parent, "Commander Tree Scroll");
    }

    private static void ExtendRankRuler(RectTransform columns)
    {
        var ruler = columns.GetComponentInChildren<RankLineWidget>(true);
        if (ruler == null) return;
        var field = typeof(RankLineWidget).GetField("rankImages",
            BindingFlags.Instance | BindingFlags.NonPublic);
        var originals = field == null ? null : field.GetValue(ruler) as Image[];
        if (originals == null || originals.Length == 0 || originals.Length >= ComponentBoostLevels) return;

        var marker = originals[originals.Length - 1];
        if (marker == null || marker.transform.parent.parent != ruler.transform) return;
        var template = marker.transform.parent.gameObject;
        var markers = new Image[ComponentBoostLevels];
        System.Array.Copy(originals, markers, originals.Length);
        for (int i = originals.Length; i < markers.Length; i++)
        {
            var row = Object.Instantiate(template, ruler.transform, false);
            row.name = "Rank" + (i + 1);
            markers[i] = row.transform.Find(marker.name).GetComponent<Image>();
            markers[i].name = row.name + "Image";
            markers[i].enabled = false;
        }
        field.SetValue(ruler, markers);
    }

    private static ScrollRect CreateSkillsScroll(RectTransform content, RectTransform parent, string name)
    {
        var frame = CreateRect(name, parent);
        frame.SetSiblingIndex(content.GetSiblingIndex());
        var layout = frame.gameObject.AddComponent<LayoutElement>();
        layout.flexibleHeight = 1f;

        var viewport = CreateRect("Viewport", frame);
        viewport.anchorMax = Vector2.one;
        viewport.offsetMin = Vector2.zero;
        viewport.offsetMax = new Vector2(-15f, 0f);
        // The Academy sits on a world-space canvas. Match the native roster's stencil mask.
        viewport.gameObject.AddComponent<Image>();
        viewport.gameObject.AddComponent<Mask>().showMaskGraphic = false;

        content.SetParent(viewport, false);
        content.anchorMin = new Vector2(0f, 1f);
        content.anchorMax = Vector2.one;
        content.pivot = new Vector2(0.5f, 1f);
        content.anchoredPosition = Vector2.zero;
        content.sizeDelta = Vector2.zero;
        var fitter = content.GetComponent<ContentSizeFitter>();
        if (fitter == null) fitter = content.gameObject.AddComponent<ContentSizeFitter>();
        fitter.verticalFit = ContentSizeFitter.FitMode.PreferredSize;

        var track = CreateRect("Scrollbar", frame);
        track.anchorMin = new Vector2(1f, 0f);
        track.anchorMax = Vector2.one;
        track.pivot = new Vector2(1f, 0.5f);
        track.sizeDelta = new Vector2(10f, 0f);
        track.gameObject.AddComponent<Image>().color = new Color(0.34f, 0.16f, 0.11f, 0.8f);
        var sliding = CreateRect("Sliding Area", track);
        sliding.anchorMax = Vector2.one;
        sliding.offsetMin = Vector2.zero;
        sliding.offsetMax = Vector2.zero;
        var handle = CreateRect("Handle", sliding);
        handle.anchorMax = Vector2.one;
        handle.offsetMin = Vector2.zero;
        handle.offsetMax = Vector2.zero;
        var thumb = handle.gameObject.AddComponent<Image>();
        thumb.color = new Color(0.9f, 0.43f, 0.28f, 1f);
        var scrollbar = track.gameObject.AddComponent<Scrollbar>();
        scrollbar.direction = Scrollbar.Direction.BottomToTop;
        scrollbar.handleRect = handle;
        scrollbar.targetGraphic = thumb;

        var scroll = frame.gameObject.AddComponent<ScrollRect>();
        scroll.viewport = viewport;
        scroll.content = content;
        scroll.horizontal = false;
        scroll.vertical = true;
        scroll.movementType = ScrollRect.MovementType.Clamped;
        scroll.scrollSensitivity = 40f;
        scroll.verticalScrollbar = scrollbar;
        scroll.verticalScrollbarVisibility = ScrollRect.ScrollbarVisibility.AutoHide;
        scroll.verticalNormalizedPosition = 1f;
        return scroll;
    }

    private static RectTransform CreateRect(string name, Transform parent)
    {
        var rect = (RectTransform)new GameObject(name, typeof(RectTransform)).transform;
        rect.gameObject.layer = parent.gameObject.layer;
        rect.SetParent(parent, false);
        rect.anchorMin = Vector2.zero;
        rect.anchorMax = Vector2.zero;
        rect.anchoredPosition = Vector2.zero;
        rect.sizeDelta = Vector2.zero;
        return rect;
    }
}

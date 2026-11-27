using System;
using System.Reflection;
using UnityEngine;
using UnityEngine.UI;

/// <summary>Runtime support for independently indexed turnaround repair bays.</summary>
public static class CustomRepairBaySlots
{
    private static readonly FieldInfo ActionOwnerField = typeof(ActionBase).GetField(
        "NLMPABLKJHE", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
    private const int FirstShortcutFunction = 60;
    private const string ClonePrefix = "__DeadlockRepairBaySlot_";

    /// <summary>Returns the module paired with this action by serialized component order.</summary>
    public static TurnaroundRepairBayModule GetModuleForAction(TurnaroundRepairBayAction action)
    {
        if (action == null || ActionOwnerField == null) return null;
        ShipInfo owner = ActionOwnerField.GetValue(action) as ShipInfo;
        if (owner == null) return null;

        TurnaroundRepairBayAction[] actions = owner.GetComponentsInChildren<TurnaroundRepairBayAction>(true);
        TurnaroundRepairBayModule[] modules = owner.GetComponents<TurnaroundRepairBayModule>();
        int index = Array.IndexOf(actions, action);
        if (index < 0 || modules == null || index >= modules.Length) return null;
        return modules[index];
    }

    /// <summary>Mirrors every ship repair module into its own button in the native slot row.</summary>
    public static void Refresh(ShipInfoPanel panel, ShipInfo owner, RepairBaySlotButton template)
    {
        if (panel == null || template == null) return;
        Transform parent = template.transform.parent;
        if (parent == null) return;

        TurnaroundRepairBayModule[] modules = owner == null
            ? new TurnaroundRepairBayModule[0]
            : owner.GetComponents<TurnaroundRepairBayModule>();
        int count = modules == null ? 0 : modules.Length;

        for (int index = 0; index < Math.Max(1, count); index++)
        {
            RepairBaySlotButton button;
            if (index == 0)
            {
                button = template;
            }
            else
            {
                string name = ClonePrefix + index;
                Transform child = parent.Find(name);
                if (child == null)
                {
                    GameObject clone = UnityEngine.Object.Instantiate(
                        template.gameObject, parent, false);
                    clone.name = name;
                    button = clone.GetComponent<RepairBaySlotButton>();
                }
                else button = child.GetComponent<RepairBaySlotButton>();
                // The native two-column grid sizes and positions the row.
                var element = button == null ? null : button.GetComponent<LayoutElement>();
                if (element != null) element.ignoreLayout = false;
            }

            if (button == null) continue;
            bool visible = owner != null && index < count;
            button.gameObject.SetActive(visible);
            if (!visible) continue;
            button.transform.SetAsLastSibling();

            ShortcutKeyWidget key = button.GetComponentInChildren<ShortcutKeyWidget>(true);
            if (key != null) key.NCJFKMFJLPO = (FBJNDNDGFCA)(FirstShortcutFunction + index);
            button.UpdateInfo(owner, modules[index]);
        }

        foreach (RepairBaySlotButton button in parent.GetComponentsInChildren<RepairBaySlotButton>(true))
        {
            if (button == template || !button.gameObject.name.StartsWith(ClonePrefix, StringComparison.Ordinal))
                continue;
            int index;
            string suffix = button.gameObject.name.Substring(ClonePrefix.Length);
            if (int.TryParse(suffix, out index) && index >= count)
                button.gameObject.SetActive(false);
        }
    }

}

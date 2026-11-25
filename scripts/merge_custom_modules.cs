using System;
using System.Linq;
using System.Collections.Generic;
using Mono.Cecil;
using Mono.Cecil.Cil;

static class MergePrototype
{
    static void CopyAttrs(ICustomAttributeProvider from, ICustomAttributeProvider to, ModuleDefinition target)
    {
        foreach (var ca in from.CustomAttributes)
        {
            var copy = new CustomAttribute(target.ImportReference(ca.Constructor));
            foreach (var a in ca.ConstructorArguments)
                copy.ConstructorArguments.Add(new CustomAttributeArgument(target.ImportReference(a.Type), a.Value));
            foreach (var a in ca.Fields)
                copy.Fields.Add(new CustomAttributeNamedArgument(a.Name, new CustomAttributeArgument(target.ImportReference(a.Argument.Type), a.Argument.Value)));
            foreach (var a in ca.Properties)
                copy.Properties.Add(new CustomAttributeNamedArgument(a.Name, new CustomAttributeArgument(target.ImportReference(a.Argument.Type), a.Argument.Value)));
            to.CustomAttributes.Add(copy);
        }
    }

    static bool IsHudModule(string name)
    {
        return name == "CustomInstantPlatingModule" || name == "CustomEmpPulseModule" || name == "CustomTargetedEmpModule" || name == "CustomBattleHud" || name == "CustomArmourRepairModule" || name == "CustomModuleSubsystems";
    }

    static string MethodName(MethodReference method, TypeDefinition sourceType, ModuleDefinition target)
    {
        if (sourceType.Name != "CustomInstantPlatingAction" && sourceType.Name != "CustomEmpPulseAction" && sourceType.Name != "CustomTargetedEmpAction") return method.Name;
        var property = sourceType.Properties.FirstOrDefault(p => p.GetMethod != null && p.GetMethod.Name == method.Name);
        if (property == null) return method.Name;
        return target.Types.Single(t => t.Name == "ActionBase").Properties
            .Single(p => p.Name == property.Name).GetMethod.Name;
    }

    static TypeReference ImportType(TypeReference type, ModuleDefinition target, TypeDefinition sourceType, TypeDefinition destType)
    {
        if (type.FullName == sourceType.FullName) return destType;
        var localType = FindType(target, type.FullName);
        if (localType != null) return localType;
        if (IsHudModule(type.FullName))
        {
            var merged = target.Types.FirstOrDefault(t => t.FullName == type.FullName);
            if (merged != null) return merged;
        }
        if (type is GenericInstanceType gi)
        {
            var copy = new GenericInstanceType(ImportType(gi.ElementType, target, sourceType, destType));
            foreach (var arg in gi.GenericArguments) copy.GenericArguments.Add(ImportType(arg, target, sourceType, destType));
            return copy;
        }
        if (type is ArrayType ar) return new ArrayType(ImportType(ar.ElementType, target, sourceType, destType), ar.Rank);
        if (type is ByReferenceType br) return new ByReferenceType(ImportType(br.ElementType, target, sourceType, destType));
        if (type is PointerType pt) return new PointerType(ImportType(pt.ElementType, target, sourceType, destType));
        return target.ImportReference(type);
    }

    static TypeDefinition FindType(ModuleDefinition module, string fullName)
    {
        foreach (var type in module.Types)
        {
            if (type.FullName == fullName) return type;
            var nested = FindNestedType(type, fullName);
            if (nested != null) return nested;
        }
        return null;
    }

    static TypeDefinition FindNestedType(TypeDefinition parent, string fullName)
    {
        foreach (var type in parent.NestedTypes)
        {
            if (type.FullName == fullName) return type;
            var nested = FindNestedType(type, fullName);
            if (nested != null) return nested;
        }
        return null;
    }

    static MethodReference ImportMethod(MethodReference method, ModuleDefinition target, TypeDefinition sourceType, TypeDefinition destType)
    {
        if (method.DeclaringType.FullName == sourceType.FullName)
        {
            var match = destType.Methods.FirstOrDefault(m => m.Name == MethodName(method, sourceType, target) && m.Parameters.Count == method.Parameters.Count);
            if (match != null) return match;
        }
        if (IsHudModule(method.DeclaringType.FullName) && method.DeclaringType.FullName != sourceType.FullName)
        {
            var mergedModule = target.Types.Single(t => t.FullName == method.DeclaringType.FullName);
            return mergedModule.Methods.Single(m => m.Name == method.Name && m.Parameters.Count == method.Parameters.Count);
        }
        if (method is GenericInstanceMethod gm)
        {
            var element = ImportMethod(gm.ElementMethod, target, sourceType, destType);
            var copy = new GenericInstanceMethod(element);
            foreach (var arg in gm.GenericArguments) copy.GenericArguments.Add(ImportType(arg, target, sourceType, destType));
            return copy;
        }
        return target.ImportReference(method);
    }

    static void InjectMagazineCapacityHooks(ModuleDefinition module, TypeDefinition capacityType)
    {
        var shipInfo = module.Types.Single(t => t.Name == "ShipInfo");
        var scale = capacityType.Methods.Single(m => m.Name == "GetScaledCapacity" && m.Parameters.Count == 2);
        var tableCapacityField = "MaxMissiles";
        var statusCapacityField = "MEJKBAJFDPN";

        var resupply = shipInfo.Methods.Single(m => m.Name == "ResupplyMunitions" && m.Parameters.Count == 2);
        if (resupply.Body.Instructions.Any(i => i.OpCode == OpCodes.Call && i.Operand is MethodReference mr &&
                                                mr.DeclaringType.FullName == capacityType.FullName && mr.Name == scale.Name))
            throw new InvalidOperationException("magazine capacity hook already exists in ResupplyMunitions");
        var resupplySites = resupply.Body.Instructions
            .Where(i => i.OpCode == OpCodes.Ldfld && i.Operand is FieldReference fr && fr.Name == tableCapacityField)
            .ToList();
        if (resupplySites.Count != 1)
            throw new InvalidOperationException("expected one vanilla MaxMissiles read in ShipInfo.ResupplyMunitions; found " + resupplySites.Count);
        var resupplyStore = resupplySites[0].Next;
        if (resupplyStore == null)
            throw new InvalidOperationException("unexpected IL after MaxMissiles read in ResupplyMunitions");
        VariableDefinition resupplyLocal = resupplyStore.Operand as VariableDefinition;
        if (resupplyLocal == null)
        {
            int index = resupplyStore.OpCode == OpCodes.Stloc_0 ? 0 : resupplyStore.OpCode == OpCodes.Stloc_1 ? 1 :
                resupplyStore.OpCode == OpCodes.Stloc_2 ? 2 : resupplyStore.OpCode == OpCodes.Stloc_3 ? 3 : -1;
            if (index >= 0 && index < resupply.Body.Variables.Count) resupplyLocal = resupply.Body.Variables[index];
        }
        if (resupplyLocal == null)
            throw new InvalidOperationException("MaxMissiles is not stored in an expected local variable");
        var resupplyIl = resupply.Body.GetILProcessor();
        resupplyIl.InsertAfter(resupplyStore, Instruction.Create(OpCodes.Ldarg_0));
        resupplyIl.InsertAfter(resupplyStore.Next, Instruction.Create(OpCodes.Ldloc, resupplyLocal));
        resupplyIl.InsertAfter(resupplyStore.Next.Next, Instruction.Create(OpCodes.Call, scale));
        resupplyIl.InsertAfter(resupplyStore.Next.Next.Next, Instruction.Create(OpCodes.Stloc, resupplyLocal));

        var status = shipInfo.Methods.Single(m => m.Name == "GetMunitionsSlotStatusData" && m.Parameters.Count == 0);
        if (status.Body.Instructions.Any(i => i.OpCode == OpCodes.Call && i.Operand is MethodReference mr &&
                                              mr.DeclaringType.FullName == capacityType.FullName && mr.Name == scale.Name))
            throw new InvalidOperationException("magazine capacity hook already exists in GetMunitionsSlotStatusData");
        var statusSites = status.Body.Instructions
            .Where(i => i.OpCode == OpCodes.Stfld && i.Operand is FieldReference fr && fr.Name == statusCapacityField)
            .ToList();
        if (statusSites.Count != 1)
            throw new InvalidOperationException("expected one munition-slot max assignment; found " + statusSites.Count);

        var scaledLocal = new VariableDefinition(module.TypeSystem.Int32);
        status.Body.Variables.Add(scaledLocal);
        var statusIl = status.Body.GetILProcessor();
        var cursor = statusSites[0];
        var statusField = (FieldReference)statusSites[0].Operand;
        foreach (var instruction in new[] {
            Instruction.Create(OpCodes.Ldarg_0),
            Instruction.Create(OpCodes.Ldloc_0),
            Instruction.Create(OpCodes.Ldloc_1),
            Instruction.Create(OpCodes.Ldelema, module.ImportReference(statusField.DeclaringType)),
            Instruction.Create(OpCodes.Ldfld, statusField),
            Instruction.Create(OpCodes.Call, scale),
            Instruction.Create(OpCodes.Stloc, scaledLocal),
            Instruction.Create(OpCodes.Ldloc_0),
            Instruction.Create(OpCodes.Ldloc_1),
            Instruction.Create(OpCodes.Ldelema, module.ImportReference(statusField.DeclaringType)),
            Instruction.Create(OpCodes.Ldloc, scaledLocal),
            Instruction.Create(OpCodes.Stfld, statusField)
        }) {
            statusIl.InsertAfter(cursor, instruction);
            cursor = instruction;
        }
        Console.WriteLine("Injected per-ship magazine capacity into ShipInfo resupply and slot status paths");
    }

    static void InjectRepairBaySlotHooks(ModuleDefinition module, TypeDefinition slotSupport)
    {
        var helper = slotSupport.Methods.Single(m => m.Name == "GetModuleForAction" && m.Parameters.Count == 1);
        var actionType = module.Types.Single(t => t.Name == "TurnaroundRepairBayAction");
        var actionBase = module.Types.Single(t => t.Name == "ActionBase");
        var radialPosition = actionBase.Methods.Single(m => m.Name == "get_RadialMenuPosition" && m.Parameters.Count == 0);
        var shortcutKey = actionType.Methods.Single(m => m.Name == "get_ShortcutKey" && m.Parameters.Count == 0);
        var shortcutBody = new MethodBody(shortcutKey) { InitLocals = false, MaxStackSize = 2 };
        shortcutKey.Body = shortcutBody;
        shortcutBody.Instructions.Add(Instruction.Create(OpCodes.Ldarg_0));
        shortcutBody.Instructions.Add(Instruction.Create(OpCodes.Call, radialPosition));
        shortcutBody.Instructions.Add(Instruction.Create(OpCodes.Ldc_I4_4));
        shortcutBody.Instructions.Add(Instruction.Create(OpCodes.Sub));
        shortcutBody.Instructions.Add(Instruction.Create(OpCodes.Ldc_I4_S, (sbyte)60));
        shortcutBody.Instructions.Add(Instruction.Create(OpCodes.Add));
        shortcutBody.Instructions.Add(Instruction.Create(OpCodes.Ret));

        var actionAwake = actionType.Methods.Single(m => m.Name == "NMGOMKJNIIA" && m.Parameters.Count == 0);
        var moduleStore = actionAwake.Body.Instructions
            .Single(i => i.OpCode == OpCodes.Stfld && i.Operand is FieldReference fr && fr.Name == "BJJNCHAFLIN");
        var callGet = moduleStore.Previous;
        var shipFieldLoad = callGet == null ? null : callGet.Previous;
        var actionLoad = shipFieldLoad == null ? null : shipFieldLoad.Previous;
        var receiverLoad = actionLoad == null ? null : actionLoad.Previous;
        if (callGet == null || callGet.OpCode != OpCodes.Callvirt || shipFieldLoad.OpCode != OpCodes.Ldfld ||
            actionLoad.OpCode != OpCodes.Ldarg_0 || receiverLoad.OpCode != OpCodes.Ldarg_0)
            throw new InvalidOperationException("unexpected TurnaroundRepairBayAction module-binding IL");
        shipFieldLoad.OpCode = OpCodes.Call;
        shipFieldLoad.Operand = helper;
        actionAwake.Body.GetILProcessor().Remove(callGet);

        var panel = module.Types.Single(t => t.Name == "ShipInfoPanel");
        var refresh = slotSupport.Methods.Single(m => m.Name == "Refresh" && m.Parameters.Count == 3);
        var selectedShip = panel.Fields.Single(f => f.FieldType.Name == "ShipInfo" && f.Name == "ECPOPMDBACP");
        var templateButton = panel.Fields.Single(f => f.Name == "repairBaySlotButton");
        var update = panel.Methods.Single(m => m.Name == "NHHEIBGBHPE" && m.Parameters.Count == 0);
        var retSites = update.Body.Instructions.Where(i => i.OpCode == OpCodes.Ret).ToList();
        if (retSites.Count == 0) throw new InvalidOperationException("ShipInfoPanel update method has no return site");
        var il = update.Body.GetILProcessor();
        foreach (var ret in retSites)
        {
            // Keep branches aimed at the original return on the refresh path.
            ret.OpCode = OpCodes.Ldarg_0;
            var cursor = ret;
            foreach (var instruction in new[] {
                Instruction.Create(OpCodes.Ldarg_0),
                Instruction.Create(OpCodes.Ldfld, selectedShip),
                Instruction.Create(OpCodes.Ldarg_0),
                Instruction.Create(OpCodes.Ldfld, templateButton),
                Instruction.Create(OpCodes.Call, refresh),
                Instruction.Create(OpCodes.Ret)
            }) {
                il.InsertAfter(cursor, instruction);
                cursor = instruction;
            }
        }
        Console.WriteLine("Injected indexed repair shortcuts, action-to-module binding, and dynamic native slot buttons");
    }

    static void InjectBattleHud(ModuleDefinition module, TypeDefinition helper)
    {
        var panel = module.Types.Single(t => t.Name == "ShipInfoPanel");
        var show = panel.Methods.Single(m => m.Name == "CGEOMHHFFDI");
        var refresh = helper.Methods.Single(m => m.Name == "RefreshRepairCard");
        var fields = new[] { "ECPOPMDBACP", "regenerativeHullResinWidget", "MOFBMPAEICL" }
            .Select(name => panel.Fields.Single(f => f.Name == name)).ToArray();
        var il = show.Body.GetILProcessor();
        foreach (var ret in show.Body.Instructions.Where(i => i.OpCode == OpCodes.Ret).ToArray())
        {
            ret.OpCode = OpCodes.Nop;
            var cursor = ret;
            foreach (var field in fields)
            {
                var loadThis = Instruction.Create(OpCodes.Ldarg_0);
                var loadField = Instruction.Create(OpCodes.Ldfld, field);
                il.InsertAfter(cursor, loadThis);
                il.InsertAfter(loadThis, loadField);
                cursor = loadField;
            }
            var call = Instruction.Create(OpCodes.Call, refresh);
            il.InsertAfter(cursor, call);
            il.InsertAfter(call, Instruction.Create(OpCodes.Ret));
        }
    }

    static void InjectSkirmishFleetCap(ModuleDefinition module, TypeDefinition helper)
    {
        var mpDataTables = module.Types.Single(t => t.Name == "MPDataTables");
        var include32k = helper.Methods.Single(m => m.Name == "Include32k" && m.Parameters.Count == 1);
        foreach (var name in new[] { "GetFleetPointOptionsPvP", "GetFleetPointOptionsCoOp" })
        {
            var getter = mpDataTables.Methods.Single(m => m.Name == name && m.Parameters.Count == 0);
            if (getter.Body.Instructions.Any(i => i.OpCode == OpCodes.Call && i.Operand is MethodReference mr &&
                                                   mr.DeclaringType.FullName == helper.FullName && mr.Name == include32k.Name))
                continue;
            var returns = getter.Body.Instructions.Where(i => i.OpCode == OpCodes.Ret).ToList();
            if (returns.Count != 1)
                throw new InvalidOperationException("unexpected return layout in MPDataTables." + name);
            getter.Body.GetILProcessor().InsertBefore(returns[0], Instruction.Create(OpCodes.Call, include32k));
            getter.Body.MaxStackSize = Math.Max(getter.Body.MaxStackSize, 1);
        }
        Console.WriteLine("Added 32,000 fleet-cap choice to skirmish PvP and co-op option lists");
    }

    static void InjectCommanderStatModifierHooks(ModuleDefinition module, TypeDefinition helper)
    {
        var commander = module.Types.Single(t => t.Name == "FleetCommander");
        var apply = helper.Methods.Single(m => m.Name == "Apply" && m.Parameters.Count == 3);
        var methods = new[] {
            new { Name = "CalculateFlagshipPercentageBonus", Count = 2 },
            new { Name = "CalculateFlagshipFighterPercentageBonus", Count = 2 },
            new { Name = "CalculateFlagshipFixedBonus", Count = 3 },
            new { Name = "CalculateFleetFixedBonus", Count = 2 },
            new { Name = "LHDBHIDIKNA", Count = 2 }
        };
        foreach (var spec in methods)
        {
            var method = commander.Methods.Single(m => m.Name == spec.Name && m.Parameters.Count == spec.Count);
            if (method.Body.Instructions.Any(i => i.OpCode == OpCodes.Call && i.Operand is MethodReference mr &&
                                                  mr.DeclaringType.FullName == helper.FullName && mr.Name == apply.Name))
                continue;
            var result = new VariableDefinition(module.TypeSystem.Single);
            method.Body.Variables.Add(result);
            method.Body.InitLocals = true;
            var il = method.Body.GetILProcessor();
            foreach (var ret in method.Body.Instructions.Where(i => i.OpCode == OpCodes.Ret).ToArray())
            {
                // Re-purpose the return instruction itself so any existing branch to it
                // still flows through the modifier.
                ret.OpCode = OpCodes.Stloc;
                ret.Operand = result;
                Instruction cursor = ret;
                foreach (var instruction in new[] {
                    Instruction.Create(OpCodes.Ldarg_0),
                    Instruction.Create(OpCodes.Ldarg_1),
                    Instruction.Create(OpCodes.Ldloc, result),
                    Instruction.Create(OpCodes.Call, apply),
                    Instruction.Create(OpCodes.Ret)
                }) {
                    il.InsertAfter(cursor, instruction);
                    cursor = instruction;
                }
            }
            method.Body.MaxStackSize = Math.Max(method.Body.MaxStackSize, 3);
        }
        Console.WriteLine("Injected custom commander stat modifiers into native commander bonus calculations");
    }

    static void InjectModuleSubsystemHooks(ModuleDefinition module, TypeDefinition helper)
    {
        // Stock modules read one subsystem for their rate. Every call to that getter is wrapped so the ship's
        // CustomModuleSubsystems entries (if any) replace the result:
        //   RegenerativeHullResinModule: rate = Engineering.GetHullRepairRate(hullPerTick)
        //        -> CustomModuleSubsystems.HullResin(rate, this, this.hullPerTick)
        //   MunitionFactoryModule: rate = Armory.GetMunitionResupplyRate()
        //        -> CustomModuleSubsystems.MunitionFactory(rate, this)
        // Only real-name calls are wrapped; the obfuscator's decoy copies call other names and never run.
        var hullHook = helper.Methods.Single(m => m.Name == "HullResin" && m.Parameters.Count == 3);
        var munitionHook = helper.Methods.Single(m => m.Name == "MunitionFactory" && m.Parameters.Count == 2);
        int wrapped = 0;
        foreach (var target in new[] { "RegenerativeHullResinModule", "MunitionFactoryModule" })
        {
            var type = module.Types.Single(t => t.Name == target);
            bool resin = target == "RegenerativeHullResinModule";
            var hook = resin ? hullHook : munitionHook;
            var hullField = resin ? type.Fields.Single(f => f.Name == "hullPerTick") : null;
            foreach (var method in type.Methods.Where(m => m.HasBody))
            {
                var calls = method.Body.Instructions.Where(i => (i.OpCode == OpCodes.Call || i.OpCode == OpCodes.Callvirt) &&
                    i.Operand is MethodReference mr && (resin
                        ? mr.Name == "GetHullRepairRate" && mr.DeclaringType.Name == "Engineering"
                        : mr.Name == "GetMunitionResupplyRate" && mr.DeclaringType.Name == "Armory")).ToList();
                if (calls.Count == 0) continue;
                if (method.Body.Instructions.Any(i => i.OpCode == OpCodes.Call && i.Operand is MethodReference hr &&
                                                      hr.DeclaringType.FullName == helper.FullName && hr.Name == hook.Name))
                    continue;                                   // already wrapped (rebuilding an updated DLL)
                var il = method.Body.GetILProcessor();
                foreach (var call in calls)
                {
                    Instruction cursor = call;
                    var added = resin
                        ? new[] { Instruction.Create(OpCodes.Ldarg_0), Instruction.Create(OpCodes.Ldarg_0),
                                  Instruction.Create(OpCodes.Ldfld, hullField), Instruction.Create(OpCodes.Call, hook) }
                        : new[] { Instruction.Create(OpCodes.Ldarg_0), Instruction.Create(OpCodes.Call, hook) };
                    foreach (var instruction in added) { il.InsertAfter(cursor, instruction); cursor = instruction; }
                    wrapped++;
                }
                method.Body.MaxStackSize = Math.Max(method.Body.MaxStackSize, 4);
            }
        }
        Console.WriteLine("Injected module subsystem drivers into Hull Resin / Munition Factory (" + wrapped + " call(s))");
    }

    static void InjectSubsystemModifierHooks(ModuleDefinition module, TypeDefinition helper)
    {
        // ShipDataTable.GetCrewVeterancyBonus(ship, stat, battle) is the funnel for every
        // crew-veterancy consumer (percentage, fixed and the SubSystemBase tooltip helper).
        // Each return becomes: result -> CustomSubsystemModifiers.Apply(ship, stat, result).
        var table = module.Types.Single(t => t.Name == "ShipDataTable");
        var apply = helper.Methods.Single(m => m.Name == "Apply" && m.Parameters.Count == 3);
        var method = table.Methods.Single(m => m.Name == "GetCrewVeterancyBonus" && m.Parameters.Count == 3 &&
            m.Parameters[0].ParameterType.Name == "ShipInfo" && m.Parameters[1].ParameterType.Name == "FEDNAJLFKEF" &&
            m.ReturnType.FullName == "System.Single");
        if (method.Body.Instructions.Any(i => i.OpCode == OpCodes.Call && i.Operand is MethodReference mr &&
                                              mr.DeclaringType.FullName == helper.FullName && mr.Name == apply.Name))
            return;
        var result = new VariableDefinition(module.TypeSystem.Single);
        method.Body.Variables.Add(result);
        method.Body.InitLocals = true;
        var il = method.Body.GetILProcessor();
        var returns = method.Body.Instructions.Where(i => i.OpCode == OpCodes.Ret).ToArray();
        if (returns.Length == 0) throw new InvalidOperationException("GetCrewVeterancyBonus has no return site");
        foreach (var ret in returns)
        {
            // Re-purpose the return itself so branches aimed at it still pass the hook.
            ret.OpCode = OpCodes.Stloc;
            ret.Operand = result;
            Instruction cursor = ret;
            foreach (var instruction in new[] {
                Instruction.Create(OpCodes.Ldarg_1),
                Instruction.Create(OpCodes.Ldarg_2),
                Instruction.Create(OpCodes.Ldloc, result),
                Instruction.Create(OpCodes.Call, apply),
                Instruction.Create(OpCodes.Ret)
            }) {
                il.InsertAfter(cursor, instruction);
                cursor = instruction;
            }
        }
        method.Body.MaxStackSize = Math.Max(method.Body.MaxStackSize, 3);
        Console.WriteLine("Injected subsystem crew modifiers into ShipDataTable.GetCrewVeterancyBonus (" + returns.Length + " returns)");
    }

    static void ReplaceCommanderRankLookup(TypeDefinition tree, TypeDefinition helper, FieldDefinition ranks,
                                           MethodReference getRank, MethodDefinition method, int abilityLocal,
                                           int indexLocal, bool readAbilityFromChoiceArray)
    {
        if (method.Body.Instructions.Any(i => i.OpCode == OpCodes.Call && i.Operand is MethodReference mr &&
            mr.DeclaringType.FullName == helper.FullName && mr.Name == getRank.Name)) return;

        var fields = method.Body.Instructions.Where(i => i.OpCode == OpCodes.Ldsfld && i.Operand is FieldReference fr &&
            fr.Name == ranks.Name && fr.DeclaringType.FullName == tree.FullName).ToList();
        if (fields.Count != 1) throw new InvalidOperationException("unexpected number of ability level lookups in " + method.FullName);
        var fieldLoad = fields[0]; var indexLoad = fieldLoad.Next; var arrayRead = indexLoad == null ? null : indexLoad.Next;
        if (indexLoad == null || arrayRead == null || arrayRead.OpCode != OpCodes.Ldelem_I4)
            throw new InvalidOperationException("unexpected ability level lookup IL in " + method.FullName);
        var il = method.Body.GetILProcessor();
        var insert = new List<Instruction>();
        if (readAbilityFromChoiceArray)
        {
            // DKAFHNBPEMM locals 5 and 10 hold the choices array and its current index.
            insert.Add(Instruction.Create(OpCodes.Ldloc, method.Body.Variables[5]));
            insert.Add(Instruction.Create(OpCodes.Ldloc, method.Body.Variables[10]));
            insert.Add(Instruction.Create(OpCodes.Ldelem_I4));
            insert.Add(Instruction.Create(OpCodes.Ldloc, method.Body.Variables[10]));
        }
        else
        {
            insert.Add(Instruction.Create(OpCodes.Ldloc, method.Body.Variables[abilityLocal]));
            insert.Add(Instruction.Create(OpCodes.Ldloc, method.Body.Variables[indexLocal]));
        }
        insert.Add(Instruction.Create(OpCodes.Ldsfld, ranks));
        insert.Add(Instruction.Create(OpCodes.Call, getRank));
        foreach (var instruction in insert) il.InsertBefore(fieldLoad, instruction);
        fieldLoad.OpCode = OpCodes.Nop; fieldLoad.Operand = null;
        indexLoad.OpCode = OpCodes.Nop; indexLoad.Operand = null;
        arrayRead.OpCode = OpCodes.Nop; arrayRead.Operand = null;
        method.Body.MaxStackSize = Math.Max(method.Body.MaxStackSize, 4);
    }

    static void FixCommanderNodeLoop(MethodDefinition method)
    {
        var instructions = method.Body.Instructions;
        var choices = instructions.Single(i => i.OpCode == OpCodes.Ldfld &&
            i.Operand is FieldReference field && field.Name == "Specialisations" &&
            field.DeclaringType.Name == "CommanderSpecialisationBySubSystem");
        var enter = choices.Next.Next.Next.Next;
        var firstNode = enter.Next;
        var repeat = instructions.Last().Previous.Previous;
        var condition = repeat.Previous.Previous.Previous.Previous;
        if ((enter.OpCode != OpCodes.Br_S && enter.OpCode != OpCodes.Br) ||
            firstNode.OpCode != OpCodes.Newobj ||
            (repeat.OpCode != OpCodes.Blt_S && repeat.OpCode != OpCodes.Blt) ||
            repeat.Previous.OpCode != OpCodes.Conv_I4 ||
            repeat.Previous.Previous.OpCode != OpCodes.Ldlen)
            throw new InvalidOperationException("unexpected commander node loop IL in " + method.FullName);

        // The longer rank lookup can exceed an 8-bit branch in either direction.
        // Rebuild both targets from the loop structure to repair existing DLLs too.
        enter.OpCode = OpCodes.Br;
        enter.Operand = condition;
        repeat.OpCode = OpCodes.Blt;
        repeat.Operand = firstNode;
    }

    static void SetComponentBoostLimit(Instruction instruction, int limit, OpCode original)
    {
        if (instruction.OpCode != original &&
            (instruction.OpCode != OpCodes.Ldc_I4_S || (sbyte)instruction.Operand != limit))
            throw new InvalidOperationException("unexpected ship component boost limit");
        instruction.OpCode = OpCodes.Ldc_I4_S;
        instruction.Operand = (sbyte)limit;
    }

    static void ExpandComponentBoost(ModuleDefinition module, TypeDefinition helper,
        TypeDefinition tree, MethodDefinition nodes)
    {
        int limit = (int)helper.Fields.Single(f => f.Name == "ComponentBoostLevels").Constant;
        var rankLoad = nodes.Body.Instructions.Single(i => i.OpCode == OpCodes.Ldfld &&
            i.Operand is FieldReference field && field.Name == "FleetSpecialisationRank");
        var loopEnd = nodes.Body.Instructions.Single(i => i.OpCode == OpCodes.Ble_S &&
            i.Offset > rankLoad.Offset);
        SetComponentBoostLimit(loopEnd.Previous, limit, OpCodes.Ldc_I4_5);

        var offers = tree.Methods.Single(m => m.Name == "NJDJOFENMNG" && m.Parameters.Count == 2);
        var offerLimit = offers.Body.Instructions.First(i => i.OpCode == OpCodes.Bgt_S).Previous;
        SetComponentBoostLimit(offerLimit, limit, OpCodes.Ldc_I4_5);

        var commander = module.Types.Single(t => t.Name == "FleetCommander");
        var apply = commander.Methods.Single(m => m.Name == "ApplyFleetGroupSpecialisation");
        var clamp = apply.Body.Instructions.Single(i => i.OpCode == OpCodes.Call &&
            i.Operand is MethodReference call && call.Name == "Clamp" &&
            call.DeclaringType.FullName == "UnityEngine.Mathf");
        SetComponentBoostLimit(clamp.Previous, limit, OpCodes.Ldc_I4_5);
    }

    static void InjectCommanderAbilityLevelHooks(ModuleDefinition module, TypeDefinition helper)
    {
        var tree = module.Types.Single(t => t.Name == "OPHEFGLELFC");
        var ranks = tree.Fields.Single(f => f.Name == "DCJDOFBHDJA");
        var getRank = helper.Methods.Single(m => m.Name == "GetUnlockRank" && m.Parameters.Count == 3);
        var offers = tree.Methods.Single(m => m.Name == "JABBOLKFKOL" && m.Parameters.Count == 2);
        ReplaceCommanderRankLookup(tree, helper, ranks, getRank, offers, 4, 3, false);
        var nodes = tree.Methods.Single(m => m.Name == "DKAFHNBPEMM" && m.Parameters.Count == 2);
        FixCommanderNodeLoop(nodes);
        ExpandComponentBoost(module, helper, tree, nodes);
        ReplaceCommanderRankLookup(tree, helper, ranks, getRank, nodes, 0, 0, true);
        var widget = module.Types.Single(t => t.Name == "SkillSelectWidget");
        var findSkill = helper.Methods.Single(m => m.Name == "FindCaptainSkill");
        var lookups = widget.Methods.Where(m => m.HasBody).SelectMany(m => m.Body.Instructions)
            .Where(i => (i.OpCode == OpCodes.Ldelem_Any &&
                i.Operand is TypeReference type && type.FullName == "CaptainSkill") ||
                (i.OpCode == OpCodes.Call && i.Operand is MethodReference call &&
                call.DeclaringType.FullName == helper.FullName && call.Name == findSkill.Name)).ToList();
        if (lookups.Count == 0) throw new InvalidOperationException("could not find the captain skill widget lookup");
        foreach (var lookup in lookups)
        {
            lookup.OpCode = OpCodes.Call;
            lookup.Operand = findSkill;
        }
        var panel = module.Types.Single(t => t.Name == "CommanderInfoPanel");
        var awake = panel.Methods.Single(m => m.Name == "Awake" && m.Parameters.Count == 0);
        var scroll = helper.Methods.Single(m => m.Name == "ConfigureSkillsScroll");
        if (!awake.Body.Instructions.Any(i => i.OpCode == OpCodes.Call &&
            i.Operand is MethodReference reference && reference.FullName == scroll.FullName))
        {
            var list = panel.Fields.Single(f => f.Name == "specialisationWidgetList");
            var ret = awake.Body.Instructions.Single(i => i.OpCode == OpCodes.Ret);
            var il = awake.Body.GetILProcessor();
            il.InsertBefore(ret, Instruction.Create(OpCodes.Ldarg_0));
            il.InsertBefore(ret, Instruction.Create(OpCodes.Ldfld, list));
            il.InsertBefore(ret, Instruction.Create(OpCodes.Call, scroll));
        }
        var promotion = module.Types.Single(t => t.Name == "PromotionSelectWidget");
        var promotionAwake = promotion.Methods.Single(m => m.Name == "Awake" && m.Parameters.Count == 0);
        var promotionScroll = helper.Methods.Single(m => m.Name == "ConfigurePromotionScroll");
        if (!promotionAwake.Body.Instructions.Any(i => i.OpCode == OpCodes.Call &&
            i.Operand is MethodReference reference && reference.FullName == promotionScroll.FullName))
        {
            var ret = promotionAwake.Body.Instructions.Single(i => i.OpCode == OpCodes.Ret);
            var il = promotionAwake.Body.GetILProcessor();
            il.InsertBefore(ret, Instruction.Create(OpCodes.Ldarg_0));
            il.InsertBefore(ret, Instruction.Create(OpCodes.Call, promotionScroll));
        }
        var academy = module.Types.Single(t => t.Name == "FleetGroupAcademyPanel");
        var academyAwake = academy.Methods.Single(m => m.Name == "Awake" && m.Parameters.Count == 0);
        var rosterScroll = helper.Methods.Single(m => m.Name == "ConfigureRosterScroll");
        if (!academyAwake.Body.Instructions.Any(i => i.OpCode == OpCodes.Call &&
            i.Operand is MethodReference reference && reference.FullName == rosterScroll.FullName))
        {
            var ret = academyAwake.Body.Instructions.Single(i => i.OpCode == OpCodes.Ret);
            var il = academyAwake.Body.GetILProcessor();
            il.InsertBefore(ret, Instruction.Create(OpCodes.Ldarg_0));
            il.InsertBefore(ret, Instruction.Create(OpCodes.Call, rosterScroll));
        }
        Console.WriteLine("Replaced fixed three-choice commander level lookups with per-ability unlock ranks");
    }

    static void Main(string[] args)
    {
        var resolver = new DefaultAssemblyResolver();
        resolver.AddSearchDirectory(args[3]);
        var rp = new ReaderParameters { InMemory = true, AssemblyResolver = resolver };
        var target = AssemblyDefinition.ReadAssembly(args[0], rp);
        var source = AssemblyDefinition.ReadAssembly(args[1], rp);
        if (args.Length < 5)
            throw new ArgumentException("usage: merge_custom_modules.exe TARGET SOURCE OUTPUT REFERENCES TYPE_NAME");
        var srcType = source.MainModule.Types.Single(t => t.Name == args[4]);
        var mod = target.MainModule;
        var destType = mod.Types.SingleOrDefault(t => t.FullName == srcType.FullName);
        bool updating = args.Length == 6 && args[5] == "--update";
        if (updating != (destType != null))
            throw new InvalidOperationException("unexpected update mode for " + srcType.FullName);
        if (!updating)
        {
            destType = new TypeDefinition(srcType.Namespace, srcType.Name, srcType.Attributes,
                ImportType(srcType.BaseType, mod, srcType, null));
            mod.Types.Add(destType);
        }

        foreach (var implementation in srcType.Interfaces)
        {
            if (!destType.Interfaces.Any(existing => existing.InterfaceType.FullName == implementation.InterfaceType.FullName))
                destType.Interfaces.Add(new InterfaceImplementation(ImportType(implementation.InterfaceType, mod, srcType, destType)));
        }

        foreach (var f in srcType.Fields)
        {
            if (destType.Fields.Any(field => field.Name == f.Name)) continue;
            var nf = new FieldDefinition(f.Name, f.Attributes, ImportType(f.FieldType, mod, srcType, destType));
            if (f.HasConstant) nf.Constant = f.Constant;
            CopyAttrs(f, nf, mod);
            destType.Fields.Add(nf);
        }
        foreach (var m in srcType.Methods)
        {
            string name = MethodName(m, srcType, mod);
            if (destType.Methods.Any(method => method.Name == name && method.Parameters.Count == m.Parameters.Count)) continue;
            var nm = new MethodDefinition(name, m.Attributes, ImportType(m.ReturnType, mod, srcType, destType));
            nm.ImplAttributes = m.ImplAttributes;
            foreach (var p in m.Parameters) nm.Parameters.Add(new ParameterDefinition(p.Name, p.Attributes, ImportType(p.ParameterType, mod, srcType, destType)));
            CopyAttrs(m, nm, mod);
            destType.Methods.Add(nm);
        }
        foreach (var m in srcType.Methods.Where(x => x.HasBody))
        {
            var dest = destType.Methods.Single(x => x.Name == MethodName(m, srcType, mod) && x.Parameters.Count == m.Parameters.Count);
            var body = new MethodBody(dest) { InitLocals = m.Body.InitLocals, MaxStackSize = m.Body.MaxStackSize };
            dest.Body = body;
            foreach (var v in m.Body.Variables) body.Variables.Add(new VariableDefinition(ImportType(v.VariableType, mod, srcType, destType)));
            var map = new Dictionary<Instruction, Instruction>();
            foreach (var i in m.Body.Instructions) { var ni = Instruction.Create(OpCodes.Nop); ni.OpCode = i.OpCode; map[i] = ni; body.Instructions.Add(ni); }
            for (int idx = 0; idx < m.Body.Instructions.Count; idx++)
            {
                var old = m.Body.Instructions[idx]; var ni = body.Instructions[idx]; var o = old.Operand;
                if (o is Instruction targetI) ni.Operand = map[targetI];
                else if (o is Instruction[] targets) ni.Operand = targets.Select(x => map[x]).ToArray();
                else if (o is TypeReference tr) ni.Operand = ImportType(tr, mod, srcType, destType);
                else if (o is MethodReference mr) ni.Operand = ImportMethod(mr, mod, srcType, destType);
                else if (o is FieldReference fr)
                {
                    if (fr.DeclaringType.FullName == srcType.FullName)
                        ni.Operand = destType.Fields.Single(f => f.Name == fr.Name);
                    else
                    {
                        var localType = FindType(mod, fr.DeclaringType.FullName);
                        var localField = localType == null ? null : localType.Fields.FirstOrDefault(f => f.Name == fr.Name);
                        ni.Operand = localField == null ? mod.ImportReference(fr) : localField;
                    }
                }
                else if (o is CallSite cs) { var c = new CallSite(ImportType(cs.ReturnType, mod, srcType, destType)); foreach (var p in cs.Parameters) c.Parameters.Add(new ParameterDefinition(ImportType(p.ParameterType, mod, srcType, destType))); ni.Operand = c; }
                else ni.Operand = o;
            }
            foreach (var h in m.Body.ExceptionHandlers)
                body.ExceptionHandlers.Add(new ExceptionHandler(h.HandlerType) { CatchType = h.CatchType == null ? null : ImportType(h.CatchType, mod, srcType, destType), TryStart=map[h.TryStart], TryEnd=h.TryEnd==null?null:map[h.TryEnd], HandlerStart=map[h.HandlerStart], HandlerEnd=h.HandlerEnd==null?null:map[h.HandlerEnd], FilterStart=h.FilterStart==null?null:map[h.FilterStart] });
        }
        if (srcType.Name == "CustomInstantPlatingAction" || srcType.Name == "CustomEmpPulseAction" || srcType.Name == "CustomTargetedEmpAction")
        {
            var actionBase = mod.Types.Single(t => t.Name == "ActionBase");
            destType.IsAbstract = false;
            destType.IsSealed = true;
            foreach (var property in srcType.Properties)
            {
                var parent = actionBase.Properties.Single(p => p.Name == property.Name).GetMethod;
                var method = destType.Methods.Single(m => m.Name == parent.Name);
                if (!method.Overrides.Any(o => o.FullName == parent.FullName)) method.Overrides.Add(parent);
            }
        }
        if (srcType.Name == "CustomMagazineCapacityModule")
            InjectMagazineCapacityHooks(mod, destType);
        if (srcType.Name == "CustomBattleHud" && !updating) InjectBattleHud(mod, destType);
        if (srcType.Name == "CustomRepairBaySlots" && !updating)
            InjectRepairBaySlotHooks(mod, destType);
        if (srcType.Name == "CustomSkirmishFleetCap" && !updating)
            InjectSkirmishFleetCap(mod, destType);
        if (srcType.Name == "CustomCommanderStatModifiers" && !updating)
            InjectCommanderStatModifierHooks(mod, destType);
        if (srcType.Name == "CustomSubsystemModifiers")
            InjectSubsystemModifierHooks(mod, destType);
        if (srcType.Name == "CustomModuleSubsystems")
            InjectModuleSubsystemHooks(mod, destType);
        if (srcType.Name == "CustomCommanderAbilityLevels")
            InjectCommanderAbilityLevelHooks(mod, destType);
        target.Write(args[2]);
        Console.WriteLine("Merged " + destType.FullName + " into " + args[2]);
    }
}

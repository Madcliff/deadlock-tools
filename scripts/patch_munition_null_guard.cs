using System;
using System.Linq;
using Mono.Cecil;
using Mono.Cecil.Cil;

public static class PatchMunitionNullGuard
{
    static bool IsArg0(Instruction instruction)
    {
        return instruction != null && instruction.OpCode == OpCodes.Ldarg_0;
    }

    static bool IsMunitionField(Instruction instruction)
    {
        var field = instruction == null ? null : instruction.Operand as FieldReference;
        return instruction != null && instruction.OpCode == OpCodes.Ldfld &&
               field != null && field.Name == "ActiveMunitions";
    }

    static bool GuardExists(Instruction start, Instruction rebuild)
    {
        var branch = start.Previous;
        var field = branch == null ? null : branch.Previous;
        var arg = field == null ? null : field.Previous;
        return branch != null && (branch.OpCode == OpCodes.Brfalse || branch.OpCode == OpCodes.Brfalse_S) &&
               branch.Operand == rebuild && IsMunitionField(field) && IsArg0(arg);
    }

    static void Main(string[] args)
    {
        if (args.Length < 2 || args.Length > 3)
            throw new ArgumentException("usage: patch_munition_null_guard INPUT_DLL OUTPUT_DLL [--check]");
        bool check = args.Length == 3 && args[2] == "--check";
        var resolver = new DefaultAssemblyResolver();
        resolver.AddSearchDirectory(System.IO.Path.GetDirectoryName(System.IO.Path.GetFullPath(args[0])));
        var managedDirectory = Environment.GetEnvironmentVariable("BSG_MANAGED_DIR");
        if (!String.IsNullOrEmpty(managedDirectory)) resolver.AddSearchDirectory(managedDirectory);
        var assembly = AssemblyDefinition.ReadAssembly(args[0], new ReaderParameters { InMemory = true, AssemblyResolver = resolver });
        var ship = assembly.MainModule.Types.Single(t => t.Name == "FleetShip");
        var method = ship.Methods.Single(m => m.Name == "PEDBAHFNIAC" && m.Parameters.Count == 6);
        var body = method.Body;
        var code = body.Instructions;
        foreach (var start in code.ToList())
        {
            if (!IsArg0(start) || !IsMunitionField(start.Next) || start.Next.Next.OpCode != OpCodes.Ldlen)
                continue;

            Instruction compare = start.Next.Next;
            while (compare != null && compare.Offset - start.Offset < 40 &&
                   compare.OpCode != OpCodes.Bge_S && compare.OpCode != OpCodes.Bge)
                compare = compare.Next;
            if (compare == null || compare.Next == null)
                throw new InvalidOperationException("could not find the munition-slot rebuild branch in FleetShip.PEDBAHFNIAC");

            var rebuild = compare.Next;
            if (GuardExists(start, rebuild))
            {
                Console.WriteLine("Munition list null guard is already present");
                return;
            }
            if (check)
            {
                Console.WriteLine("Munition list null guard is missing");
                return;
            }

            var il = body.GetILProcessor();
            il.InsertBefore(start, Instruction.Create(OpCodes.Ldarg_0));
            il.InsertBefore(start, Instruction.Create(OpCodes.Ldfld, (FieldReference)start.Next.Operand));
            il.InsertBefore(start, Instruction.Create(OpCodes.Brfalse_S, rebuild));
            body.MaxStackSize = Math.Max(body.MaxStackSize, 2);
            assembly.Write(args[1]);
            Console.WriteLine("Added a null guard before the saved munition-list length check");
            return;
        }
        throw new InvalidOperationException("could not find the patched ActiveMunitions length check in FleetShip.PEDBAHFNIAC");
    }
}

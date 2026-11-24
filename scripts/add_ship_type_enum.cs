using System;
using System.Linq;
using Mono.Cecil;

public static class AddShipTypeEnum
{
    static void Main(string[] args)
    {
        if (args.Length != 4)
            throw new ArgumentException("usage: add_ship_type_enum INPUT_DLL OUTPUT_DLL SHIP_ID ENUM_NAME");

        int shipId = Int32.Parse(args[2]);
        string enumName = args[3];
        var resolver = new DefaultAssemblyResolver();
        resolver.AddSearchDirectory(System.IO.Path.GetDirectoryName(System.IO.Path.GetFullPath(args[0])));
        string managed = Environment.GetEnvironmentVariable("BSG_MANAGED_DIR");
        if (!String.IsNullOrEmpty(managed)) resolver.AddSearchDirectory(managed);
        var assembly = AssemblyDefinition.ReadAssembly(args[0], new ReaderParameters {
            InMemory = true,
            AssemblyResolver = resolver
        });

        var shipTypes = assembly.MainModule.Types.Where(t => t.IsEnum && t.Fields.Any(f =>
            f.Name == "ColonialBattlestarMercury" && f.HasConstant && Convert.ToInt32(f.Constant) == 29));
        var shipType = shipTypes.Single();
        var sameValue = shipType.Fields.FirstOrDefault(f => f.HasConstant && Convert.ToInt32(f.Constant) == shipId);
        if (sameValue != null)
        {
            if (sameValue.Name != enumName)
                throw new InvalidOperationException("ship ID " + shipId + " is already assigned to enum member " + sameValue.Name);
            System.IO.File.Copy(args[0], args[1], true);
            Console.WriteLine("Ship type enum already contains " + enumName + "=" + shipId);
            return;
        }
        if (shipType.Fields.Any(f => f.Name == enumName))
            throw new InvalidOperationException("enum member " + enumName + " already exists with another value");

        var field = new FieldDefinition(enumName,
            FieldAttributes.Public | FieldAttributes.Static | FieldAttributes.Literal | FieldAttributes.HasDefault,
            assembly.MainModule.TypeSystem.Int32) { Constant = shipId };
        shipType.Fields.Add(field);
        assembly.Write(args[1]);
        Console.WriteLine("Added ship type enum member " + enumName + "=" + shipId);
    }
}

using System;
using System.Collections.Generic;
using System.Linq;
using Mono.Cecil;

// Adds ship IDs to the game's ShipType enum. Fleet placement iterates this
// enum to build its collision-radius map, so every ship row in the level
// tables needs a member here.
//
// usage: add_ship_type_enum INPUT_DLL OUTPUT_DLL ID=NAME [ID=NAME ...]
//        add_ship_type_enum INPUT_DLL OUTPUT_DLL SHIP_ID ENUM_NAME   (older form)
public static class AddShipTypeEnum
{
    static void Main(string[] args)
    {
        if (args.Length < 3)
            throw new ArgumentException("usage: add_ship_type_enum INPUT_DLL OUTPUT_DLL ID=NAME [ID=NAME ...]");

        var wanted = new List<KeyValuePair<int, string>>();
        if (args.Length == 4 && !args[2].Contains("=") && !args[3].Contains("="))
            wanted.Add(new KeyValuePair<int, string>(Int32.Parse(args[2]), args[3]));
        else
            foreach (var spec in args.Skip(2))
            {
                int split = spec.IndexOf('=');
                if (split <= 0 || split == spec.Length - 1)
                    throw new ArgumentException("expected ID=NAME, got " + spec);
                wanted.Add(new KeyValuePair<int, string>(Int32.Parse(spec.Substring(0, split)), spec.Substring(split + 1)));
            }

        var resolver = new DefaultAssemblyResolver();
        resolver.AddSearchDirectory(System.IO.Path.GetDirectoryName(System.IO.Path.GetFullPath(args[0])));
        string managed = Environment.GetEnvironmentVariable("BSG_MANAGED_DIR");
        if (!String.IsNullOrEmpty(managed)) resolver.AddSearchDirectory(managed);
        var assembly = AssemblyDefinition.ReadAssembly(args[0], new ReaderParameters {
            InMemory = true,
            AssemblyResolver = resolver
        });

        var shipType = assembly.MainModule.Types.Single(t => t.IsEnum && t.Fields.Any(f =>
            f.Name == "ColonialBattlestarMercury" && f.HasConstant && Convert.ToInt32(f.Constant) == 29));
        int added = 0;
        foreach (var item in wanted)
        {
            int shipId = item.Key;
            string enumName = item.Value;
            var sameValue = shipType.Fields.FirstOrDefault(f => f.HasConstant && Convert.ToInt32(f.Constant) == shipId);
            if (sameValue != null)
            {
                if (sameValue.Name != enumName)
                    throw new InvalidOperationException("ship ID " + shipId + " is already assigned to enum member " + sameValue.Name);
                Console.WriteLine("Ship type enum already contains " + enumName + "=" + shipId);
                continue;
            }
            if (shipType.Fields.Any(f => f.Name == enumName))
                throw new InvalidOperationException("enum member " + enumName + " already exists with another value");

            shipType.Fields.Add(new FieldDefinition(enumName,
                FieldAttributes.Public | FieldAttributes.Static | FieldAttributes.Literal | FieldAttributes.HasDefault,
                assembly.MainModule.TypeSystem.Int32) { Constant = shipId });
            Console.WriteLine("Added ship type enum member " + enumName + "=" + shipId);
            added++;
        }
        if (added == 0)
            System.IO.File.Copy(args[0], args[1], true);
        else
            assembly.Write(args[1]);
    }
}

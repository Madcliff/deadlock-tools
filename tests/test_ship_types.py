"""ShipType enum preservation: a DLL rebuilt from a clean base must keep the ship types of copied ships.

Regression for 2026-11-23: the custom-module rebuild started from a base DLL without ship type 206
(Colonial Mercury MK2), so adding that ship to a Skirmish fleet threw KeyNotFoundException again.
The DLL tests need Assembly-CSharp.dll (DEADLOCK_GAME_DLL, or the dll path in deadlock.json) and,
for the round trip, mono + mcs + Mono.Cecil; they are skipped otherwise.
"""
import os, shutil, subprocess, tempfile, unittest

from deadlock_tools import config, ship_types as stypes

CECIL = "/usr/lib/mono/gac/Mono.Cecil/0.11.1.0__0738eb9f132ed756/Mono.Cecil.dll"


def game_dll():
    for path in (os.environ.get("DEADLOCK_GAME_DLL"), config.setting("dll", required=False)):
        if path and os.path.isfile(path): return path
    return None


DLL = game_dll()


class MissingMembers(unittest.TestCase):
    def test_restores_lost_members_under_old_names(self):
        current = {29: "ColonialBattlestarMercury", 205: "SagittaranFreighter"}
        previous = {**current, 206: "ColonialBattleshipMercuryMK2"}
        self.assertEqual(stypes.missing(current, previous, [29, 205, 206]), {206: "ColonialBattleshipMercuryMK2"})

    def test_level_rows_without_a_member_get_a_custom_name(self):
        self.assertEqual(stypes.missing({29: "ColonialBattlestarMercury"}, {}, [29, 207]), {207: "CustomShip207"})

    def test_nothing_to_add(self):
        current = {29: "ColonialBattlestarMercury", 206: "ColonialBattleshipMercuryMK2"}
        self.assertEqual(stypes.missing(current, current, [29, 206]), {})

    def test_name_clash_is_refused(self):
        with self.assertRaises(ValueError):
            stypes.missing({29: "ColonialBattlestarMercury", 1: "CustomShip207"}, {}, [207])


@unittest.skipUnless(DLL, "needs the game's Assembly-CSharp.dll")
class GameEnum(unittest.TestCase):
    def test_reads_ship_type_enum(self):
        found = stypes.members(DLL)
        self.assertEqual(found[29], "ColonialBattlestarMercury")
        self.assertGreaterEqual(len(found), 68)

    @unittest.skipUnless(shutil.which("mono") and shutil.which("mcs") and os.path.isfile(CECIL), "needs mono, mcs and Mono.Cecil")
    def test_round_trip_adds_and_is_idempotent(self):
        managed = os.path.dirname(DLL)
        if not os.path.isfile(os.path.join(managed, "UnityEngine.CoreModule.dll")):
            levels = config.setting("levels", required=False)
            managed = levels and os.path.join(levels, "Managed")
        if not managed or not os.path.isfile(os.path.join(managed, "UnityEngine.CoreModule.dll")):
            self.skipTest("needs the game's Managed folder")
        before = stypes.members(DLL)
        new_id = max(before) + 1
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "a.dll")
            r = stypes.add(managed, DLL, out, {new_id: "TestShip%d" % new_id}, tmp)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            after = stypes.members(out)
            self.assertEqual(after, {**before, new_id: "TestShip%d" % new_id})
            again = os.path.join(tmp, "b.dll")
            r = stypes.add(managed, out, again, {new_id: "TestShip%d" % new_id}, tmp)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            with open(out, "rb") as a, open(again, "rb") as b:
                self.assertEqual(a.read(), b.read())


if __name__ == "__main__":
    unittest.main()

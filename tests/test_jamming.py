"""Capital-ship Jamming (the Cylon Wardriver's Jammer + JammingAction written into a ship bundle), on the vanilla Mercury.

Needs tests/regression_inputs.json with "dll" and "mercury_dec"; skipped otherwise.

    python -m unittest tests.test_jamming -v
"""
import json, os, subprocess, sys, tempfile, unittest

from deadlock_tools import weapons as W

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
INPUTS_FILE = os.path.join(HERE, "regression_inputs.json")
INPUTS = json.load(open(INPUTS_FILE)) if os.path.isfile(INPUTS_FILE) else {}
READY = INPUTS.get("mercury_dec") and os.path.isfile(INPUTS["mercury_dec"])


def components(path):
    """{class name: [(fields, owner GameObject name, type nodes)]} of the jamming scripts in a bundle"""
    b = W.read_bundle(path)
    off, size, *_ = b["nodes"][0]
    s = W.SerFile(b["data"][off:off + size])
    scripts = {o.pid: s.read(o)[0].get("m_ClassName") for o in s.objs if o.classid == 115}
    names = {o.pid: s.read(o)[0]["m_Name"] for o in s.objs if o.classid == 1}
    out = {"scripts": sorted(n for n in scripts.values() if n in ("Jammer", "JammingAction")), "objects": len(s.objs)}
    for o in s.objs:
        if o.classid == 114:
            fields, _ = s.read(o)
            cls = scripts.get(fields.get("m_Script", {}).get("m_PathID"))
            if cls in ("Jammer", "JammingAction"):
                out.setdefault(cls, []).append((fields, names[fields["m_GameObject"]["m_PathID"]], s.types[o.tidx].nodes))
    return out


class InstalledAbilityList(unittest.TestCase):
    def test_every_added_module_is_listed(self):
        """the Ships tab shows an ability as installed only when its component is in EDITABLE_ABILITY_MODULES"""
        import re
        source = open(W.__file__, encoding="utf-8").read()
        added = set(re.findall(r'add_custom_module_component\(\s*"([A-Za-z]+)"', source))
        modules = {name for name in added if not name.endswith("Action")}
        modules |= set(W.CUSTOM_MODULE_DEFINITIONS)
        modules |= {module for module, _action, _donor in W.SUPPORT_ABILITIES.values()}
        modules |= {"Jammer", "StealthModule", "MiniJumpAgent", "PredictiveAuguryModule",
                    "RegenerativeHullResinModule", "MunitionFactoryModule"}
        self.assertEqual(sorted(modules - set(W.EDITABLE_ABILITY_MODULES)), [])


@unittest.skipIf(not READY, "no tests/regression_inputs.json with mercury_dec")
class Jamming(unittest.TestCase):
    def run_tool(self, *args, ok=True):
        env = dict(os.environ, PYTHONPATH=ROOT)
        r = subprocess.run([sys.executable, "-m", "deadlock_tools", "weapons", *args], capture_output=True, text=True, cwd=ROOT, env=env, timeout=900)
        if ok:
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def test_add_update_and_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.run_tool(INPUTS["mercury_dec"], "-o", tmp, "--no-encrypt", "--jamming", "--jam-range", "800")
            first = os.path.join(tmp, "bundles", os.path.basename(INPUTS["mercury_dec"]))
            got = components(first)
            self.assertEqual(got["scripts"], ["Jammer", "JammingAction"])
            self.assertEqual(got["objects"], components(INPUTS["mercury_dec"])["objects"] + 4)   # 2 MonoScripts + 2 components
            (jammer, owner, nodes), = got["Jammer"]
            self.assertEqual(owner, "ColonialMercury")
            self.assertEqual((jammer["jamRange"], round(jammer["jamMissileChance"], 3), jammer["visualiseLayer"], jammer["assignToSelf"]),
                             (800.0, 0.6, "UI_3D", False))
            self.assertIn(("string", "visualiseLayer", -1, 0x8000), [(n.type, n.name, n.size, n.meta) for n in nodes])
            (action, menu, _nodes), = got["JammingAction"]
            self.assertEqual(menu, "Actions")
            # again on the result: settings update in place, nothing is added twice
            again = os.path.join(tmp, "again")
            self.run_tool(first, "-o", again, "--no-encrypt", "--jamming", "--jam-range", "600", "--jam-missile-chance", "0.9")
            got2 = components(os.path.join(again, "bundles", os.path.basename(first)))
            self.assertEqual(got2["objects"], got["objects"])
            self.assertEqual((got2["Jammer"][0][0]["jamRange"], round(got2["Jammer"][0][0]["jamMissileChance"], 3)), (600.0, 0.9))
            moved = os.path.join(tmp, "moved")
            self.run_tool(first, "-o", moved, "--no-encrypt", "--jamming", "--ability-submenu")
            self.assertIn("abilit", components(os.path.join(moved, "bundles", os.path.basename(first)))["JammingAction"][0][1].casefold())
            stats = W.bundle_ship_stats(first)
            self.assertEqual(stats["abilities"].get("Jammer"), "Jamming")         # shows as an installed ability
            self.assertEqual({f["key"]: f["value"] for f in stats["fields"]}.get("Jammer.jamRange"), 800.0)
            penalties = os.path.join(tmp, "penalties")
            self.run_tool(first, "-o", penalties, "--no-encrypt", "--jam-evade", "-25")
            from deadlock_tools import jamming as J
            ship = W.bundle_ship_type(first)
            self.assertEqual(J.read_overrides(os.path.join(penalties, "bundles", J.FILE)), {("ship", ship): {"evade": -25.0}})
            bad = self.run_tool(first, "-o", os.path.join(tmp, "bad"), "--no-encrypt", "--jamming", "--jam-missile-chance", "2", ok=False)
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn("fraction from 0 to 1", bad.stdout + bad.stderr)


if __name__ == "__main__":
    unittest.main()

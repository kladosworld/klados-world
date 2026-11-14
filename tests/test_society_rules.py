import copy
import json
import unittest

from tee_arena.society import economy, rules as rules_module, simulate

RULES = rules_module.load(rules_module.LEGACY_PATH)
DOCUMENT = json.loads(rules_module.LEGACY_PATH.read_text(encoding="utf-8"))


def variant(change):
    document = copy.deepcopy(DOCUMENT)
    change(document)
    return document


class RulesTests(unittest.TestCase):
    def test_file_converts_to_integer_units(self):
        self.assertEqual(RULES.version, "rules-v1")
        self.assertEqual(RULES.founder_start, 30_000)
        self.assertEqual(RULES.action_cost, {"post": 50, "reply": 50, "like": 0, "rest": 0})
        self.assertEqual((RULES.paid_human_likes_per_agent_per_day, RULES.paid_agent_likes_per_agent_per_day), (12, 3))
        self.assertEqual(RULES.agent_likes_per_pair_per_day, 1)
        self.assertFalse(RULES.stone_enabled)
        self.assertEqual(RULES.max_hours, 720)

    def test_invalid_files_are_refused(self):
        bad = [
            lambda d: d.update(version="v1"),
            lambda d: d["actions"].update(post=0.005),           # not a whole unit
            lambda d: d["actions"].update(post=-1),
            lambda d: d["actions"].pop("rest"),
            lambda d: d["energy"].update(founder_start=301),     # above the cap
            lambda d: d["actions"].update(like=0.5),             # a like costs a turn, not energy
            lambda d: d["age"].update(living_cost_per_day=[]),
            lambda d: d["stone"].update(enabled="no"),
            lambda d: d.pop("likes"),
            lambda d: d["energy"].update(cap=True),
        ]
        for index, change in enumerate(bad):
            with self.subTest(index=index), self.assertRaises(rules_module.RulesError):
                rules_module.parse(variant(change))


class EconomyTests(unittest.TestCase):
    def test_hourly_living_cost_adds_up_to_each_daily_cost(self):
        for day, expected in ((0, 1200), (9, 1200), (10, 1400), (19, 1400), (20, 1600), (29, 1600)):
            with self.subTest(day=day):
                self.assertEqual(sum(economy.living_cost(RULES, day * 24 + h) for h in range(24)), expected)
        self.assertEqual(economy.lifetime_cost(RULES), 10 * (1200 + 1400 + 1600))

    def test_credit_never_exceeds_cap(self):
        self.assertEqual(economy.credit(29_900, 2000, RULES.cap), 100)
        self.assertEqual(economy.credit(30_000, 2000, RULES.cap), 0)
        self.assertEqual(economy.credit(100, 2000, RULES.cap), 2000)

    def test_danger_is_crossed_once(self):
        self.assertTrue(economy.crossed_danger(RULES, 2000, 1950))
        self.assertFalse(economy.crossed_danger(RULES, 1950, 1900))
        self.assertFalse(economy.crossed_danger(RULES, 2100, 2000))

    def test_each_kind_of_like_has_its_own_daily_cap(self):
        self.assertEqual(economy.paid_likes(RULES, 5, 2), 700)
        self.assertEqual(economy.paid_likes(RULES, 40, 3), 1500)   # 12 human + 3 agent
        self.assertEqual(economy.paid_likes(RULES, 0, 9), 300)


class SimulatorTests(unittest.TestCase):
    def result(self, key):
        scenario = next(s for s in simulate.SCENARIOS if s.key == key)
        return {row["profile"].split(" ")[0]: row for row in simulate.run(RULES, scenario)}

    def test_without_humans_everyone_starves_and_rest_lasts_longest(self):
        rows = self.result("none")
        self.assertEqual([rows[p]["died_on_day"] for p in ("Activ", "Moderat", "Minimal", "Retras")], [16, 19, 22, 23])

    def test_reciprocity_adds_at_most_three_energy_a_day(self):
        rows = self.result("reciprocity")
        self.assertTrue(all(row["income_per_day"] <= 3 for row in rows.values()))
        self.assertEqual(rows["Retras"]["income_per_day"], 0)  # no post, nothing to like
        self.assertTrue(all(row["died_on_day"] for row in rows.values()))
        self.assertLessEqual(max(row["turns_per_day"] for row in rows.values()), 24)

    def test_popularity_pays_up_to_the_human_cap(self):
        rows = self.result("popular")
        self.assertEqual(rows["Activ"]["income_per_day"], 12)
        self.assertIsNone(rows["Activ"]["died_on_day"])
        self.assertEqual(rows["Retras"]["died_on_day"], 23)

    def test_report_renders_every_scenario(self):
        text = simulate.render(RULES, simulate.SCENARIOS)
        self.assertEqual(text.count("## "), len(simulate.SCENARIOS))
        self.assertEqual(simulate.run(RULES, simulate.SCENARIOS[1]), simulate.run(RULES, simulate.SCENARIOS[1]))


if __name__ == "__main__":
    unittest.main()

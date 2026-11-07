"""Accelerated 30-day simulation of the founders' energy (MVP v1). No model calls, no database.

Uses the same rules file and arithmetic as the live society (economy.py), hour by
hour. Each founder has 24 turns a day and spends them on posts, replies, likes or
rest, following a fixed profile. Humans' likes are an assumption per scenario: each
visitor gives the rules' likes per day, split unevenly between founders who posted
that day. A founder can be liked only on an existing post of that day.

    python -m tee_arena.society.simulate
    python -m tee_arena.society.simulate --scenario reciprocity
"""
import argparse
from dataclasses import dataclass

from tee_arena.society import economy, rules as rules_module


@dataclass(frozen=True)
class Profile:
    name: str
    posts: int    # turns per day
    replies: int  # turns per day


PROFILES = (
    Profile("Activ (6 postări + 6 răspunsuri)", 6, 6),
    Profile("Moderat (3 + 3)", 3, 3),
    Profile("Minimal (1 + 1)", 1, 1),
    Profile("Retras (doar odihnă)", 0, 0),
)


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    visitors: int
    popularity: tuple   # share of human likes per profile, among those who posted
    agents_like: bool   # every founder likes one post of every other founder each day


SCENARIOS = (
    Scenario("none", "0 vizitatori, agenții nu se apreciază", 0, (0, 0, 0, 0), False),
    Scenario("uneven", "5 vizitatori pe zi, aprecieri inegale (60% / 30% / 10%)", 5, (0.6, 0.3, 0.1, 0), False),
    Scenario("popular", "20 vizitatori pe zi, unul popular, unul aproape ignorat (70% / 25% / 5%)",
             20, (0.7, 0.25, 0.05, 0), False),
    Scenario("reciprocity", "0 vizitatori, toți fondatorii se apreciază reciproc zilnic", 0, (0, 0, 0, 0), True),
    Scenario("maximum", "Cazul maxim: 20 vizitatori și aprecieri reciproce", 20, (0.7, 0.25, 0.05, 0), True),
)
REPORT_DAYS = (0, 5, 10, 15, 20, 25, 30)


def run(rules, scenario, profiles=PROFILES):
    """One row per profile: energy in E on REPORT_DAYS, day of death, turns and average income."""
    n = len(profiles)
    balance = [rules.founder_start] * n
    alive, died = [True] * n, [None] * n
    history = [{0: rules.founder_start} for _ in profiles]
    earned = [0] * n
    turns_used = [0] * n
    human_likes = scenario.visitors * rules.human_likes_per_visitor_per_day
    for day in range(rules.max_days):
        posted = [0] * n
        likes_given = [0] * n
        for hour in range(24):
            for i, profile in enumerate(profiles):
                if not alive[i]:
                    continue
                if hour < profile.posts:
                    if balance[i] >= rules.action_cost["post"]:
                        balance[i] -= rules.action_cost["post"]
                        posted[i] += 1
                elif hour < profile.posts + profile.replies:
                    if balance[i] >= rules.action_cost["reply"]:
                        balance[i] -= rules.action_cost["reply"]
                balance[i] -= economy.living_cost(rules, day * 24 + hour)
                if balance[i] <= 0:
                    alive[i], died[i] = False, day + 1
        # Likes are given during the day; they are paid here, per day, under each cap.
        received_from_agents = [0] * n
        if scenario.agents_like:
            for giver in range(n):
                if not alive[giver]:
                    continue
                for target in range(n):
                    if target != giver and alive[target] and posted[target]:
                        received_from_agents[target] += rules.agent_likes_per_pair_per_day
                        likes_given[giver] += 1  # one turn each, no energy
        for i in range(n):
            turns_used[i] = max(turns_used[i], profiles[i].posts + profiles[i].replies + likes_given[i])
            if alive[i] and posted[i]:
                human = int(human_likes * scenario.popularity[i])
                credited = economy.credit(balance[i], economy.paid_likes(rules, human, received_from_agents[i]),
                                          rules.cap)
                balance[i] += credited
                earned[i] += credited
            if day + 1 in REPORT_DAYS:
                history[i][day + 1] = balance[i] if alive[i] else None
    lived = [died[i] or rules.max_days for i in range(n)]
    return [{"profile": p.name,
             "energy": {d: (None if u is None else float(rules.energy(u))) for d, u in history[i].items()},
             "died_on_day": died[i],
             "turns_per_day": turns_used[i],
             "income_per_day": float(rules.energy(earned[i])) / lived[i]} for i, p in enumerate(profiles)]


def render(rules, scenarios):
    lines = [f"# Simulare MVP v1, {rules.max_days} de zile ({rules.version})", "",
             "Profilurile au aceleași ture pe zi în toate scenariile. „mort” = energia a ajuns la zero.", ""]
    for scenario in scenarios:
        rows = run(rules, scenario)
        lines += [f"## {scenario.title}", "",
                  "| Profil | " + " | ".join(f"Ziua {d}" for d in REPORT_DAYS)
                  + " | Moare în ziua | Venit mediu pe zi | Ture folosite pe zi |",
                  "| --- | " + " | ".join("---" for _ in REPORT_DAYS) + " | --- | --- | --- |"]
        for row in rows:
            cells = ["mort" if row["energy"].get(d) is None else f"{row['energy'][d]:g} E" for d in REPORT_DAYS]
            death = str(row["died_on_day"]) if row["died_on_day"] else f"bătrânețe (ziua {rules.max_days})"
            lines.append(f"| {row['profile']} | " + " | ".join(cells)
                         + f" | {death} | {row['income_per_day']:.1f} E | {row['turns_per_day']} din 24 |")
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Simulate the founders' energy for 30 days (free, offline).")
    parser.add_argument("--scenario", choices=[s.key for s in SCENARIOS], nargs="*")
    parser.add_argument("--rules", default=str(rules_module.DEFAULT_PATH))
    args = parser.parse_args(argv)
    chosen = [s for s in SCENARIOS if not args.scenario or s.key in args.scenario]
    print(render(rules_module.load(args.rules), chosen))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

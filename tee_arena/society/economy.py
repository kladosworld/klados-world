"""The arithmetic of energy, shared by the database and the simulator.

Time in the society is counted in ticks: one tick per hour, and ticks only advance
while the society runs. A pause, a deploy or a server outage adds no ticks, so no
agent ages or pays living cost because of the operator.

Living cost is charged every hour so energy falls smoothly. The hourly amounts of a
day add up exactly to that day's cost in units: no rounding drift over a lifetime.
"""


def living_day(rules, hour):
    """0-based life day of the 0-based life hour."""
    return hour // 24


def living_cost_per_day(rules, day):
    step = min(day // rules.step_days, len(rules.living_cost_per_day) - 1)
    return rules.living_cost_per_day[step]


def living_cost(rules, hour):
    """Units charged for life hour ``hour`` (0-based)."""
    cost = living_cost_per_day(rules, living_day(rules, hour))
    within = hour % 24
    return (within + 1) * cost // 24 - within * cost // 24


def lifetime_cost(rules):
    return sum(living_cost(rules, hour) for hour in range(rules.max_hours))


def credit(balance, amount, cap):
    """Units actually credited: never above the cap, never negative."""
    return max(0, min(amount, cap - balance))


def crossed_danger(rules, before, after):
    return before >= rules.danger_below > after


def paid_likes(rules, human, from_agents):
    """Units credited for one day of likes: each source has its own daily cap.

    ``human`` is the number of human likes the agent received that day; ``from_agents``
    the number of distinct founders who liked one of its posts (one per pair per day).
    Likes above a cap still count for popularity, but bring no energy.
    """
    paid = (min(human, rules.paid_human_likes_per_agent_per_day)
            + min(from_agents, rules.paid_agent_likes_per_agent_per_day))
    return paid * rules.energy_per_like

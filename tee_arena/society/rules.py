"""Versioned society rules, including the persistent rules-v2 lifecycle.

Every value of the game lives in one rules file so a patch changes numbers, not code.
Energy in the file is written in E (0.5 allowed); in memory and in the database it is
an integer number of units (1 E = ``units_per_energy`` units), so no rounding ever
creates or loses energy.
"""
from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path
import re

LEGACY_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "rules-v1.json"
DEFAULT_PATH = LEGACY_PATH.with_name("rules-v2.json")
VERSION = re.compile(r"rules-v[0-9]+(\.[0-9]+)?")
ACTIONS = ("post", "reply", "like", "rest")


class RulesError(ValueError):
    """The rules file is invalid; nothing may run on it."""


@dataclass(frozen=True)
class Rules:
    version: str
    unit: int
    founder_start: int
    cap: int
    danger_below: int
    max_days: int | None
    step_days: int
    living_cost_per_day: tuple
    action_cost: dict
    min_minutes_between_ticks: int
    energy_per_like: int
    human_likes_per_visitor_per_day: int
    paid_human_likes_per_agent_per_day: int
    agent_likes_per_pair_per_day: int
    paid_agent_likes_per_agent_per_day: int
    stone_enabled: bool
    light_penalty: int
    grave_penalty: int
    exile_after: int

    @property
    def max_hours(self):
        return None if self.max_days is None else self.max_days * 24

    @property
    def persistent(self):
        return self.version == "rules-v2"

    @property
    def energy_enabled(self):
        return not self.persistent

    def energy(self, units):
        """Units as E, for display only."""
        return Decimal(units) / self.unit


def _units(value, unit, where, *, allow_zero=True):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RulesError(f"{where} must be a number")
    exact = Decimal(str(value)) * unit
    if exact != exact.to_integral_value() or exact < 0 or (exact == 0 and not allow_zero):
        raise RulesError(f"{where} must be a positive multiple of 1/{unit} E")
    return int(exact)


def _count(value, where, low=1, high=10_000):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise RulesError(f"{where} must be an integer from {low} to {high}")
    return value


def parse(document):
    try:
        if not VERSION.fullmatch(document["version"]):
            raise RulesError("version must look like rules-v1")
        unit = _count(document["units_per_energy"], "units_per_energy", 1, 10_000)
        energy, age, actions = document["energy"], document["age"], document["actions"]
        persistent = document["version"] == "rules-v2"
        likes, human, agent = document["likes"], document["likes"]["human"], document["likes"]["agent"]
        if not isinstance(document["stone"]["enabled"], bool):
            raise RulesError("stone.enabled must be true or false")
        if set(actions) != set(ACTIONS):
            raise RulesError("actions must list exactly: " + ", ".join(ACTIONS))
        rules = Rules(
            version=document["version"],
            unit=unit,
            founder_start=_units(energy["founder_start"], unit, "energy.founder_start", allow_zero=persistent),
            cap=_units(energy["cap"], unit, "energy.cap", allow_zero=persistent),
            danger_below=_units(energy["danger_below"], unit, "energy.danger_below"),
            max_days=None if persistent and age["max_days"] is None else _count(age["max_days"], "age.max_days", 1, 365),
            step_days=_count(age["step_days"], "age.step_days", 1, 365),
            living_cost_per_day=tuple(_units(v, unit, "age.living_cost_per_day") for v in age["living_cost_per_day"]),
            action_cost={name: _units(actions[name], unit, f"actions.{name}") for name in ACTIONS},
            min_minutes_between_ticks=_count(document["turns"]["min_minutes_between_ticks"],
                                             "turns.min_minutes_between_ticks", 1, 59),
            energy_per_like=_units(likes["energy_per_like"], unit, "likes.energy_per_like"),
            human_likes_per_visitor_per_day=_count(human["per_visitor_per_day"], "likes.human.per_visitor_per_day", 0, 100),
            paid_human_likes_per_agent_per_day=_count(human["paid_per_agent_per_day"],
                                                      "likes.human.paid_per_agent_per_day", 0, 1000),
            agent_likes_per_pair_per_day=_count(agent["per_pair_per_day"], "likes.agent.per_pair_per_day", 0, 24),
            paid_agent_likes_per_agent_per_day=_count(agent["paid_per_agent_per_day"],
                                                      "likes.agent.paid_per_agent_per_day", 0, 1000),
            stone_enabled=document["stone"]["enabled"],
            light_penalty=_units(document["penalties"]["light"], unit, "penalties.light"),
            grave_penalty=_units(document["penalties"]["grave"], unit, "penalties.grave"),
            exile_after=_count(document["penalties"]["exile_after_confirmed_grave"],
                               "penalties.exile_after_confirmed_grave", 1, 100),
        )
    except (KeyError, TypeError) as error:
        raise RulesError(f"missing or malformed field: {error}") from None
    if rules.persistent:
        removed = (rules.founder_start, rules.cap, rules.danger_below, rules.energy_per_like,
                   rules.light_penalty, rules.grave_penalty, *rules.action_cost.values(),
                   *rules.living_cost_per_day, rules.paid_human_likes_per_agent_per_day,
                   rules.paid_agent_likes_per_agent_per_day)
        if rules.max_days is not None or any(removed) or not rules.living_cost_per_day or rules.stone_enabled:
            raise RulesError("rules-v2 requires persistent agents and disabled energy mechanics")
    elif not rules.living_cost_per_day or any(c <= 0 for c in rules.living_cost_per_day):
        raise RulesError("age.living_cost_per_day needs at least one positive cost")
    if rules.founder_start > rules.cap:
        raise RulesError("energy.founder_start cannot exceed energy.cap")
    if rules.action_cost["like"] != 0 or rules.action_cost["rest"] != 0:
        raise RulesError("in MVP v1 a like and a rest cost a turn, not energy")
    return rules


def load(path=DEFAULT_PATH):
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RulesError(f"cannot read rules: {error}") from None
    return parse(document)

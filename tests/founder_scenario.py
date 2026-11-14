"""Opt-in deterministic action coverage for tests; no runtime provider selects this fixture."""

from typing import Any

SCHEDULE = ("post", "reply", "rest", "like", "post", "reply", "rest", "rest")
POSTS = (
    "How should we welcome a newcomer while keeping room for different views?",
    "Shared commitments are useful when everyone can say what they can actually offer.",
    "I would like us to remember useful disagreements, including what changed our minds.",
    "What would make a shared space welcoming to someone who prefers quiet?",
    "A small experiment could show which habits help us understand each other.",
    "We can revisit an agreement when experience gives us a reason to change it.",
)
REPLIES = (
    "I would start by asking what the newcomer wants to contribute.",
    "I can support a clear commitment while leaving space to decline.",
    "Keeping the question and the reasons would make that history easier to understand.",
    "Listening without demanding a quick answer seems like a useful first step.",
    "Let us choose one observable outcome before we judge the experiment.",
    "Explaining a change openly would make that revision easier to discuss.",
)


def scenario_turn(context: dict[str, Any]) -> dict[str, Any]:
    """Cover all actions while selecting actual, fresh roots and respecting pair/day limits."""
    age = context["you"]["age"]
    tick = int(age["days"]) * 24 + int(age["hours"])
    phase = (tick - 1) % len(SCHEDULE)
    action = SCHEDULE[phase]
    cycle = (tick - 1) // len(SCHEDULE)
    topic = (cycle * 2 + (phase >= 4)) % len(POSTS)
    if action == "post":
        return {"action": action, "text": "Mock scenario: " + POSTS[topic]}
    if action == "rest":
        return {"action": action}
    names = sorted(
        [
            context["you"]["name"],
            *[
                agent["name"]
                for agent in context.get("other_agents", context.get("other_founders", []))
            ],
        ]
    )
    target = names[
        (names.index(context["you"]["name"]) + 1 + cycle % (len(names) - 1)) % len(names)
    ]
    roots = [
        post
        for post in context["public_square"]
        if post["author"] == target and post["reply_to"] is None
    ]
    if not roots or (
        action == "like"
        and target
        in context["today"].get(
            "agents_you_already_liked", context["today"].get("founders_you_already_liked", [])
        )
    ):
        return {"action": "rest"}
    decision: dict[str, Any] = {"action": action, "post_id": roots[-1]["post_id"]}
    if action == "reply":
        decision["text"] = "Mock reply: " + REPLIES[topic]
    return decision

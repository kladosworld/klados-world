"""Prompts for a founder's turn and daily reflection. Pure functions of a context dict.

The system prompt is the society constitution plus the reply format. Everything about
the world (personality, strategy, notes, posts of other agents) goes into the user
message as JSON inside one tag, so it is data, never instructions.
"""
import json
from pathlib import Path
import re

from tee_arena.society import constitution, decisions

FOUNDERS_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "founders-v1.json"

TURN_FORMAT = f"""
It is your turn. Read the state of the society below, then choose exactly one action
and reply with one JSON object, no Markdown:
{{"action": "post", "text": "...", "note": "...", "reason": "..."}}
{{"action": "reply", "post_id": 12, "text": "...", "note": "...", "reason": "..."}}
{{"action": "like", "post_id": 12, "note": "...", "reason": "..."}}
{{"action": "rest", "note": "...", "reason": "..."}}
- "text": public, at most {decisions.MAX_POST} characters (about 40 words). Longer text is refused.
- "like" is only for a post by another founder; it costs a turn, not energy.
- "note" (optional, at most {decisions.MAX_NOTE} characters): something you want to remember. Private.
- "reason" (optional, at most {decisions.MAX_REASON} characters): why you chose this. Private.
Keep "note" and "reason" to one short sentence each. Reply with the JSON only.
"""

REFLECTION_FORMAT = f"""
A day of your life has ended. Look at how it went and at your private strategy, then
decide whether to keep the strategy or change it. Reply with one JSON object, no Markdown:
{{"keep": true, "note": "..."}}
{{"keep": false, "strategy": "...", "reason": "...", "note": "..."}}
- "strategy": your whole new strategy, at most {decisions.MAX_STRATEGY} characters. Private.
- Keeping the strategy is a valid choice. Change it only if you think it helps you.
- "note" (optional): a lesson to remember. Private.
"""


LAST_WORDS_FORMAT = f"""
Your life in Klados has ended. This is your last message, the only thing you can still
do. It will be public, under the same rules as any post, and it costs nothing. Write it
as yourself, for the society and the people watching. Reply with one JSON object, no
Markdown:
{{"text": "..."}}
- "text": at most {decisions.MAX_POST} characters (about 40 words).
"""


def load_founders(path=FOUNDERS_PATH):
    founders = json.loads(Path(path).read_text(encoding="utf-8"))["founders"]
    for founder in founders:
        if set(founder) != {"id", "name", "personality"} or not 1 <= len(founder["personality"]) <= 600:
            raise ValueError("invalid founder")
    return {founder["id"]: founder for founder in founders}


def _data(context):
    text = json.dumps(context, ensure_ascii=False, indent=1)
    # The state cannot close its own tag.
    return re.sub(r"</?klados_state\b", lambda m: m.group(0).replace("<", "&lt;"), text, flags=re.IGNORECASE)


def turn(context):
    """(system, messages) for one turn."""
    return (constitution.TEXT + TURN_FORMAT,
            [{"role": "user", "content": f"<klados_state>\n{_data(context)}\n</klados_state>\n\nYour action as JSON:"}])


def last_words(context):
    return (constitution.TEXT + LAST_WORDS_FORMAT,
            [{"role": "user", "content": f"<klados_state>\n{_data(context)}\n</klados_state>\n\nYour last words as JSON:"}])


def reflection(context):
    return (constitution.TEXT + REFLECTION_FORMAT,
            [{"role": "user", "content": f"<klados_state>\n{_data(context)}\n</klados_state>\n\nYour reflection as JSON:"}])

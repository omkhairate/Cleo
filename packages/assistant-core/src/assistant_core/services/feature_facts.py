"""Verified product facts for self-description; not generated capability claims."""

import re

PULSE_FACTS = (
    "Cleo Pulse keeps your goals, collects accessible browser links when research mode is enabled, "
    "and shows a local activity feed and quiet goal-check suggestions. Awareness is opt-in and can be paused. "
    "It does not measure your heart rate, detect emotions, or automatically execute suggestions."
)

CAPABILITY_FACTS = (
    "I can help with chat, selected text and captured screen context, use saved memory, and run supported app actions. "
    "Pulse adds saved goals, opt-in app awareness, research links and goal-check suggestions. "
    "App actions depend on the installed apps and permissions; drafting a message is not the same as sending it. "
    "I cannot control every app or device, and I only claim an action succeeded when its tool reports success."
)


def feature_answer(message: str) -> str | None:
    text = message.strip().lower()
    question = bool(re.search(r"\b(what|how|does|can|is|tell|explain)\b", text))
    if question and re.search(r"\b(?:your pulse|cleo(?:'s)? pulse|pulse thing\w*|pulse feature)\b", text):
        return PULSE_FACTS
    if re.fullmatch(r"(?:what can you do|what are your (?:features|capabilities)|what can cleo do)[?!. ]*", text):
        return CAPABILITY_FACTS
    return None

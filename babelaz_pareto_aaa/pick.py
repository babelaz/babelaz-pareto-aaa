"""Preset choice, the replies that start nothing, and the same-model notes. No Hermes.

A tuned preset is picked only on clear words for it; any other small game gets the house paper style, judged against
the brick anchors as quality levels only. Vague and app prompts get one reply and start no run.
"""

import re

BRICK = r"bricks?|breakout|arkanoid|block[\s-]*breakers?|paddle and ball"
CATCH = r"catch|catcher|catching|baskets?"
ANCHOR_SET = {"paper": "brick"}  # a preset with no anchors of its own borrows these (quality levels only)
FILLER = set("make me a an the some game games please build create fun cool simple small web browser something "
             "anything new for us i want can you".split())
APP = r"apps?|websites?|sites?|dashboards?|todos?|to-dos?|landing|forms?|calculators?|tools?|crm|blogs?|stores?|shops?"
GAME = r"games?|play|plays|player|players|levels?|arcade|scores?|puzzles?"
VAGUE = ("Which game? For example: /aaa make me a brick-breaker · /aaa make me snake · /aaa a falling-blocks puzzle. "
         "Add --refs <folder> (1-3 PNG pictures) for your own look.")
NOT_APP = "Pareto AAA builds small games only for now, not apps or sites. For example: /aaa make me snake."
PAPER_NOTE = ("No tuned style for this game yet: it uses the house paper style and the general quality bar "
              "(experimental). For your own look, add --refs <folder> with 1-3 PNG pictures.")
SAME_MODEL = ("One model does every job, so the second opinion at 8 is not independent. For a stronger 8, set "
              "auxiliary.aaa_confirm to a model from another family.")


def _has(words: str, text: str) -> bool:
    """A whole-word, case-insensitive match of any of the words (a regex alternation)."""
    return re.search(rf"(?<![a-z0-9])(?:{words})(?![a-z0-9])", text.lower()) is not None


def preset(text: str) -> str:
    """brick or catch only on clear words for them (brick wins a tie); every other game: paper."""
    return "brick" if _has(BRICK, text) else "catch" if _has(CATCH, text) else "paper"


def anchor_set(name: str) -> str:
    """The folder under assets/anchors/ that holds this preset's three anchor pictures."""
    return ANCHOR_SET.get(name, name)


def refuse(text: str) -> str | None:
    """The one reply for a vague or app prompt (nothing starts), else None. --refs and its folder are ignored."""
    text = re.sub(r"\s--refs(=\S*|\s+(\"[^\"]+\"|\S+))?", " ", " " + text)
    if not [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in FILLER]:
        return VAGUE
    if _has(APP, text) and not _has(GAME, text):
        return NOT_APP
    return None


def final_who(models: dict, confirmed: bool, status: str) -> str:
    """The end of the final report's "Best round" text: who judged, and, for an AAA result whose judge and second
    opinion were the same model, a new line saying so. The caller puts it after the stamped STATUS line, never in it."""
    models = models or {}
    j, c = models.get("judge"), models.get("confirm")
    who = f" Judged by {j}, second opinion by {c}." if confirmed else ""
    return who + (f"\nJudge and second opinion: the same model ({j})." if status == "AAA" and j and j == c else "")

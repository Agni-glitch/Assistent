"""Redoing tasks the user undid, and answering repeated questions from cache."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage

from conftest import FakeApprover, tool_call
from fox.core.answer_cache import is_cacheable, normalize
from fox.core.permissions import Decision
from fox.core.tooling import fox_tool
from fox.runtime import STALE_NOTE
from fox.server import create_app

opened: list[str] = []


@fox_tool(scope=lambda a: a["app"], describe=lambda a: f"Open {a['app']}")
def _open_thing(app: str) -> str:
    """Opens a test thing."""
    opened.append(app)
    return f"Opened {app}"


@pytest.fixture(autouse=True)
def clear_opened():
    opened.clear()


# --- Redoing a task ---


async def test_earlier_tool_results_are_marked_stale(make_fox):
    approver = FakeApprover(Decision.ONCE, Decision.ONCE)
    fox = make_fox(
        [
            tool_call("_open_thing", app="notepad"),
            AIMessage("Opened Notepad."),
            tool_call("_open_thing", "c2", app="notepad"),
            AIMessage("Opened Notepad again."),
        ],
        approver=approver,
    )
    first = await fox.chat("open notepad")
    await fox.chat("open notepad", first.session_id)

    assert opened == ["notepad", "notepad"]
    second_turn = fox.models[0].seen[2]
    old, new = [m for m in second_turn if isinstance(m, ToolMessage)], fox.models[0].seen[3][-1]
    assert old[0].content.startswith(STALE_NOTE)
    # The result from this turn is not marked, and the stored history keeps the original text.
    assert not new.content.startswith(STALE_NOTE)
    stored = [m for m in fox.store.load_messages(first.session_id) if isinstance(m, ToolMessage)]
    assert stored[0].content == "Opened notepad"


def test_system_prompt_says_to_redo_undone_tasks(make_fox):
    assert "do it again with your tools" in make_fox([])._system_prompt(False)


# --- Answer cache ---


async def test_repeated_question_is_answered_without_the_model(make_fox):
    fox = make_fox([AIMessage("DNS turns names into addresses.")])
    first = await fox.chat("What is DNS used for?")
    # The script is empty now, so reaching the model again would fail.
    again = await fox.chat("  what is dns USED for ")
    assert again.text == "DNS turns names into addresses."
    assert again.model == "cache"
    assert again.session_id != first.session_id
    assert [m.content for m in fox.store.load_messages(again.session_id)] == [
        "  what is dns USED for ",
        "DNS turns names into addresses.",
    ]


async def test_a_turn_that_used_a_tool_is_never_cached(make_fox):
    fox = make_fox(
        [
            tool_call("_open_thing", app="calculator"),
            AIMessage("Opened it."),
            tool_call("_open_thing", "c2", app="calculator"),
            AIMessage("Opened it."),
        ],
        approver=FakeApprover(Decision.ONCE, Decision.ONCE),
    )
    await fox.chat("please open the calculator")
    await fox.chat("please open the calculator")
    assert opened == ["calculator", "calculator"]


async def test_answers_given_mid_conversation_are_not_stored(make_fox):
    fox = make_fox([AIMessage("Hi."), AIMessage("Paris."), AIMessage("Paris, France.")])
    first = await fox.chat("hello")
    await fox.chat("what is the capital of france", first.session_id)
    again = await fox.chat("what is the capital of france")
    assert again.text == "Paris, France."


async def test_cache_can_be_turned_off(make_fox, monkeypatch):
    fox = make_fox([AIMessage("One."), AIMessage("Two.")])
    monkeypatch.setattr(fox.settings, "fox_cache_hours", 0)
    await fox.chat("explain how vaccines work")
    assert (await fox.chat("explain how vaccines work")).text == "Two."


async def test_changing_memory_misses_the_cache(make_fox):
    fox = make_fox([AIMessage("I don't know."), AIMessage("You like green.")])
    await fox.chat("what is my favourite colour")
    fox.store.add_fact("The user's favourite colour is green.")
    try:
        assert (await fox.chat("what is my favourite colour")).text == "You like green."
    finally:
        fox.store.clear_facts()


async def test_reset_clears_cached_answers(make_fox):
    fox = make_fox([AIMessage("Light bends.")])
    await fox.chat("why is the sky blue")
    assert fox.store.count_answers() == 1
    client = TestClient(create_app(fox, token="t"))
    reply = client.post("/api/reset", json={"scopes": ["cache"]}, headers={"Authorization": "Bearer t"})
    assert reply.json() == {"reset": ["cache"]}
    assert fox.store.count_answers() == 0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("What is DNS used for?", True),
        ("why?", False),
        ("do it again", False),
        ("tell me more about that", False),
        ("what's the weather in London", False),
        ("what time is it now", False),
    ],
)
def test_only_standalone_lasting_questions_are_cached(text, expected):
    assert is_cacheable(text) is expected


def test_normalize_ignores_case_spacing_and_end_punctuation():
    assert normalize("  What   is DNS?! ") == normalize("what is dns")

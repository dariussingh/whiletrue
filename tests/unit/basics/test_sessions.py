from whiletrue.basics import SessionEndConfig, context_reset_module, session_end_module
from whiletrue.core import EmptyConfig, Scope, create_step_context


def scope_with(sessions: dict[str, str] | None) -> Scope:
    scope = Scope()
    if sessions is not None:
        scope.set("sessions", sessions)
    return scope


async def test_session_end_closes_only_the_named_session() -> None:
    sessions = {"item": "a", "design": "b"}
    ctx = create_step_context(SessionEndConfig(), scope=scope_with(sessions))
    result = await session_end_module.run({"in": "payload"}, ctx)
    assert (result.port, result.value) == ("ok", "payload")
    assert sessions == {"design": "b"}
    ctx = create_step_context(SessionEndConfig(name="design"), scope=scope_with(sessions))
    await session_end_module.run({}, ctx)
    assert sessions == {}


async def test_ending_a_session_that_does_not_exist_is_fine() -> None:
    ctx = create_step_context(SessionEndConfig(), scope=scope_with({}))
    assert (await session_end_module.run({"in": 1}, ctx)).port == "ok"


async def test_context_reset_drops_every_shared_session() -> None:
    sessions = {"item": "a", "design": "b"}
    ctx = create_step_context(EmptyConfig(), scope=scope_with(sessions))
    result = await context_reset_module.run({"in": [1]}, ctx)
    assert (result.port, result.value) == ("ok", [1])
    assert sessions == {}


async def test_without_a_session_store_both_are_no_ops() -> None:
    ctx = create_step_context(EmptyConfig(), scope=scope_with(None))
    assert (await context_reset_module.run({}, ctx)).port == "ok"
    ctx2 = create_step_context(SessionEndConfig(), scope=scope_with(None))
    assert (await session_end_module.run({}, ctx2)).port == "ok"


def test_neither_module_is_cacheable_or_pure() -> None:
    for module in (session_end_module, context_reset_module):
        assert module.effects == "deterministic" and module.cacheable is False

import pytest

from whiletrue.core.expr import Expr, ExprError, parse_expr


def test_evaluates_booleans_and_comparisons() -> None:
    expr = parse_expr("gate.ok && iter.i < 5")
    assert expr.eval({"gate": {"ok": True}, "iter": {"i": 2}}) is True
    assert expr.eval({"gate": {"ok": False}, "iter": {"i": 2}}) is False


def test_supports_lists_and_macros() -> None:
    expr = parse_expr("size(item.labels) > 1 && item.labels.exists(l, l == 'x')")
    assert expr.eval({"item": {"labels": ["x", "y"]}}) is True


def test_source_is_kept() -> None:
    assert parse_expr("1 + 1").source == "1 + 1"


def test_syntax_error_is_reported_at_parse_time() -> None:
    with pytest.raises(ExprError, match="syntax"):
        parse_expr("1 +")


def test_reserved_identifier_gets_a_helpful_error() -> None:
    with pytest.raises(ExprError, match="reserved"):
        parse_expr("loop.i < 5")


def test_too_long_expression_is_rejected() -> None:
    with pytest.raises(ExprError, match="too long"):
        parse_expr("1 + " * 2000 + "1")


def test_missing_member_is_an_eval_error() -> None:
    with pytest.raises(ExprError, match="missing"):
        parse_expr("gate.missing").eval({"gate": {}})


def test_check_reports_unknown_variables() -> None:
    expr = parse_expr("gate.ok && nope.x")
    assert expr.check({"gate"}) == ['Unknown variable "nope" (known: gate)']
    assert expr.check({"gate", "nope"}) == []


def test_check_rejects_functions_outside_the_allow_list() -> None:
    expr = parse_expr("env('HOME') == 'x'")
    assert expr.check(set()) == ['Function "env" is not allowed']


def test_result_must_be_boolean_for_conditions() -> None:
    with pytest.raises(ExprError, match="boolean"):
        parse_expr("1 + 1").eval_bool({})
    assert parse_expr("1 < 2").eval_bool({}) is True


def test_expr_is_a_value_type() -> None:
    assert isinstance(parse_expr("true"), Expr)


def test_extension_functions_are_available() -> None:
    assert parse_expr("max(1, 2) == 2 && 'a,b'.split(',').size() == 2").eval({}) is True
    assert parse_expr("max(1, 2)").check(set()) == []

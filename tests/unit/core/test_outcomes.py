from whiletrue.core import (
    OUTCOME_PORTS,
    OutcomePolicy,
    Resolution,
    is_outcome_port,
    item_state_for,
    resolve_outcome,
)

PARK = OutcomePolicy("park")


def test_lists_the_documented_ports() -> None:
    assert OUTCOME_PORTS == ("ok", "fail", "needs_human", "skip", "rejected")


def test_is_outcome_port() -> None:
    assert is_outcome_port("ok")
    assert not is_outcome_port("findings")


def test_ok_and_skip_continue() -> None:
    assert resolve_outcome("ok", {}, PARK) == Resolution("continue")
    assert resolve_outcome("skip", {}, PARK) == Resolution("continue")


def test_a_wired_port_routes_to_its_target_even_for_ok() -> None:
    assert resolve_outcome("rejected", {"rejected": "fix"}, PARK) == Resolution(
        "route", target="fix"
    )
    assert resolve_outcome("ok", {"ok": "end"}, PARK) == Resolution("route", target="end")


def test_unwired_fail_follows_the_on_fail_policy() -> None:
    assert resolve_outcome("fail", {}, OutcomePolicy("park")) == Resolution(
        "exit", port="fail", state="parked"
    )
    assert resolve_outcome("fail", {}, OutcomePolicy("drop")) == Resolution(
        "exit", port="fail", state="failed"
    )
    assert resolve_outcome("fail", {}, OutcomePolicy("propagate")) == Resolution(
        "propagate", port="fail"
    )


def test_needs_human_and_rejected_with_no_route_park_the_item() -> None:
    assert resolve_outcome("needs_human", {}, PARK) == Resolution(
        "exit", port="needs_human", state="parked"
    )
    assert resolve_outcome("rejected", {}, PARK) == Resolution(
        "exit", port="rejected", state="parked"
    )


def test_propagate_policy_bubbles_non_ok_ports_to_the_parent() -> None:
    assert resolve_outcome("needs_human", {}, OutcomePolicy("propagate")) == Resolution(
        "propagate", port="needs_human"
    )


def test_custom_ports_without_a_wire_continue() -> None:
    assert resolve_outcome("findings", {}, PARK) == Resolution("continue")


def test_item_state_for_maps_terminal_ports() -> None:
    assert item_state_for("ok", "park") == "done"
    assert item_state_for("skip", "park") == "done"
    assert item_state_for("fail", "park") == "parked"
    assert item_state_for("fail", "drop") == "failed"
    assert item_state_for("fail", "propagate") == "failed"
    assert item_state_for("needs_human", "drop") == "parked"
    assert item_state_for("rejected", "park") == "parked"

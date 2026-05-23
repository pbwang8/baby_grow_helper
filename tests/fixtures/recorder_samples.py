"""Ten Chinese sample inputs for the recorder, plus expected schema invariants.

We deliberately don't pin the model's exact `summary` text — Qwen2.5-3B is a
small model and one-token-different output across pulls would break the
suite for no good reason. Instead we assert *structural invariants*: type is
in the allowed set, domains overlap with what the sentence is plainly
about, etc.

These same 10 inputs are also used by the unit-level snapshot tests, where
the LLM is mocked so we get exact-string assertions on a deterministic feed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RecorderSample:
    """One Chinese input + structural expectations.

    `expected_type` is now a frozenset of acceptable types — many of these
    sentences are genuinely borderline (e.g. "晚饭吃了两碗" can read as either
    `routine` or `observation`), and grading them as exact-match was an
    artifact of the fixture, not a real product requirement.
    """

    raw_text: str
    expected_type: frozenset[str]  # any of these counts as a match
    must_include_domains: frozenset[str]  # at least these domains
    forbid_domains: frozenset[str] = frozenset()

    @property
    def primary_type(self) -> str:
        """Single canonical type, used by the unit-level snapshot test where
        the LLM is mocked. Picks the lexicographically smallest member so
        the choice is deterministic across runs."""
        return sorted(self.expected_type)[0]


SAMPLES: list[RecorderSample] = [
    # 1. clear milestone
    RecorderSample(
        raw_text="今天小明第一次自己尿尿了，特别兴奋还跳起来庆祝",
        expected_type=frozenset({"milestone"}),
        must_include_domains=frozenset({"self_care"}),
    ),
    # 2. interest pattern — observation
    RecorderSample(
        raw_text="这两天他特别喜欢拼那套磁力片，一坐就是半小时",
        expected_type=frozenset({"observation"}),
        must_include_domains=frozenset({"cognition"}),
    ),
    # 3. eating — both routine (作息行为本身) and observation (胃口"不错"是评价) are defensible
    RecorderSample(
        raw_text="晚饭吃了两碗米饭，胃口不错",
        expected_type=frozenset({"routine", "observation"}),
        must_include_domains=frozenset({"self_care"}),
    ),
    # 4. curiosity in nature
    RecorderSample(
        raw_text="今天去公园看到一只蝴蝶，他追着它跑了好久，一直问\"它去哪儿了\"",
        expected_type=frozenset({"observation"}),
        must_include_domains=frozenset({"nature"}),
    ),
    # 5. minor injury — observation if neutral retelling, concern if parent flags worry
    RecorderSample(
        raw_text="刚才小明摔倒膝盖蹭破了点皮，哭了一会儿，自己说\"我没事\"",
        expected_type=frozenset({"observation", "concern"}),
        must_include_domains=frozenset({"emotion"}),
    ),
    # 6. spontaneous singing — music observation
    RecorderSample(
        raw_text="晚上听到《小星星》的旋律，他自己哼出了几句，调子大致对",
        expected_type=frozenset({"observation"}),
        must_include_domains=frozenset({"music"}),
    ),
    # 7. nap-too-short + temper — observation if just reporting, concern if parent flags reversal
    RecorderSample(
        raw_text="今天午睡只睡了 20 分钟就醒了，下午脾气特别大",
        expected_type=frozenset({"observation", "concern"}),
        must_include_domains=frozenset({"routine"}),
    ),
    # 8. prosocial sharing — observation; common confusion is filling 'social' into type slot
    RecorderSample(
        raw_text="他主动把自己的小饼干掰一半给隔壁的姐姐，说\"姐姐也吃\"",
        expected_type=frozenset({"observation"}),
        must_include_domains=frozenset({"social"}),
    ),
    # 9. clear motor milestone
    RecorderSample(
        raw_text="今天小明会用筷子夹起一颗花生米了，之前一直只能用勺子",
        expected_type=frozenset({"milestone"}),
        must_include_domains=frozenset({"motor"}),
    ),
    # 10. low-info input
    RecorderSample(
        raw_text="今天没什么特别的，挺正常一天",
        expected_type=frozenset({"observation"}),
        # 'other' is allowed but not required — the model may pick anything reasonable.
        # We don't assert any positive domain for this one; only test_type/no-throw.
        must_include_domains=frozenset(),
    ),
]

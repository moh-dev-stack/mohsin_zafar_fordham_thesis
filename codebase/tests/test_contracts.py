"""Tests for the guards that stop a silently-wrong run.

Each test here pins one defect found in the second audit pass. None of them
asserts a score; they assert that a configuration which *would* move a score
without anyone noticing is refused, counted, or held to what it was.
"""

from __future__ import annotations

import json
import random

import pytest

from dqa import agents, metrics
from dqa.agents import (
    _TIMESTAMP_LEAVES,
    _UNREAD_TIMESTAMP_LEAVES,
    CACHE_CONTRACT_FILE,
    CallStats,
    LLMAgent,
    Orchestrator,
    _is_timestamp_path,
    assert_cache_contract,
    cache_contract,
)
from dqa.baseline import a1_consistency
from dqa.inject import inject_planned, stratified_plan
from dqa.manifests import WIDTHS, Manifest, load_width, project
from dqa.metrics import SAFE_HARBOR_JSONPATHS, SAFE_HARBOR_MARKERS
from dqa.run import DATASETS, MANIFESTS_DIR, read_cohort

SEED = 42
LIMIT = 200
RATE = 0.30

# The token test the timestamp matcher used to be, kept so the replacement can
# be held to producing exactly the same read set on the shipped manifests.
_LEGACY_TOKENS = ("Time", "start", "end", "lastUpdated")


def _cohorts():
    for name, directory in DATASETS.items():
        if directory.is_dir():
            yield name, read_cohort(directory, LIMIT)


# ------------------------------------------------------- timestamp matching


def test_timestamp_matching_reads_exactly_what_the_token_test_read() -> None:
    """The leaf allow-list must not silently change which fields are judged.

    Replacing a substring test with an allow-list is only safe if the set of
    paths it accepts is unchanged on every manifest that ships. If this fails,
    a timeliness verdict moved and the published numbers moved with it.
    """
    for width in WIDTHS:
        manifest = load_width(MANIFESTS_DIR, width)["timeliness"]
        for policy in manifest.resource_types:
            for field in policy.allowed_fields:
                path = field.jsonpath_expression
                legacy = any(token in path for token in _LEGACY_TOKENS)
                assert _is_timestamp_path(path) is legacy, (
                    f"{width}/timeliness {path}: leaf matching says "
                    f"{_is_timestamp_path(path)}, token matching said {legacy}"
                )


def test_every_released_timestamp_is_read_or_listed_as_unread() -> None:
    """A timestamp a manifest releases is either judged or knowingly ignored.

    ``$.issued`` is the live case: intermediate and full release it, the rule
    does not read it, and the monolithic baseline does not read it either. That
    is a decision, and this test is where it stays a decision rather than
    decaying into an oversight.
    """
    suspicious = {"issued", "authoredOn", "recordedDate", "onsetDateTime", "date"}
    for width in WIDTHS:
        manifest = load_width(MANIFESTS_DIR, width)["timeliness"]
        for policy in manifest.resource_types:
            for field in policy.allowed_fields:
                leaf = field.jsonpath_expression.rsplit(".", 1)[-1]
                if leaf not in suspicious:
                    continue
                assert leaf in _TIMESTAMP_LEAVES or leaf in _UNREAD_TIMESTAMP_LEAVES, (
                    f"{width}/timeliness releases {field.jsonpath_expression}, "
                    f"which looks like a timestamp but is neither read nor "
                    f"listed in _UNREAD_TIMESTAMP_LEAVES"
                )


def test_a_path_whose_name_merely_contains_end_is_not_a_timestamp() -> None:
    """``$.gender`` contains "end". The token matcher would have judged it."""
    assert any(token in "$.gender" for token in _LEGACY_TOKENS)
    assert _is_timestamp_path("$.gender") is False
    assert _is_timestamp_path("$.period.end") is True
    assert _is_timestamp_path("$.meta.lastUpdated") is True


# --------------------------------------------------------- exposure metric


def test_every_counted_safe_harbor_path_is_matchable() -> None:
    """phi_total's paths and phi_exposed's markers must count the same thing."""
    for label, paths in SAFE_HARBOR_JSONPATHS.items():
        for path in paths:
            assert any(marker in path for marker in SAFE_HARBOR_MARKERS), (
                f"{label} path {path} is in the denominator and can never be "
                f"in the numerator"
            )


def test_no_marker_counts_what_the_denominator_does_not(monkeypatch) -> None:
    """A marker looser than its path inflates exposure above 1.0 for free."""
    assert "identifier[*].value" in SAFE_HARBOR_MARKERS
    assert "performer[*].reference" in SAFE_HARBOR_MARKERS
    # The loose forms would match a released sibling field that phi_total never
    # counts, which is the defect this pins.
    exposed = metrics.phi_exposed([{"$.identifier[*].system": ["urn:oid:1.2.3"]}])
    assert exposed == 0
    exposed = metrics.phi_exposed([{"$.performer[*].display": ["Dr Who"]}])
    assert exposed == 0


def test_marker_path_coverage_is_asserted_at_import() -> None:
    """The guard is a startup failure, not a comment."""
    with pytest.raises(AssertionError):
        original = metrics.SAFE_HARBOR_MARKERS
        try:
            metrics.SAFE_HARBOR_MARKERS = (*original, "no-such-field")
            metrics._assert_markers_cover_paths()
        finally:
            metrics.SAFE_HARBOR_MARKERS = original


# ------------------------------------------------------------- manifest set


def test_a_manifest_whose_dimension_disagrees_with_its_filename_is_refused(
    tmp_path,
) -> None:
    """The orchestrator dispatches on the filename, so the two must agree."""
    source = MANIFESTS_DIR / "minimal"
    target = tmp_path / "minimal"
    target.mkdir(parents=True)
    import shutil

    import yaml

    for path in source.glob("*.yaml"):
        shutil.copy(path, target / path.name)

    corrupted = yaml.safe_load((target / "timeliness.yaml").read_text())
    corrupted["dimension"] = "plausibility"
    (target / "timeliness.yaml").write_text(yaml.safe_dump(corrupted))

    with pytest.raises(ValueError, match="declares dimension"):
        load_width(tmp_path, "minimal")


def test_the_shipped_manifests_all_agree_with_their_filenames() -> None:
    for width in WIDTHS:
        for dimension, manifest in load_width(MANIFESTS_DIR, width).items():
            assert manifest.dimension == dimension


def test_an_unknown_dimension_names_itself_rather_than_keyerroring() -> None:
    """A manifest set with a dimension no agent handles must say so."""
    manifest_set = load_width(MANIFESTS_DIR, "minimal")
    rogue = Manifest(
        **{
            **manifest_set["plausibility"].model_dump(),
            "agent_id": "rogue",
        }
    )
    orchestrator = Orchestrator({"nonsense": rogue}, cache_dir=None)
    with pytest.raises(KeyError, match="no agent for dimension"):
        orchestrator.evaluate(
            {
                "resourceType": "Observation",
                "id": "o1",
                "code": {"coding": [{"code": "8867-4"}]},
                "valueQuantity": {"value": 1.0, "unit": "/min"},
            }
        )


# ---------------------------------------------------------- verdict cache


def test_the_cache_contract_is_written_on_adoption(tmp_path) -> None:
    assert_cache_contract(tmp_path)
    recorded = json.loads((tmp_path / CACHE_CONTRACT_FILE).read_text())
    assert recorded == cache_contract()
    assert recorded["temperature"] == 0.0
    assert set(recorded["prompt_sha256"]) == {"plausibility", "consistency"}


def test_a_cache_written_under_a_different_prompt_is_refused(tmp_path) -> None:
    """The defect: editing a prompt used to replay verdicts it never produced."""
    assert_cache_contract(tmp_path)
    original = agents._PROMPTS["plausibility"]
    try:
        agents._PROMPTS["plausibility"] = original + " Also consider the weather."
        with pytest.raises(RuntimeError, match="different decoding configuration"):
            assert_cache_contract(tmp_path)
    finally:
        agents._PROMPTS["plausibility"] = original
    # Restored: the same cache is acceptable again.
    assert_cache_contract(tmp_path)


def test_the_shipped_cache_matches_the_shipped_prompts() -> None:
    """The bundle's own cache must be replayable by the bundle's own code."""
    from dqa.run import CACHE_DIR

    if CACHE_DIR is None or not CACHE_DIR.is_dir():
        pytest.skip("no verdict cache in this checkout")
    assert_cache_contract(CACHE_DIR)


def test_the_cache_key_did_not_move(tmp_path) -> None:
    """Re-keying would strand every shipped entry, so the key is pinned."""
    import hashlib

    agent = LLMAgent("plausibility", tmp_path)
    payload = '{"$.valueQuantity.value": [72.0]}'
    expected = hashlib.sha256(
        f"{agent.model}|plausibility|v2|{payload}".encode()
    ).hexdigest()
    assert agent._cache_path(payload).name == f"{expected}.json"


# ------------------------------------------------------------ call accounting


def test_call_stats_count_an_offline_skip_rather_than_reporting_a_clean_run(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    agent = LLMAgent("plausibility", tmp_path)
    agent.check({"_projected_fields": {"$.valueQuantity.value": [72.0]}})
    assert agent.stats.as_dict() == {
        "cache_hits": 0,
        "cache_misses": 0,
        "model_errors": 0,
        "offline_skips": 1,
        "truncated_retries": 0,
        "self_corrections": 0,
    }


def test_call_stats_count_a_hit(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    agent = LLMAgent("plausibility", tmp_path)
    payload = json.dumps({"$.valueQuantity.value": [72.0]}, sort_keys=True)
    path = agent._cache_path(payload)
    path.write_text(json.dumps({"verdict": "pass", "justification": "x", "confidence": 1.0}))
    verdict = agent.check({"_projected_fields": {"$.valueQuantity.value": [72.0]}})
    assert verdict.verdict == "pass"
    assert agent.stats.hits == 1
    assert agent.stats.offline == 0


def test_call_stats_merge_across_agents() -> None:
    pooled = CallStats()
    pooled.merge(CallStats(hits=2, misses=1))
    pooled.merge(CallStats(errors=3, offline=4))
    assert pooled.as_dict() == {
        "cache_hits": 2,
        "cache_misses": 1,
        "model_errors": 3,
        "offline_skips": 4,
        "truncated_retries": 0,
        "self_corrections": 0,
    }


SELF_CORRECTING_REPLY = (
    '```json\n{\n  "verdict": "fail",\n'
    '  "justification": "Serum sodium of 140.96 mmol/L is at the upper limit of normal.",\n'
    '  "confidence": 0.85\n}\n```\n\n'
    "**Correction - Revised Response:**\n\n"
    '```json\n{\n  "verdict": "pass",\n'
    '  "justification": "140.96 mmol/L is within the normal physiological range.",\n'
    '  "confidence": 0.95\n}\n```'
)


def test_the_final_verdict_wins_when_the_model_answers_twice() -> None:
    """The worst defect the instrumentation found, pinned.

    On some payloads this model answers, reconsiders in prose, and answers
    again. ``raw[find("{") : rfind("}") + 1]`` spans both objects plus the prose
    between them and raises, so the verdict collapsed to "uncertain"; and when
    the reply hit the output cap before the second object arrived, that slice
    parsed cleanly and the run recorded the model's RETRACTED answer.
    """
    assert len(agents.json_objects(SELF_CORRECTING_REPLY)) == 2
    parsed = agents.extract_verdict(SELF_CORRECTING_REPLY)
    assert parsed is not None
    assert parsed["verdict"] == "pass", "the retracted first answer must not win"

    # The old extraction on the same text: unparseable.
    opening = SELF_CORRECTING_REPLY.find("{")
    closing = SELF_CORRECTING_REPLY.rfind("}")
    with pytest.raises(ValueError):
        json.loads(SELF_CORRECTING_REPLY[opening : closing + 1])


def test_the_old_extraction_returned_the_retracted_answer_when_truncated() -> None:
    """Why raising max_tokens alone would not have been enough."""
    cut = SELF_CORRECTING_REPLY[: SELF_CORRECTING_REPLY.find("```", 10)]
    old = json.loads(cut[cut.find("{") : cut.rfind("}") + 1])
    assert old["verdict"] == "fail"
    assert agents.extract_verdict(cut)["verdict"] == "fail"
    # With the budget raised the second object arrives and the correction wins.
    assert agents.extract_verdict(SELF_CORRECTING_REPLY)["verdict"] == "pass"


def test_a_brace_inside_a_justification_does_not_close_the_object() -> None:
    text = '{"verdict": "fail", "justification": "value {x} is impossible", "confidence": 1.0}'
    assert agents.json_objects(text) == [text]
    assert agents.extract_verdict(text)["verdict"] == "fail"


def test_an_escaped_quote_inside_a_justification_is_handled() -> None:
    text = '{"verdict": "pass", "justification": "the unit is \\"mmol/L\\"", "confidence": 1.0}'
    assert agents.extract_verdict(text)["justification"] == 'the unit is "mmol/L"'


def test_an_object_without_a_verdict_key_is_skipped() -> None:
    text = '{"note": "thinking"}\nthen\n{"verdict": "fail", "confidence": 1.0}\n{"trailing": 1}'
    assert agents.extract_verdict(text)["verdict"] == "fail"


def test_no_json_at_all_returns_none_rather_than_guessing() -> None:
    assert agents.extract_verdict("I cannot answer that.") is None
    assert agents.extract_verdict("") is None


def test_a_self_correction_is_counted(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    agent = LLMAgent("plausibility", tmp_path)

    class _Client:
        class messages:
            @staticmethod
            def create(**_kwargs):
                return _Reply(SELF_CORRECTING_REPLY)

    agent._client = _Client()
    verdict = agent.check({"_projected_fields": {"$.valueQuantity.value": [140.96]}})
    assert verdict.verdict == "pass"
    assert agent.stats.self_corrections == 1
    assert agent.stats.errors == 0


class _Reply:
    """Minimal stand-in for the SDK response shape."""

    def __init__(self, text: str, stop_reason: str = "end_turn") -> None:
        self.content = [type("Block", (), {"text": text})()]
        self.stop_reason = stop_reason


def test_a_truncated_response_is_re_asked_rather_than_scored_as_uncertain(
    tmp_path, monkeypatch
) -> None:
    """The defect this pins is the worst one the instrumentation found.

    A verdict costs 208 to 217 output tokens on these cohorts against a 256
    cap, so a slightly longer justification overran it. The body was then cut
    mid-string, ``rfind("}")`` returned -1, the slice was empty, and the
    ``JSONDecodeError`` turned a real "fail" into "uncertain" -- which scores as
    a non-detection. Nothing in the output showed it had happened, and because
    "uncertain" is never cached the payload was re-asked, and could resolve
    differently, on every later run.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    agent = LLMAgent("plausibility", tmp_path)
    truncated = '```json\n{\n  "verdict": "fail",\n  "justification": "the value is imposs'
    complete = '{"verdict": "fail", "justification": "out of range", "confidence": 0.95}'
    budgets: list[int] = []

    class _Client:
        class messages:
            @staticmethod
            def create(**kwargs):
                budgets.append(kwargs["max_tokens"])
                return _Reply(truncated, "max_tokens") if len(budgets) == 1 else _Reply(complete)

    agent._client = _Client()
    verdict = agent.check({"_projected_fields": {"$.valueQuantity.value": [9999.0]}})

    assert verdict.verdict == "fail", "a truncated response must not become a non-detection"
    assert budgets == [agents.MAX_TOKENS, agents.MAX_TOKENS_RETRY]
    assert agent.stats.truncated_retries == 1
    assert agent.stats.errors == 0
    # The recovered verdict is cached, so the payload is not re-asked next run.
    assert agent._cache_path(
        json.dumps({"$.valueQuantity.value": [9999.0]}, sort_keys=True)
    ).is_file()


def test_truncation_is_detected_without_a_stop_reason(tmp_path, monkeypatch) -> None:
    """An opening brace with no closing one is the symptom that breaks parsing."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    agent = LLMAgent("consistency", tmp_path)
    calls: list[int] = []

    class _Client:
        class messages:
            @staticmethod
            def create(**kwargs):
                calls.append(kwargs["max_tokens"])
                if len(calls) == 1:
                    # No stop_reason of "max_tokens", but unmistakably cut off.
                    return _Reply('{"verdict": "pass", "justific', "end_turn")
                return _Reply('{"verdict": "pass", "justification": "ok", "confidence": 1.0}')

    agent._client = _Client()
    assert agent.check({"_projected_fields": {"$.period.start": ["2024-01-01"]}}).verdict == "pass"
    assert agent.stats.truncated_retries == 1


def test_a_response_with_no_json_at_all_is_an_error_not_a_silent_pass(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    agent = LLMAgent("plausibility", tmp_path)

    class _Client:
        class messages:
            @staticmethod
            def create(**_kwargs):
                return _Reply("I cannot answer that.")

    agent._client = _Client()
    verdict = agent.check({"_projected_fields": {"$.valueQuantity.value": [1.0]}})
    assert verdict.verdict == "uncertain"
    assert agent.stats.errors == 1


def test_the_retry_budget_is_recorded_in_the_cache_contract() -> None:
    assert cache_contract()["max_tokens"] == agents.MAX_TOKENS
    assert cache_contract()["max_tokens_retry"] == agents.MAX_TOKENS_RETRY
    assert agents.MAX_TOKENS_RETRY > agents.MAX_TOKENS


def test_a_model_error_is_counted_not_swallowed(tmp_path, monkeypatch) -> None:
    """The defect: an outage produced an artefact indistinguishable from a run."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    agent = LLMAgent("plausibility", tmp_path)

    class _Exploding:
        class messages:
            @staticmethod
            def create(**_kwargs):
                raise ConnectionError("network went away")

    agent._client = _Exploding()
    verdict = agent.check({"_projected_fields": {"$.valueQuantity.value": [72.0]}})
    assert verdict.verdict == "uncertain"
    assert agent.stats.errors == 1
    assert agent.stats.misses == 1


# ------------------------------------------------- injector cross-contamination


@pytest.mark.parametrize("cohort_name", [n for n, _ in _cohorts()])
def test_a_timeliness_injection_does_not_manufacture_a_consistency_defect(
    cohort_name: str,
) -> None:
    """Pins the latent collision documented on ``inject_timeliness``.

    Pushing Encounter ``period.start`` into 2126 leaves ``period.end`` behind,
    so an Encounter carrying both would violate the ordering invariant and take
    a consistency false positive against a row labelled timeliness. On these
    cohorts none of the affected Encounters carries a ``period.end``, so the
    collision is inert. If a cohort change makes it live, this fails rather than
    quietly contaminating the consistency column.
    """
    cohort = dict(_cohorts())[cohort_name]
    rng = random.Random(SEED)
    plan = stratified_plan(cohort, rng, RATE)
    collisions = 0
    for resource, dimension in zip(cohort, plan, strict=True):
        if dimension is None:
            continue
        modified, label = inject_planned(resource, dimension, rng)
        if label.dimension != "timeliness":
            continue
        if modified.get("resourceType") != "Encounter":
            continue
        if a1_consistency(modified).is_failure:
            collisions += 1
    assert collisions == 0, (
        f"{cohort_name}: {collisions} timeliness injections also broke the "
        f"Encounter period invariant, so the consistency column is measured "
        f"against contaminated ground truth"
    )


# -------------------------------------------------------------- cohort reading


def test_the_excluded_file_prefixes_are_in_the_run_contract() -> None:
    """Which files are read determines the cohort, so it belongs in the contract."""
    from dqa.run import EXCLUDED_FILE_PREFIXES

    assert EXCLUDED_FILE_PREFIXES == ("hospital", "practitioner")
    source = (MANIFESTS_DIR.parent / "src" / "dqa" / "run.py").read_text()
    assert '"excluded_file_prefixes": EXCLUDED_FILE_PREFIXES' in source


def test_projection_is_unchanged_by_the_timeliness_fix() -> None:
    """The rule change consumes the slice; it must not alter what is released.

    This is what makes the shipped model verdicts for a seed still valid after
    the timeliness rule changed: the payload the model was asked about is a
    function of the manifest and the resource alone.
    """
    for _, cohort in _cohorts():
        for width in WIDTHS:
            manifest = load_width(MANIFESTS_DIR, width)["plausibility"]
            for resource in cohort[:25]:
                sliced = project(resource, manifest)
                if sliced is None:
                    continue
                assert set(sliced) == {
                    "resourceType",
                    "_agent_id",
                    "_dimension",
                    "_projected_fields",
                }

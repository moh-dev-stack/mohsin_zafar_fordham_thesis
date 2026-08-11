"""The four Confined agents, each confined to its manifest-projected slice.

Two agents are rule-only and deterministic (completeness, timeliness).
Two blend rules with a pinned LLM snapshot (plausibility, consistency),
because judging whether a value is clinically believable is not a
closed-form rule.

The LLM agents call the Anthropic API directly. An earlier revision
routed them through CrewAI, but every agent is created with no tools
and no delegation, so the framework wrapped a single completion call
and contributed nothing to routing. Removing it cuts a dependency, cuts
roughly twenty seconds of telemetry setup per call, and leaves the
orchestration behaviour identical.

Verdicts are cached on disk under a SHA-256 of
(model snapshot, dimension, projected payload), so re-running against
unchanged manifests costs nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from dqa.linkage import surrogate_path_for
from dqa.manifests import Dimension, Manifest
from dqa.ranges import RangeRule, inputs_verdict, projected_range_inputs

VerdictValue = Literal["pass", "fail", "uncertain"]

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
MAX_FUTURE_SKEW_MINUTES = 5
MAX_PAST_AGE_DAYS = 365 * 30

# Fixed evaluation clock for the timeliness checks, in both this module and
# ``dqa.baseline``.
#
# WHY: the two timeliness rules used ``datetime.now()``, so "implausibly old"
# (more than MAX_PAST_AGE_DAYS before now) and "in the future" (more than
# MAX_FUTURE_SKEW_MINUTES after now) both slid forward every day the suite ran.
# A published timeliness F1 was therefore only reproducible on the day it was
# computed, which is not a property a result may have.
#
# WHY THIS DATE: 2026-08-05 is the day the current result set in ``results/``
# was regenerated, so it is the clock those numbers were actually computed
# against. Anchoring there preserves them exactly. The choice is also robust
# rather than lucky: sweeping every timestamp the two rules read across all
# three cohorts (N = 200 each), the closest any of them comes to the 30-year
# past boundary is 1,865 days and the closest any comes to the present boundary
# is 2,403 days. Any anchor within about five years of this one therefore yields
# byte-identical verdicts, and the published Baseline timeliness figures
# (0.6250 Synthea, 0.0719 MIMIC-IV, 1.0000 eICU) are unchanged by the switch.
NOW_ANCHOR = datetime(2026, 8, 5, 0, 0, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Verdict:
    """One agent's judgement on one resource."""

    dimension: Dimension
    verdict: VerdictValue
    justification: str
    confidence: float = 1.0

    @property
    def is_failure(self) -> bool:
        return self.verdict == "fail"


# ---------------------------------------------------------------- rule agents

REQUIRED_FIELDS: dict[str, list[str]] = {
    "Observation": ["$.status", "$.code.coding[*].code", "$.subject.reference"],
    "Encounter": ["$.status", "$.period.start", "$.subject.reference"],
}


def _requirement_met(projected: dict[str, Any], path: str) -> bool:
    """Whether ``path`` is satisfied, allowing a surrogate to stand in for it.

    WHY this indirection rather than a literal presence test. Under the L1
    linkage rung, :func:`dqa.linkage.rewrite_slice` **deletes** each linkage key
    and installs a differently named surrogate key, because the exposure metric
    matches Safe Harbor markers against the projected JSONPath rather than
    against its value, so editing the value in place would leave exposure
    unmoved. The consequence was that the same real Observation passed
    completeness un-rewritten and failed it after surrogate substitution, naming
    ``$.subject.reference`` in its justification. Running the ladder on that
    footing would have shown completeness collapsing from 1.000 on every event
    resource, and the ladder would have appeared to prove that surrogates
    destroy detection, which is an artefact of this rule and not a property of
    surrogates.

    A surrogate carries exactly the linkage information completeness is checking
    for: it establishes that the record *has* a subject, which is the only thing
    a presence check can know. So the requirement is met by the canonical path
    or by the surrogate that replaced it. The canonical path is what gets named
    when the requirement is unmet, so justifications are unchanged.

    ``REQUIRED_FIELDS`` keeps its published shape, a flat list of canonical
    paths, so callers and tests that inspect it continue to work.
    """
    if projected.get(path):
        return True
    alternative = surrogate_path_for(path)
    return bool(alternative) and bool(projected.get(alternative))

# Which projected JSONPaths carry a timestamp this agent should judge.
#
# This was a substring test over ("Time", "start", "end", "lastUpdated"), which
# is fragile in both directions. It reads nothing at ``$.issued`` -- a real
# instant that the intermediate and full manifests release -- because "issued"
# contains none of the tokens; and it would read ``$.gender`` as a timestamp,
# because "gender" contains "end", the moment a manifest released it to this
# agent. Matching on the leaf name is explicit and greppable instead.
#
# The set of paths actually read is unchanged from the token version on every
# shipped manifest, so no published verdict moves; ``tests/test_dqa.py`` pins
# that. ``$.issued`` stays unread deliberately: the monolithic baseline does not
# read it either (see ``dqa.baseline.a1_timeliness``), and adding it to one arm
# only would reintroduce on timeliness exactly the checker asymmetry this study
# removed from plausibility. It is released and not read because manifest width
# is the experimental variable: a wider manifest is *meant* to hand over more
# than its checker consumes.
# Held to exactly the leaves the shipped manifests release and this rule reads.
# Widening a manifest with a new timestamp requires adding its leaf here as a
# deliberate act; ``tests/test_dqa.py`` fails if a released timestamp leaf is
# missing from this set without being listed as knowingly unread.
_TIMESTAMP_LEAVES = frozenset({"effectiveDateTime", "lastUpdated", "start", "end"})

# Timestamp leaves a manifest may release that this rule deliberately does not
# read. Listed rather than merely absent, so the omission is a decision on the
# record instead of an oversight.
_UNREAD_TIMESTAMP_LEAVES = frozenset({"issued"})


def _is_timestamp_path(path: str) -> bool:
    """Whether a projected JSONPath names a timestamp the timeliness rule reads."""
    leaf = path.rsplit(".", 1)[-1]
    return leaf in _TIMESTAMP_LEAVES


def _parse_iso(value: Any) -> Any:
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def check_completeness(sliced: dict[str, Any]) -> Verdict:
    """Required-field presence. Reads presence only, never clinical values."""
    resource_type = sliced.get("resourceType", "Unknown")
    projected = sliced.get("_projected_fields", {})
    required = REQUIRED_FIELDS.get(resource_type, [])
    if not required:
        return Verdict("completeness", "uncertain", f"no rules for {resource_type}")

    missing = [path for path in required if not _requirement_met(projected, path)]
    if missing:
        return Verdict(
            "completeness",
            "fail",
            f"missing on {resource_type}: {', '.join(missing)}",
        )
    return Verdict("completeness", "pass", f"all required fields present on {resource_type}")


def check_timeliness(sliced: dict[str, Any], now: datetime | None = None) -> Verdict:
    """Timestamp plausibility. Period ordering belongs to consistency, see below.

    ``now`` defaults to :data:`NOW_ANCHOR` rather than the wall clock, so the
    verdict is a function of the resource alone. Callers may pass an explicit
    reference time to test the boundaries.
    """
    from datetime import timedelta

    projected = sliced.get("_projected_fields", {})
    resource_type = sliced.get("resourceType", "Unknown")
    now = NOW_ANCHOR if now is None else now
    issues: list[str] = []

    for path, values in projected.items():
        if not _is_timestamp_path(path):
            continue
        for raw in values:
            if not isinstance(raw, str):
                continue
            parsed = _parse_iso(raw)
            if parsed is None:
                issues.append(f"unparseable timestamp at {path}")
            elif parsed > now + timedelta(minutes=MAX_FUTURE_SKEW_MINUTES):
                issues.append(f"future timestamp at {path}")
            elif parsed < now - timedelta(days=MAX_PAST_AGE_DAYS):
                issues.append(f"implausibly old timestamp at {path}")

    # Encounter period ordering is deliberately NOT checked here. The injector
    # labels "end precedes start" as a consistency defect, so flagging it as
    # timeliness produces a false positive on one dimension and a false
    # negative on the other for the same record. The monolithic baseline does
    # not check it in its timeliness rule either, so removing it also removes
    # an asymmetry between the two systems.

    if issues:
        return Verdict("timeliness", "fail", "; ".join(issues[:3]), confidence=0.95)
    return Verdict("timeliness", "pass", f"timestamps within bounds for {resource_type}", 0.95)


def check_plausibility_ranges(
    sliced: dict[str, Any], rules: dict[str, RangeRule]
) -> Verdict:
    """Rule-only plausibility, judged against the shared per-analyte ranges.

    The alternative to :class:`LLMAgent` on the plausibility dimension. It reads
    exactly the three fields ``dqa.ranges`` needs -- code, value and unit --
    out of the manifest-projected slice, and nothing else. It is pure: no clock,
    no network, no model.

    This is the Confined side of the controlled comparison. Handing this agent and the
    range-based monolith the same rule file leaves field visibility as the only
    difference between them, which is the variable the study is about. It is not
    the default: the published results were produced by the LLM agent and stay
    reproducible only while that remains the default path.
    """
    verdict, reason = inputs_verdict(
        projected_range_inputs(sliced.get("_projected_fields", {})), rules
    )
    if verdict == "fail":
        return Verdict("plausibility", "fail", reason)
    if verdict == "pass":
        return Verdict("plausibility", "pass", reason)
    return Verdict("plausibility", "uncertain", reason, 0.5)


# ----------------------------------------------------------------- LLM agents

_PROMPTS: dict[Dimension, str] = {
    "plausibility": (
        "You are a clinical plausibility reviewer. You see a manifest-permitted "
        "slice of one FHIR resource and decide whether the recorded values are "
        "physiologically believable for the patient described. Values far outside "
        "any survivable physiological range are implausible."
    ),
    "consistency": (
        "You are a clinical consistency reviewer. You see a manifest-permitted "
        "slice of one FHIR resource and decide whether its fields contradict one "
        "another, for example a period that ends before it starts."
    ),
}

_INSTRUCTION = (
    "Respond with ONLY a JSON object with keys verdict "
    '("pass", "fail" or "uncertain"), justification (one sentence), and '
    "confidence (0.0 to 1.0). Return verdict \"fail\" if and only if the slice "
    "shows a defect in your assigned dimension."
)


# Output budget for a verdict. Measured rather than guessed: a typical verdict
# on these cohorts costs 208 to 217 output tokens, so 256 leaves only a few
# tokens of headroom and a slightly longer justification overruns it.
#
# WHY THAT MATTERED: an overrun truncates the JSON body mid-string. The
# extraction below takes ``raw[raw.find("{") : raw.rfind("}") + 1]``, and with no
# closing brace ``rfind`` returns -1, which makes that slice the empty string and
# raises ``JSONDecodeError``. The except arm then collapsed the verdict to
# "uncertain", which scores as "did not flag". So the cap silently converted a
# real judgement into a non-detection, at a rate of a few per run, with nothing
# in the output to show it. Because "uncertain" is never written to the cache,
# the affected payload was re-asked on every subsequent run and could resolve
# differently each time -- which is one contributor to the residual run-to-run
# drift this study reports at temperature 0.
#
# 256 was the published budget and it is now known to have been actively
# harmful. On payloads where this model answers, reconsiders and answers again
# (see :func:`extract_verdict`), a 256-token cap cut the reply off before the
# second, corrected object arrived. The old extraction then parsed the first
# object cleanly and the run recorded the model's RETRACTED verdict as its
# answer. Raising the cap is what lets the correction arrive; fixing the
# extraction is what makes the correction the one that counts. Both are needed:
# either alone leaves a wrong or missing verdict.
#
# Raising it invalidates every cached verdict, deliberately. Entries written
# under the old cap and the old extraction cannot be distinguished from correct
# ones by inspection, and ``assert_cache_contract`` refuses to mix them.
MAX_TOKENS = 512
MAX_TOKENS_RETRY = 1024

# Name of the sidecar the cache directory carries, recording the decoding
# configuration every entry in it was produced under.
CACHE_CONTRACT_FILE = "CACHE_CONTRACT.json"


def cache_contract() -> dict[str, Any]:
    """The decoding configuration that determines what a cached verdict means."""
    return {
        "key_version": "v2",
        "model": os.environ.get("ANTHROPIC_MODEL_SNAPSHOT", DEFAULT_MODEL),
        "max_tokens": MAX_TOKENS,
        "max_tokens_retry": MAX_TOKENS_RETRY,
        "temperature": 0.0,
        # Which object is taken when the reply contains more than one. Recorded
        # because a cached verdict produced under first-brace-to-last-brace
        # extraction can be the model's retracted answer, and nothing about the
        # stored entry says so.
        "extraction": "last-parseable-object-carrying-a-verdict",
        "prompt_sha256": {
            dimension: hashlib.sha256(
                (prompt + " " + _INSTRUCTION).encode()
            ).hexdigest()
            for dimension, prompt in _PROMPTS.items()
        },
    }


def assert_cache_contract(cache_dir: Path | None) -> None:
    """Refuse to replay a cache written under a different prompt.

    WHY this rather than putting the prompt in the key: the cache key covers the
    model, the dimension and the projected payload, but not the system prompt,
    ``_INSTRUCTION`` or ``max_tokens``. Editing a prompt therefore used to
    replay verdicts produced by a prompt that no longer exists, silently, unless
    somebody remembered to bump the hand-written ``"v2"`` salt.

    Folding the prompt into the key would fix that, and would also strand the
    2,005 shipped entries behind digests no version of this code can recompute.
    The cache is a primary experimental artefact -- it is what makes the
    model-backed numbers replayable at all -- so it is worth more intact than
    re-keyed. This function gets the same guarantee by recording the
    configuration beside the entries and failing loudly on a mismatch.

    A cache directory with no sidecar is adopted: the file is written on first
    use, which is how the shipped cache acquires one without being invalidated.
    """
    if cache_dir is None:
        return
    path = Path(cache_dir) / CACHE_CONTRACT_FILE
    current = cache_contract()
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current, indent=2, sort_keys=True))
        return
    try:
        recorded = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"unreadable cache contract at {path}: {exc}") from exc
    if recorded != current:
        differing = sorted(
            k for k in set(recorded) | set(current) if recorded.get(k) != current.get(k)
        )
        raise RuntimeError(
            f"the verdict cache at {cache_dir} was written under a different "
            f"decoding configuration; {differing} differ. Every cached verdict "
            f"in it answers a different question from the one this code asks. "
            f"Either restore the configuration recorded in {path}, or delete "
            f"that file and the cache and re-run against the model."
        )


def json_objects(text: str) -> list[str]:
    """Every balanced top-level ``{...}`` span in ``text``, in order.

    Brace counting rather than ``find``/``rfind``, and string-aware so a brace
    inside a justification cannot close an object early.
    """
    spans: list[str] = []
    depth = 0
    start: int | None = None
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                spans.append(text[start : index + 1])
                start = None
    return spans


def extract_verdict(text: str) -> dict[str, Any] | None:
    """The model's FINAL verdict object, or ``None`` if it emitted none.

    WHY this is not a one-liner. The extraction used to be
    ``json.loads(raw[raw.find("{") : raw.rfind("}") + 1])``, which assumes the
    response contains exactly one JSON object. It does not always. On some
    payloads this model answers, then reconsiders in prose, then answers again:

        ```json
        {"verdict": "fail",  ...}      <- retracted
        ```
        **Correction - Revised Response:**
        ```json
        {"verdict": "pass",  ...}      <- the actual answer
        ```

    First-brace-to-last-brace spans both objects and the prose between them, so
    it raises and the verdict collapses to "uncertain". Worse, when the reply
    happened to hit the output cap before the second object arrived, the slice
    contained only the first object, parsed cleanly, and the run recorded the
    model's *retracted* verdict as its answer, with nothing anywhere to show it.

    Taking the last parseable object that carries a ``verdict`` key gives the
    model's final answer in both cases, and returns ``None`` rather than
    guessing when there is nothing to take.
    """
    for span in reversed(json_objects(text)):
        try:
            parsed = json.loads(span)
        except ValueError:
            continue
        if isinstance(parsed, dict) and "verdict" in parsed:
            return parsed
    return None


def _is_truncated(response: Any) -> bool:
    """Whether the model stopped because it ran out of output budget.

    Checked two ways because they fail independently: ``stop_reason`` is the
    provider's own statement, and a body with no closing brace is the symptom
    that actually breaks parsing. Either is enough to justify re-asking.
    """
    if getattr(response, "stop_reason", None) == "max_tokens":
        return True
    try:
        text = response.content[0].text
    except (AttributeError, IndexError, TypeError):
        return False
    return "{" in text and text.rfind("}") < text.find("{")


@dataclass(slots=True)
class CallStats:
    """How a run's model verdicts were actually obtained.

    WHY: every failure inside :meth:`LLMAgent.check` -- a dropped connection, a
    rate limit, a truncated response, a malformed JSON body -- collapses to
    ``Verdict(uncertain)``, which scores as "did not flag". Without a count, a
    run degraded by an outage is indistinguishable in the output artefact from a
    clean one, and a reader cannot tell whether a low F1 is a finding or a
    network incident. These counters go into the run output beside the scores so
    that question is answerable from the artefact alone.

    ``offline`` counts calls skipped because no API key was set, which is a
    deliberate configuration rather than a failure, so it is tallied apart from
    ``errors``.
    """

    hits: int = 0
    misses: int = 0
    errors: int = 0
    offline: int = 0
    truncated_retries: int = 0
    self_corrections: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "cache_hits": self.hits,
            "cache_misses": self.misses,
            "model_errors": self.errors,
            "offline_skips": self.offline,
            "truncated_retries": self.truncated_retries,
            "self_corrections": self.self_corrections,
        }

    def merge(self, other: CallStats) -> None:
        self.hits += other.hits
        self.misses += other.misses
        self.errors += other.errors
        self.offline += other.offline
        self.truncated_retries += other.truncated_retries
        self.self_corrections += other.self_corrections


class LLMAgent:
    """A manifest-confined agent backed by a pinned model snapshot."""

    def __init__(self, dimension: Dimension, cache_dir: Path | None = None) -> None:
        self.dimension = dimension
        self.model = os.environ.get("ANTHROPIC_MODEL_SNAPSHOT", DEFAULT_MODEL)
        self.cache_dir = cache_dir
        self.stats = CallStats()
        self._client: Any = None

    @property
    def system_prompt(self) -> str:
        """The exact system string sent to the model, and hashed into the key."""
        return _PROMPTS[self.dimension] + " " + _INSTRUCTION

    def _cache_path(self, payload: str) -> Path | None:
        """Where the verdict for ``payload`` is cached.

        The key is ``(model, dimension, "v2", payload)`` and is deliberately
        unchanged: the shipped ``.llm_cache`` is a primary experimental artefact
        and re-keying it would strand all 2,005 entries behind digests nothing
        can compute any more.

        What the key does NOT cover -- the system prompt, ``_INSTRUCTION`` and
        ``max_tokens`` -- is covered instead by :func:`assert_cache_contract`,
        which refuses to read a cache written under a different prompt rather
        than replaying it silently. See that function for why detection beats
        re-keying here.
        """
        if self.cache_dir is None:
            return None
        key = hashlib.sha256(
            f"{self.model}|{self.dimension}|v2|{payload}".encode()
        ).hexdigest()
        return self.cache_dir / f"{key}.json"

    def _ask(self, payload: str, max_tokens: int) -> Any:
        """One model call. Greedy decoding, pinned snapshot.

        Temperature is 0.0 because the API default is 1.0, which samples: without
        it a cache miss draws a fresh verdict and the run is not reproducible
        from its contract. The disk cache hid that, because once a verdict was
        stored the value stayed stable while any unseen payload produced a new
        sample.
        """
        return self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=0.0,
            system=self.system_prompt,
            messages=[{"role": "user", "content": f"SLICE:\n{payload}"}],
        )

    def check(self, sliced: dict[str, Any]) -> Verdict:
        payload = json.dumps(sliced.get("_projected_fields", {}), sort_keys=True, default=str)

        cache_file = self._cache_path(payload)
        if cache_file is not None and cache_file.is_file():
            try:
                cached = json.loads(cache_file.read_text())
                verdict = Verdict(
                    self.dimension,
                    cached["verdict"],
                    cached["justification"],
                    float(cached.get("confidence", 0.5)),
                )
            except (OSError, ValueError, KeyError):
                cache_file.unlink(missing_ok=True)
            else:
                self.stats.hits += 1
                return verdict

        if not os.environ.get("ANTHROPIC_API_KEY"):
            # Offline runs stay deterministic so structural behaviour is
            # reproducible without model access.
            self.stats.offline += 1
            return Verdict(self.dimension, "uncertain", "no API key; offline run", 0.5)

        self.stats.misses += 1

        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()

        try:
            response = self._ask(payload, MAX_TOKENS)
            if _is_truncated(response):
                # The body cannot contain a complete JSON object, so parsing it
                # would raise and the verdict would silently become a
                # non-detection. Re-ask once with room to finish the sentence.
                self.stats.truncated_retries += 1
                response = self._ask(payload, MAX_TOKENS_RETRY)
            raw = response.content[0].text
            if len(json_objects(raw)) > 1:
                # The model answered more than once. Counted, because which
                # answer is taken is a decision the reader should be able to
                # see was made.
                self.stats.self_corrections += 1
            parsed = extract_verdict(raw)
            if parsed is None:
                raise ValueError(f"no verdict object in response: {raw[:160]!r}")
            verdict: VerdictValue = parsed.get("verdict", "uncertain")
            justification = str(parsed.get("justification", ""))[:500]
            confidence = float(parsed.get("confidence", 0.5))
        except Exception as exc:  # noqa: BLE001 - any failure collapses to uncertain
            # Counted, not just swallowed. A non-zero model_errors in the run
            # output is what tells a reader the scores below it are degraded.
            self.stats.errors += 1
            return Verdict(self.dimension, "uncertain", f"unparsed: {type(exc).__name__}", 0.5)

        if cache_file is not None and verdict != "uncertain":
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(
                json.dumps(
                    {
                        "verdict": verdict,
                        "justification": justification,
                        "confidence": confidence,
                    }
                )
            )
        return Verdict(self.dimension, verdict, justification, confidence)


# --------------------------------------------------------------- orchestrator


class Orchestrator:
    """Dispatch all four agents for one resource, through projection only.

    Routing is deterministic: every dimension whose manifest declares a
    policy for the resource type is dispatched exactly once. The raw
    resource never reaches an agent.

    ``range_rules`` is optional and defaults to ``None``, which is the
    published behaviour: plausibility goes to the LLM agent. Supplying a rule
    table (from :func:`dqa.ranges.load_ranges`) swaps that one dimension for
    :func:`check_plausibility_ranges`, so the confined system and the
    range-based monolith run the same checking logic and differ only in what
    they may see. Nothing else about routing changes, and the other three
    dimensions are untouched.
    """

    def __init__(
        self,
        manifests: dict[str, Manifest],
        cache_dir: Path | None = None,
        range_rules: dict[str, RangeRule] | None = None,
    ) -> None:
        self.manifests = manifests
        self.range_rules = range_rules
        # Fail before any resource is read if the cache on disk was written
        # under a different prompt or decoding configuration.
        assert_cache_contract(cache_dir)
        self._llm = {
            dim: LLMAgent(dim, cache_dir) for dim in ("plausibility", "consistency")
        }

    @property
    def call_stats(self) -> CallStats:
        """How this orchestrator's model verdicts were obtained, pooled.

        Read after a cell finishes and written into the run output, so a reader
        can tell a clean run from one the network degraded.
        """
        pooled = CallStats()
        for agent in self._llm.values():
            pooled.merge(agent.stats)
        return pooled

    def evaluate(
        self, resource: dict[str, Any]
    ) -> tuple[list[Verdict], list[dict[str, Any]]]:
        """Return (verdicts, projected slices released to agents).

        Stateless per call, so the harness can evaluate resources
        concurrently. The projected slices are what the exposure metric
        scores: they are exactly the inter-agent messages.
        """
        from dqa.manifests import project

        projected_messages: list[dict[str, Any]] = []
        verdicts: list[Verdict] = []

        for dimension, manifest in self.manifests.items():
            sliced = project(resource, manifest)
            if sliced is None:
                continue
            projected_messages.append(sliced["_projected_fields"])

            if dimension == "completeness":
                verdicts.append(check_completeness(sliced))
            elif dimension == "timeliness":
                verdicts.append(check_timeliness(sliced))
            elif dimension == "plausibility" and self.range_rules is not None:
                verdicts.append(check_plausibility_ranges(sliced, self.range_rules))
            elif dimension in self._llm:
                verdicts.append(self._llm[dimension].check(sliced))
            else:
                # Reached only if a manifest set carries a dimension key this
                # module has no agent for. Naming it beats a bare KeyError from
                # inside the dispatch table.
                raise KeyError(
                    f"no agent for dimension {dimension!r}; expected one of "
                    f"completeness, timeliness, plausibility, consistency"
                )

        return verdicts, projected_messages

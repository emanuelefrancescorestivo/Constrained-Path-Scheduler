"""
The constrained scheduler: AO* over the calendar.

What problem this actually is
-----------------------------
The January report called this "A* search". It is not, and the difference is not
pedantic. A review can fail. Choosing a study block leads to *two* successor
states -- recall with probability R, lapse with probability 1 - R -- so the search
space is an AND/OR graph, not a path graph, and what we are looking for is a
*policy* (what to study next given what happened), not a sequence. Running plain
A* over a path graph here means silently solving a different, easier problem in
which memory never fails.

The right algorithm is therefore AO*, Nilsson's heuristic search for AND/OR
graphs. Note what we do *not* need: LAO* exists to handle cycles in the state
graph, and here the block index strictly increases along every edge, so the graph
is acyclic. Bringing in LAO*'s cycle machinery would be weight without benefit.

Three simplifications, stated rather than hidden
-----------------------------------------------
1. **Block positions within a day are fixed by greedy tiling.** The decision the
   scheduler makes is *which topic* gets *which block*, not the minute it starts.
   This is justified rather than convenient: FSRS stability is measured in days,
   so moving a block from 18:00 to 20:00 changes the retrievability at review by
   less than a thousandth. Day-level placement is what matters and that is
   preserved exactly.
2. **The grade on a successful review is Good.** Modelling Hard/Easy would triple
   the branching factor for a second-order effect on stability.
3. **Work left over at the end of the horizon is charged twice**: at the
   unconstrained optimistic rate `V_opt`, plus a lateness penalty per topic that
   is not ready. Both terms are needed. Without any continuation cost the
   cheapest policy is to study nothing -- zero blocks spent, target never
   reached. With only `V_opt`, the policy is *indifferent*: because `V_opt` is the
   fixed point of a dominated Bellman operator, spending a block inside the
   horizon and then paying `V_opt` never beats simply paying `V_opt` at the end,
   so the search again schedules nothing. Measured directly: on the first
   two-topic instance the heuristic at the root equalled the optimum to five
   decimals and the returned plan was empty. The lateness penalty is what makes
   the exam a deadline rather than a label.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Callable, Iterable, Protocol, Sequence, runtime_checkable

from .memory import (
    D_MIN,
    initial_difficulty,
    DEFAULT_WEIGHTS,
    Grade,
    MemoryState,
    Weights,
    retrievability,
    review,
)
from .ssp import MemorizationPolicy
from .timegrid import TimeGrid


@runtime_checkable
class Continuation(Protocol):
    """What an `Instance` needs in order to price work left at the horizon.

    `ssp.MemorizationPolicy` satisfies this, and so does the budgeted
    continuation in `rolling.py`. Naming the contract keeps the admissibility
    guard honest: the only thing an Instance may accept is something that
    declares itself a lower bound.
    """

    @property
    def is_lower_bound(self) -> bool: ...

    def reviews_lower_bound(self, states: Iterable[MemoryState]) -> float: ...


@runtime_checkable
class TimedContinuation(Protocol):
    """A continuation that also needs to know *when* the window ends.

    Once the value function has a clock (`clock.py`), leftover work depends on the
    time elapsed since each topic's last review and the time left before its exam,
    so a memory state alone no longer prices it. `cost_at` is the window's terminal
    value and is an estimate; `bound_at` is the admissible bound a heuristic may
    use. Keeping them as two methods is invariant 1 applied to this object.
    """

    def cost_at(self, topics: Sequence["TopicState"], now: float) -> float: ...

    def bound_at(self, topics: Sequence["TopicState"], now: float) -> float: ...

# --------------------------------------------------------------------------- #
# States
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TopicState:
    """One subject's memory state plus when it was last touched."""

    stability: float
    difficulty: float
    last_review_day: float = 0.0

    def as_memory(self) -> MemoryState:
        return MemoryState(self.stability, self.difficulty)


@dataclass(frozen=True, slots=True)
class PlanState:
    """A node of the search graph. Frozen and hashable, so identical states
    reached by different action sequences collapse into one node."""

    block_index: int
    topics: tuple[TopicState, ...]


@dataclass(frozen=True, slots=True)
class Block:
    """A candidate study block."""

    slot: int
    day: int
    start_day: float  # absolute time in days, for elapsed-time arithmetic


SKIP = -1  # the action of leaving a block unused


# --------------------------------------------------------------------------- #
# Instances
# --------------------------------------------------------------------------- #


def tile_free_time(
    grid: TimeGrid, block_slots: int = 3, max_blocks_per_day: int = 2
) -> tuple[Block, ...]:
    """Greedily cut each day's free time into non-overlapping study blocks.

    Earliest-fit within the day, capped per day to model fatigue. See
    simplification 1 in the module docstring for why fixing the position inside
    the day is harmless here.
    """
    blocks: list[Block] = []
    for day in range(grid.days):
        placed = 0
        slot = day * grid.slots_per_day
        end = slot + grid.slots_per_day
        while slot + block_slots <= end and placed < max_blocks_per_day:
            if grid.is_free(slot, block_slots):
                blocks.append(Block(slot, day, grid.days_from_start(slot)))
                placed += 1
                slot += block_slots
            else:
                slot += 1
    return tuple(blocks)


@dataclass(frozen=True, slots=True)
class Instance:
    """A concrete scheduling problem."""

    topics: tuple[str, ...]
    blocks: tuple[Block, ...]
    initial: PlanState
    target_stability: float
    continuation: Continuation
    lateness_penalty: float
    # State aggregation. Zero disables it. Otherwise successor stabilities are
    # snapped to a multiplicative grid of this relative step and difficulties to
    # `difficulty_step`, so that contingency branches which end up in nearly the
    # same memory state collapse into one node.
    #
    # This is the only remedy for the real scaling wall. A policy for a stochastic
    # problem is a contingency tree whose size is exponential in the number of
    # coin flips along a path, and no heuristic shrinks the solution itself --
    # inflating h only prunes exploration *outside* it, which is why weighting
    # bought a 2x speedup and no change in reach. Aggregation attacks the size of
    # the solution instead. The result is optimal for the aggregated problem, and
    # the gap against the exact answer is measured on instances small enough to
    # solve both ways.
    stability_step: float = 0.0
    difficulty_step: float = 0.0
    weights: Weights = DEFAULT_WEIGHTS
    # Per-topic stability targets and exam days (days from the start of the
    # horizon). Empty means every topic uses `target_stability` and has no exam
    # inside the horizon, which is the one-shot solver's original setting. A topic
    # cannot be studied in a block that starts at or after its exam.
    targets: tuple[float, ...] = ()
    exam_days: tuple[float, ...] = ()
    # When the window's terminal state is evaluated, in days from the start of the
    # horizon: the start of the first block after the window. Only a
    # TimedContinuation reads it.
    end_day: float | None = None

    def target_of(self, index: int) -> float:
        return self.targets[index] if self.targets else self.target_stability

    def is_ready(self, index: int, topic: "TopicState") -> bool:
        return topic.stability >= self.target_of(index)

    @classmethod
    def build(
        cls,
        grid: TimeGrid,
        topics: Sequence[tuple[str, MemoryState]],
        target_stability: float,
        continuation: Continuation,
        block_slots: int = 3,
        max_blocks_per_day: int = 2,
        lateness_penalty: float | None = None,
        stability_step: float = 0.0,
        difficulty_step: float = 0.0,
        weights: Weights = DEFAULT_WEIGHTS,
    ) -> "Instance":
        if not getattr(continuation, "is_lower_bound", False):
            raise ValueError(
                "continuation must be an optimistic (lower-bounding) solve; "
                "use SSPConfig.for_heuristic"
            )
        blocks = tile_free_time(grid, block_slots, max_blocks_per_day)
        # Default penalty: the number of blocks in the horizon. Since no policy
        # can spend more than that, charging one horizon's worth of blocks per
        # unready topic makes readiness dominate block-thrift lexicographically,
        # while still minimising blocks among policies that get ready. It is a
        # construction rather than a tuned constant, which is why it is derived
        # from the instance instead of guessed.
        #
        # Caveat that bit once already: because it depends on the instance, two
        # instances built with the default do NOT share an objective, and their
        # costs are not comparable. Pin it explicitly for any A/B comparison.
        if lateness_penalty is None:
            lateness_penalty = float(len(blocks))
        initial = PlanState(
            0, tuple(TopicState(m.stability, m.difficulty, 0.0) for _, m in topics)
        )
        return cls(
            topics=tuple(name for name, _ in topics),
            blocks=blocks,
            initial=initial,
            target_stability=target_stability,
            continuation=continuation,
            lateness_penalty=lateness_penalty,
            stability_step=stability_step,
            difficulty_step=difficulty_step,
            weights=weights,
        )

    # -- dynamics ------------------------------------------------------------ #

    def snap(self, state: MemoryState) -> MemoryState:
        """Round a memory state onto the aggregation grid, if one is configured."""
        if self.stability_step <= 0.0 and self.difficulty_step <= 0.0:
            return state
        stability = state.stability
        if self.stability_step > 0.0:
            step = math.log1p(self.stability_step)
            stability = math.exp(round(math.log(stability) / step) * step)
        difficulty = state.difficulty
        if self.difficulty_step > 0.0:
            difficulty = round(difficulty / self.difficulty_step) * self.difficulty_step
        return MemoryState(max(stability, 0.01), min(max(difficulty, 1.0), 10.0))

    def is_done(self, state: PlanState) -> bool:
        return all(self.is_ready(i, t) for i, t in enumerate(state.topics))

    def is_terminal(self, state: PlanState) -> bool:
        return state.block_index >= len(self.blocks) or self.is_done(state)

    def terminal_cost(self, state: PlanState) -> float:
        """Cost of arriving at the deadline in this state.

        Two terms: the blocks still owed, priced at the unconstrained optimistic
        rate, plus `lateness_penalty` for each topic that is not ready. Keeping
        the `V_opt` term is what preserves admissibility of `h = sum V_opt`
        (the penalty term is non-negative, so it can only push the true cost up);
        adding the penalty is what stops the search from deferring everything.
        """
        if isinstance(self.continuation, TimedContinuation):
            if self.end_day is None:
                raise ValueError("a TimedContinuation needs Instance.end_day")
            owed = self.continuation.cost_at(state.topics, self.end_day)
        else:
            owed = self.continuation.reviews_lower_bound(t.as_memory() for t in state.topics)
        return owed + self.lateness_penalty * self.unready_count(state)

    def unready_count(self, state: PlanState) -> int:
        return sum(1 for i, t in enumerate(state.topics) if not self.is_ready(i, t))

    def actions(self, state: PlanState) -> tuple[int, ...]:
        """Which topic to study in this block, or SKIP.

        Topics already at target are dropped: reviewing them cannot help and only
        inflates the branching factor.
        """
        now = self.blocks[state.block_index].start_day
        live = tuple(
            i
            for i, t in enumerate(state.topics)
            if not self.is_ready(i, t) and (not self.exam_days or now < self.exam_days[i])
        )
        return live + (SKIP,)

    def successors(self, state: PlanState, action: int) -> tuple[float, tuple[tuple[float, PlanState], ...]]:
        """(immediate cost, [(probability, successor), ...])."""
        nxt_index = state.block_index + 1
        if action == SKIP:
            return 0.0, ((1.0, PlanState(nxt_index, state.topics)),)

        block = self.blocks[state.block_index]
        topic = state.topics[action]
        elapsed = max(block.start_day - topic.last_review_day, 0.0)
        r = retrievability(elapsed, topic.stability)

        target = self.target_of(action)
        outcomes = []
        for prob, grade in ((r, Grade.GOOD), (1.0 - r, Grade.AGAIN)):
            if prob <= 0.0:
                continue
            exact = review(topic.as_memory(), elapsed, grade, self.weights)
            after = self.snap(exact)
            # Aggregation may move a state, but never across the goal: a snapped
            # 8.85 became 9.36 against a target of 9, and the search then bought a
            # review at recall 0.999 believing it finished the topic (AUDIT.md
            # item 26). Goal membership is decided by the exact state.
            if (exact.stability >= target) != (after.stability >= target):
                edge = target if exact.stability >= target else math.nextafter(target, 0.0)
                after = MemoryState(edge, after.difficulty)
            topics = list(state.topics)
            topics[action] = TopicState(after.stability, after.difficulty, block.start_day)
            outcomes.append((prob, PlanState(nxt_index, tuple(topics))))
        return 1.0, tuple(outcomes)


# --------------------------------------------------------------------------- #
# Heuristics
# --------------------------------------------------------------------------- #

Heuristic = Callable[[Instance, PlanState], float]


def zero_heuristic(instance: Instance, state: PlanState) -> float:
    """h = 0. Turns AO* into uninformed expectimax; the control condition."""
    return 0.0


def best_case_reviews(
    stability: float,
    difficulty: float,
    target: float,
    horizon_days: float,
    weights: Weights = DEFAULT_WEIGHTS,
    cap: int = 200,
) -> int:
    """Fewest reviews that could conceivably take `stability` to `target`.

    A hard lower bound with no discretisation caveat attached, obtained by taking
    the best case on every term of the stability update at once. Two of those
    terms can be tightened while staying provable, and the tightening matters: the
    naive version assumed difficulty 1 and retrievability 0, which claims a single
    review multiplies stability by 88 and makes the bound useless.

    **Difficulty.** With grades restricted to Good and Again, D is pulled toward
    D0(Good) = 5.16 from both sides -- Good maps D to 0.16 + 0.969*D, which
    increases D below the fixed point and decreases it above -- and Again only
    pushes it up. So `min(D, D0(Good))` is a valid floor on difficulty for the
    whole future, rather than the global floor of 1.

    **Timing.** The spacing term `exp(w10*(1-R)) - 1` is maximised as R goes to 0,
    which needs an unbounded delay. The calendar does not have one: no gap can
    exceed the days left before the last usable block. Evaluating the term at
    `R = retrievability(horizon, S)` is therefore still generous -- every single
    gap is bounded by the whole horizon -- but far tighter.

    Lapses cannot beat this either: a lapse never ends above a successful recall
    from the same state after the same delay, and it raises difficulty
    (`test_a_lapse_never_beats_a_recall`, AUDIT.md item 27). So no policy, constrained or not, reaches the target in fewer
    reviews than the count returned here.
    """
    floor = min(difficulty, initial_difficulty(Grade.GOOD, weights))
    horizon = max(horizon_days, 0.0)
    s = stability
    for n in range(cap):
        if s >= target:
            return n
        r_min = retrievability(horizon, s) if horizon > 0 else 1.0
        growth = 1.0 + (
            math.exp(weights.sinc_scale)
            * (11.0 - floor)
            * s ** (-weights.sinc_s_decay)
            * (math.exp((1.0 - r_min) * weights.sinc_r_gain) - 1.0)
        )
        if growth <= 1.0 + 1e-12:
            return cap  # unreachable within this horizon
        s *= growth
    return cap


def _topic_horizon(instance: "Instance", state: PlanState, topic: TopicState) -> float:
    """Longest gap still available to this topic before the last usable block."""
    if state.block_index >= len(instance.blocks):
        return 0.0
    return max(instance.blocks[-1].start_day - topic.last_review_day, 0.0)


def _deadline_bound(instance: "Instance", state: PlanState) -> float:
    """Lower bound on blocks spent plus lateness penalties, by a knapsack argument.

    Any policy finishes some subset F of the live topics. Each topic in F costs at
    least `best_case_reviews` blocks and those blocks are disjoint; each topic
    outside F costs at least one lateness penalty. So with `m` blocks left:

        bound = min over feasible F of [ sum_{i in F} k_i + penalty * (n - |F|) ]

    Sorting the counts makes the inner minimisation trivial: the cheapest way to
    finish exactly `j` topics is to finish the `j` with the smallest counts, and
    that is feasible only while the running total fits in `m`.

    An earlier version simply summed the counts, which is *not* a lower bound on
    cost: a topic that cannot be finished within the horizon costs one penalty,
    not two hundred blocks. The admissibility test caught it -- `h = 400` against
    an optimum of `12.38` at a terminal state.
    """
    remaining = len(instance.blocks) - state.block_index
    needs = sorted(
        best_case_reviews(
            t.stability,
            t.difficulty,
            instance.target_of(i),
            _topic_horizon(instance, state, t),
            instance.weights,
        )
        for i, t in enumerate(state.topics)
        if not instance.is_ready(i, t)
    )
    live = len(needs)
    best = instance.lateness_penalty * live  # abandon everything
    running = 0
    for finished, need in enumerate(needs, start=1):
        running += need
        if running > remaining:
            break
        best = min(best, running + instance.lateness_penalty * (live - finished))
    return float(best)


def closed_form_heuristic(instance: Instance, state: PlanState) -> float:
    """The bound that needs no discretisation argument at all: see `_deadline_bound`."""
    return _deadline_bound(instance, state)


def ssp_heuristic(instance: Instance, state: PlanState) -> float:
    """h = sum of per-topic optimistic V*. Much better informed; see METHOD.md."""
    return instance.continuation.reviews_lower_bound(t.as_memory() for t in state.topics)


# --------------------------------------------------------------------------- #
# Exact reference: full expectimax with memoisation
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Solution:
    value: float
    nodes: int
    policy: dict[PlanState, int]
    # Populated only by `solve_exact`. Having the optimal value of *every*
    # reachable state, not just the root, is what lets the tests check
    # admissibility across the whole state space instead of at one point.
    values: dict[PlanState, float] = field(default_factory=dict)

    def trajectory(self, instance: Instance, assume_recall: bool = True) -> list[tuple[Block, str]]:
        """One realised plan, for display.

        A policy is a contingency tree, so it cannot be printed as a calendar.
        This walks the single branch where every review succeeds -- the modal
        outcome, and the one a student would see about half the time with three
        reviews at 0.9. It is a slice of the solution, not the solution.
        """
        out: list[tuple[Block, str]] = []
        state = instance.initial
        while not instance.is_terminal(state):
            action = self.policy.get(state)
            if action is None:
                break
            block = instance.blocks[state.block_index]
            if action != SKIP:
                out.append((block, instance.topics[action]))
            _, outcomes = instance.successors(state, action)
            state = max(outcomes, key=lambda po: po[0])[1] if assume_recall else outcomes[-1][1]
        return out


def solve_exact(instance: Instance) -> Solution:
    """Backward induction over the whole reachable state space.

    This is the reference optimum for the *constrained* problem. It is only
    tractable on small instances, which is precisely the point: on those we can
    check that AO* returns the same number, and then trust AO* where exhaustive
    search is out of reach.
    """
    memo: dict[PlanState, float] = {}
    policy: dict[PlanState, int] = {}

    def value_of(state: PlanState) -> float:
        cached = memo.get(state)
        if cached is not None:
            return cached
        if instance.is_terminal(state):
            memo[state] = instance.terminal_cost(state)
            return memo[state]
        best, best_action = math.inf, SKIP
        for action in instance.actions(state):
            cost, outcomes = instance.successors(state, action)
            total = cost + sum(p * value_of(nxt) for p, nxt in outcomes)
            if total < best:
                best, best_action = total, action
        memo[state] = best
        policy[state] = best_action
        return best

    value = value_of(instance.initial)
    return Solution(value=value, nodes=len(memo), policy=policy, values=memo)


# --------------------------------------------------------------------------- #
# AO*
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Node:
    state: PlanState
    value: float
    terminal: bool
    solved: bool = False
    expanded: bool = False
    best_action: int = SKIP
    children: dict[int, tuple[float, tuple[tuple[float, "_Node"], ...]]] = field(default_factory=dict)
    parents: list["_Node"] = field(default_factory=list)


def evaluate_policy(instance: Instance, solution: "Solution") -> float:
    """Exact expected cost of *following* a given policy, with no minimisation.

    Needed because a weighted run inflates the heuristic, so the value AO*
    reports at the root is no longer the cost of the plan it returns. Quoting
    that number as the plan's cost is the same category of error as the January
    report's, and this function is how the real cost gets measured instead.
    """
    memo: dict[PlanState, float] = {}

    def rec(state: PlanState) -> float:
        cached = memo.get(state)
        if cached is not None:
            return cached
        if instance.is_terminal(state):
            value = instance.terminal_cost(state)
        else:
            action = solution.policy.get(state)
            if action is None:
                value = instance.terminal_cost(state)
            else:
                cost, outcomes = instance.successors(state, action)
                value = cost + sum(p * rec(nxt) for p, nxt in outcomes)
        memo[state] = value
        return value

    return rec(instance.initial)


def evaluate_exact_dynamics(instance: Instance, solution: "Solution") -> float:
    """Expected cost of following an aggregated policy in the real, unsnapped world.

    `evaluate_policy` follows the policy with the instance's own transitions, so on
    an aggregated instance it reports the cost in the aggregated model. That is
    only the true cost if aggregation is harmless, which is what needs checking.
    Here the decisions are still looked up by the aggregated state, as the planner
    would make them, but the memory state that pays the terminal cost evolves with
    exact FSRS transitions and the exact recall probabilities.

    It exposed AUDIT.md item 26: the ten-day plan in `demo.py` was reported at 8.83
    blocks and costs 28.60 when followed with exact dynamics
    (`benchmarks/aggregation_goal_crossing.py`).
    """
    exact = replace(instance, stability_step=0.0, difficulty_step=0.0)

    def rec(snapped: PlanState, real: PlanState) -> float:
        if exact.is_terminal(real) or instance.is_terminal(snapped):
            return exact.terminal_cost(real)
        action = solution.policy.get(snapped)
        if action is None:
            return exact.terminal_cost(real)
        cost, snapped_outcomes = instance.successors(snapped, action)
        _, real_outcomes = exact.successors(real, action)
        if len(snapped_outcomes) != len(real_outcomes):
            raise RuntimeError("aggregated and exact outcomes disagree on which grades are possible")
        # Both lists are ordered recall, then lapse; probabilities come from the real state.
        return cost + sum(
            p * rec(s_next, r_next)
            for (_, s_next), (p, r_next) in zip(snapped_outcomes, real_outcomes)
        )

    return rec(instance.initial, instance.initial)


def solve_ao_star(
    instance: Instance,
    heuristic: Heuristic = ssp_heuristic,
    max_expansions: int = 2_000_000,
    weight: float = 1.0,
) -> Solution:
    """AO* on the acyclic AND/OR graph induced by the calendar.

    The loop is Nilsson's: trace the current best partial solution graph down to
    an unexpanded tip, expand it, then revise values bottom-up and re-mark the
    best action at every ancestor. Because block indices strictly increase,
    revision can be ordered by block index descending and no cycle detection is
    needed.

    With an admissible heuristic the value returned is exactly optimal, which
    `tests/test_plan.py` checks against `solve_exact` rather than assuming.
    """
    if weight < 1.0:
        raise ValueError("weight must be >= 1; below 1 the search loses its guarantee")

    nodes: dict[PlanState, _Node] = {}

    def node_for(state: PlanState) -> _Node:
        existing = nodes.get(state)
        if existing is not None:
            return existing
        terminal = instance.is_terminal(state)
        value = (
            instance.terminal_cost(state) if terminal else weight * heuristic(instance, state)
        )
        created = _Node(state=state, value=value, terminal=terminal, solved=terminal)
        nodes[state] = created
        return created

    root = node_for(instance.initial)
    expansions = 0

    def find_tip(node: _Node) -> _Node | None:
        """An unexpanded non-terminal node in the marked solution graph.

        The `solved` test is not a micro-optimisation. Without it the descent
        re-walks every already-finished subgraph on every iteration, making the
        whole search quadratic in the number of expansions -- which is what made
        the first version of this function hang on a fourteen-block instance
        rather than merely be slow.
        """
        if node.solved or node.terminal:
            return None
        if not node.expanded:
            return node
        for _, child in node.children[node.best_action][1]:
            found = find_tip(child)
            if found is not None:
                return found
        return None

    def backup(node: _Node) -> None:
        """Recompute value and best action; True if either changed."""
        best, best_action, best_solved = math.inf, SKIP, False
        for action, (cost, outcomes) in node.children.items():
            total = cost + sum(p * child.value for p, child in outcomes)
            if total < best:
                best, best_action = total, action
                best_solved = all(child.solved for _, child in outcomes)
        node.value, node.best_action, node.solved = best, best_action, best_solved

    while not root.solved:
        tip = find_tip(root)
        if tip is None:
            break
        if expansions >= max_expansions:
            raise RuntimeError(f"AO* exceeded {max_expansions} expansions without solving")

        for action in instance.actions(tip.state):
            cost, outcomes = instance.successors(tip.state, action)
            children = tuple((p, node_for(nxt)) for p, nxt in outcomes)
            for _, child in children:
                if tip not in child.parents:
                    child.parents.append(tip)
            # The cost is cached alongside the children: recomputing it during
            # every bottom-up revision meant re-running the FSRS transition maths
            # thousands of times for a number that never changes.
            tip.children[action] = (cost, children)
        tip.expanded = True
        expansions += 1

        # Revise bottom-up. Parents always have a strictly smaller block index,
        # so processing by descending index is a valid topological order.
        pending = {tip.state: tip}
        while pending:
            current = max(pending.values(), key=lambda n: n.state.block_index)
            del pending[current.state]
            before = (current.value, current.solved)
            backup(current)
            if (current.value, current.solved) != before:
                for parent in current.parents:
                    pending[parent.state] = parent

    policy = {n.state: n.best_action for n in nodes.values() if n.expanded}
    return Solution(value=root.value, nodes=expansions, policy=policy)


def capacity_heuristic(instance: Instance, state: PlanState) -> float:
    """The informed heuristic: the better of the two independent bounds.

    `ssp_heuristic` bounds blocks-spent-plus-continuation via Bellman on V_opt.
    `_deadline_bound` bounds blocks-spent-plus-penalty via the knapsack argument.
    Both are valid lower bounds on the total, and both include the blocks term, so
    they cannot be added -- the maximum is what is safe.

    Taking the max matters: on a deadline instance the cost is dominated by the
    penalty, which V_opt knows nothing about. Measured at the root of the
    seven-block instance, the SSP bound alone gave 3.23 against an optimum of
    11.81 and barely beat h = 0; the combination reduced expansions from 2,289 to
    305.
    """
    owed = instance.continuation.reviews_lower_bound(t.as_memory() for t in state.topics)
    return max(owed, _deadline_bound(instance, state))

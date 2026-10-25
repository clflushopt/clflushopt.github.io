+++
title = "Join Ordering, Part 2: DPsize, DPsub and DPccp"
description = "Exploring early enumeration approaches."
date = 2026-09-27

tags = ["databases", "algorithms"]
+++

*This is Part 2 of the join ordering series, if you haven't read [Part 1](@/posts/join-ordering.md) start there.*

We ended Part 1 with the core problem, the data structures we need and what an optimal algorithm looks like, one that
spends constant time per [csg-cmp-pair](@/posts/join-ordering.md#the-csg-cmp-pair) and doesn't enumerate anything else.
This part covers DPsize, DPsub and DPccp from the VLDB 2006 paper. They all fill the same DP table (`DpTable` from
Part 1) with the cheapest plan producing ⋈S for every connected set S of relations. They differ *only* in how they enumerate
candidate splits (S₁, S₂), and therefore in how much work they waste on splits that fail the csg-cmp-pair conditions.

The examples in this part use the three relations from Part 1, the chain R₀ — R₂ — R₁ with its four csg-cmp-pairs.

<details>
<summary>Code from Part 1</summary>

```rust
/// A set of relations, represented as a 64-bit bitset.
#[derive(Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Debug)]
pub struct NodeSet(pub u64);

impl NodeSet {
    pub const EMPTY: NodeSet = NodeSet(0);

    pub fn single(i: usize) -> NodeSet { NodeSet(1u64 << i) }
    /// {v_0, ..., v_i} — the "B_i" prefix set from the DPccp paper.
    pub fn prefix(i: usize) -> NodeSet { NodeSet((1u64 << (i + 1)) - 1) }
    pub fn full(n: usize) -> NodeSet {
        NodeSet(if n == 64 { !0 } else { (1u64 << n) - 1 })
    }
    pub fn is_empty(self) -> bool { self.0 == 0 }
    pub fn contains(self, i: usize) -> bool { self.0 & (1u64 << i) != 0 }
    pub fn is_subset_of(self, other: NodeSet) -> bool { self.0 & !other.0 == 0 }
    pub fn intersects(self, other: NodeSet) -> bool { self.0 & other.0 != 0 }
    pub fn union(self, other: NodeSet) -> NodeSet { NodeSet(self.0 | other.0) }
    pub fn intersect(self, other: NodeSet) -> NodeSet { NodeSet(self.0 & other.0) }
    pub fn minus(self, other: NodeSet) -> NodeSet { NodeSet(self.0 & !other.0) }
    pub fn len(self) -> u32 { self.0.count_ones() }

    /// Index of the minimal element.
    pub fn min_elem(self) -> usize {
        debug_assert!(!self.is_empty());
        self.0.trailing_zeros() as usize
    }

    /// Iterate over the elements (ascending).
    pub fn iter(self) -> impl Iterator<Item = usize> {
        let mut bits = self.0;
        std::iter::from_fn(move || {
            if bits == 0 { None } else {
                let i = bits.trailing_zeros() as usize;
                bits &= bits - 1; // clear lowest set bit
                Some(i)
            }
        })
    }

    /// Iterate over all NON-EMPTY subsets of `self`, in ASCENDING numeric
    /// order. Since A ⊂ B implies A < B numerically, this guarantees that
    /// every subset is produced before any of its supersets — a property the
    /// DP algorithms rely on (subplans must exist before they are combined).
    /// Trick: sub = (sub - mask) & mask walks all submasks ascending.
    pub fn nonempty_subsets(self) -> impl Iterator<Item = NodeSet> {
        let mask = self.0;
        let mut sub = 0u64;
        let mut done = mask == 0;
        std::iter::from_fn(move || {
            if done { return None; }
            sub = sub.wrapping_sub(mask) & mask;
            if sub == mask { done = true; }
            Some(NodeSet(sub))
        })
    }
}
```

```rust
/// A (simple) query graph: relations are nodes, binary join predicates are
/// edges annotated with a selectivity.
#[derive(Clone, Debug)]
pub struct QueryGraph {
    pub n: usize,
    /// cardinalities |R_i|
    pub card: Vec<f64>,
    /// adjacency[i] = set of neighbors of node i
    pub adjacency: Vec<NodeSet>,
    /// edges (u, v, selectivity) with u < v
    pub edges: Vec<(usize, usize, f64)>,
}

impl QueryGraph {
    pub fn new(card: Vec<f64>) -> QueryGraph {
        let n = card.len();
        QueryGraph { n, card, adjacency: vec![NodeSet::EMPTY; n], edges: vec![] }
    }
    pub fn add_edge(&mut self, u: usize, v: usize, sel: f64) {
        let (u, v) = if u < v { (u, v) } else { (v, u) };
        self.adjacency[u] = self.adjacency[u].union(NodeSet::single(v));
        self.adjacency[v] = self.adjacency[v].union(NodeSet::single(u));
        self.edges.push((u, v, sel));
    }

    /// Neighborhood of a set S: all nodes adjacent to S, excluding S itself
    /// and an exclusion set X.
    pub fn neighbors(&self, s: NodeSet, x: NodeSet) -> NodeSet {
        let mut acc = NodeSet::EMPTY;
        for v in s.iter() {
            acc = acc.union(self.adjacency[v]);
        }
        acc.minus(s).minus(x)
    }

    /// Is the subgraph induced by S connected? (BFS over bitsets)
    pub fn is_connected(&self, s: NodeSet) -> bool {
        if s.is_empty() { return false; }
        let mut reach = NodeSet::single(s.min_elem());
        loop {
            let grown = reach.union(self.neighbors(reach, NodeSet::EMPTY).intersect(s));
            if grown == reach { break; }
            reach = grown;
        }
        reach == s
    }

    /// Is there at least one edge with one endpoint in S1 and the other in S2?
    pub fn connected_pair(&self, s1: NodeSet, s2: NodeSet) -> bool {
        !self.neighbors(s1, NodeSet::EMPTY).intersect(s2).is_empty()
    }

    /// Estimated cardinality of the join of all relations in S:
    /// |⋈ S| = ∏ card(R_i) · ∏ sel(e) over edges internal to S.
    pub fn cardinality(&self, s: NodeSet) -> f64 {
        let mut c = 1.0;
        for v in s.iter() { c *= self.card[v]; }
        for &(u, v, sel) in &self.edges {
            if s.contains(u) && s.contains(v) { c *= sel; }
        }
        c
    }
}
```

```rust
use std::collections::HashMap;

#[derive(Clone, Debug)]
pub enum Plan {
    Leaf(usize),
    Join(Box<Plan>, Box<Plan>),
}

#[derive(Clone, Debug)]
pub struct Entry {
    pub set: NodeSet,
    pub card: f64,
    pub cost: f64,
    pub plan: Plan,
}

pub type DpTable = HashMap<NodeSet, Entry>;

/// The cost of a join is the sum of the costs of its inputs plus the
/// cardinality of its output. Leaves cost 0.
pub fn combine(graph: &QueryGraph, left: &Entry, right: &Entry) -> Entry {
    let set = left.set.union(right.set);
    let card = graph.cardinality(set);
    Entry {
        set,
        card,
        cost: left.cost + right.cost + card,
        plan: Plan::Join(Box::new(left.plan.clone()), Box::new(right.plan.clone())),
    }
}

fn seed_leaves(graph: &QueryGraph) -> DpTable {
    let mut t = DpTable::new();
    for i in 0..graph.n {
        let s = NodeSet::single(i);
        t.insert(s, Entry { set: s, card: graph.card[i], cost: 0.0, plan: Plan::Leaf(i) });
    }
    t
}

fn consider(table: &mut DpTable, cand: Entry) {
    match table.get(&cand.set) {
        Some(old) if old.cost <= cand.cost => {}
        _ => { table.insert(cand.set, cand); }
    }
}
```

```rust
fn chain(n: usize) -> QueryGraph {
    let mut g = QueryGraph::new(vec![100.0; n]);
    for i in 0..n - 1 { g.add_edge(i, i + 1, 0.1); }
    g
}

fn cycle(n: usize) -> QueryGraph {
    let mut g = chain(n);
    g.add_edge(n - 1, 0, 0.1);
    g
}

fn star(n: usize) -> QueryGraph {
    let mut g = QueryGraph::new(vec![100.0; n]);
    for i in 1..n { g.add_edge(0, i, 0.1); }
    g
}

fn clique(n: usize) -> QueryGraph {
    let mut g = QueryGraph::new(vec![100.0; n]);
    for i in 0..n {
        for j in i + 1..n { g.add_edge(i, j, 0.1); }
    }
    g
}
```

```rust
/// Builds a set from a list of relation indices.
fn set(rels: &[usize]) -> NodeSet {
    rels.iter().fold(NodeSet::EMPTY, |acc, &i| acc.union(NodeSet::single(i)))
}

/// The three relations from the beginning: |R₀| = |R₁| = 1,000,000 and
/// |R₂| = 10, joined as the chain R₀ — R₂ — R₁.
fn example() -> QueryGraph {
    let mut g = QueryGraph::new(vec![1e6, 1e6, 10.0]);
    g.add_edge(0, 2, 1e-6);
    g.add_edge(2, 1, 1e-6);
    g
}
```

</details>

## DPsize: enumerate by result size

DPsize is System R's strategy generalized to bushy plans: build all plans of size 2, then size 3, and so on; a plan of
size k is formed from plans of sizes k₁ + k₂ = k. Its candidate space is *pairs of already-built plans with compatible
sizes*, and each candidate is then filtered by the disjointness and connectedness tests.

```rust
/// Returns (table, number of inner-loop pairs inspected).
pub fn dpsize(graph: &QueryGraph) -> (DpTable, u64) {
    let n = graph.n;
    let mut table = seed_leaves(graph);
    // plans grouped by size
    let mut by_size: Vec<Vec<NodeSet>> = vec![vec![]; n + 1];
    for i in 0..n {
        by_size[1].push(NodeSet::single(i));
    }
    let mut pairs_inspected = 0u64;
    for sz in 2..=n {
        for sz1 in 1..sz {
            let sz2 = sz - sz1;
            // avoid enumerating each unordered pair twice
            if sz1 > sz2 { continue; }
            let sets1 = by_size[sz1].clone();
            let sets2 = by_size[sz2].clone();
            for &s1 in &sets1 {
                for &s2 in &sets2 {
                    if sz1 == sz2 && s1.0 >= s2.0 { continue; }
                    pairs_inspected += 1;
                    if s1.intersects(s2) {
                        continue; // not disjoint
                    }
                    if !graph.connected_pair(s1, s2) {
                        continue; // no join predicate applies => cross product
                    }
                    let cand = combine(graph, &table[&s1], &table[&s2]);
                    if !table.contains_key(&cand.set) {
                        by_size[sz].push(cand.set);
                    }
                    consider(&mut table, cand);
                }
            }
        }
    }
    (table, pairs_inspected)
}
```

The failure mode is visible in how the loop is structured, the pair (s1, s2) is generated *before* the disjointness
test, so DPsize inspects every pair of connected sets with compatible sizes, including all the overlapping ones. The
VLDB 2006 analysis quantifies this: on chains DPsize inspects Θ(n⁴) pairs against Θ(n³) csg-cmp-pairs (tolerable), but
on cliques the inspected pairs grow like ~4ⁿ against ~3ⁿ/2 csg-cmp-pairs, so asymptotically it inspects ≈ (4/3)ⁿ
candidates per useful pair.

For the chain from Part 1 DPsize inspects 9 pairs to find the 4 csg-cmp-pairs. At size 2 it inspects the pairs of single
relations and drops ({R₀}, {R₁}) because there is no edge that connects them. At size 3 it pairs each of the three
single relations with each of the two connected pairs {R₀, R₂} and {R₂, R₁}, and four of those six overlap.

## DPsub: enumerate by subset

DPsub iterates the *sets* in ascending numeric order (which, as we saw in
[Part 1](@/posts/join-ordering.md#submask-enumeration), refines the subset order: every S₁ ⊂ S was completed before S)
and splits each connected S into all subset/complement pairs:

```rust
/// Returns (table, number of subset splits inspected).
pub fn dpsub(graph: &QueryGraph) -> (DpTable, u64) {
    let n = graph.n;
    let mut table = seed_leaves(graph);
    let mut splits_inspected = 0u64;
    for bits in 1u64..(1u64 << n) {
        let s = NodeSet(bits);
        if s.len() < 2 || !graph.is_connected(s) {
            continue;
        }
        // enumerate proper non-empty subsets S1 that contain min(S) so each
        // unordered split {S1, S2} is inspected exactly once
        let lowest = NodeSet::single(s.min_elem());
        let rest = s.minus(lowest);
        // submasks of `rest` unioned with `lowest` give all S1 containing min(S)
        let mut sub = rest.0;
        loop {
            let s1 = NodeSet(sub | lowest.0);
            let s2 = s.minus(s1);
            if !s2.is_empty() {
                splits_inspected += 1;
                if graph.is_connected(s1)
                    && graph.is_connected(s2)
                    && graph.connected_pair(s1, s2)
                {
                    let cand = combine(graph, &table[&s1], &table[&s2]);
                    consider(&mut table, cand);
                }
            }
            if sub == 0 { break; }
            sub = (sub - 1) & rest.0;
        }
    }
    (table, splits_inspected)
}
```

The `min_elem` call in there compiles down to a single [`tzcnt`](https://www.felixcloutier.com/x86/tzcnt) and lets an
algorithm pick a canonical node out of a set. DPsub uses it to break the symmetry of a split S = S₁ ∪ S₂ by forcing
min(S) into S₁, so each unordered split is only seen once. DPccp and DPhyp use the same approach to impose a global
order on subgraphs, they only extend a subgraph towards larger node indices, and that rule is what keeps them from
enumerating the same pair twice.

DPsub's waste is dual to DPsize's: it never inspects overlapping pairs (splits are partitions by construction), but it
inspects every split of every connected set, including the many whose halves are disconnected. On a clique that is
fine, *every* split of every subset is a csg-cmp-pair and DPsub wastes nothing there (Θ(3ⁿ) splits, matching `#ccp`). On
a chain almost all of its work is wasted, it does Ω(2ⁿ) work (it must at least skip all subsets) while only Θ(n³)
csg-cmp-pairs exist.

With the same chain DPsub inspects 5 splits, one each for {R₀, R₂} and {R₂, R₁} and three for {R₀, R₁, R₂}, where
({R₀, R₁}, {R₂}) fails because {R₀, R₁} isn't connected. That's only one wasted split, but the example is tiny. Here
is what happens on larger queries:

```text
chain  n=10:  DPsize inspects   1,135 pairs   DPsub inspects    1,981 splits   #ccp =    165
star   n=10:  DPsize inspects  57,888 pairs   DPsub inspects   19,171 splits   #ccp =  2,304
clique n=10:  DPsize inspects 306,991 pairs   DPsub inspects   28,501 splits   #ccp = 28,501
chain  n=15:  DPsize inspects   5,628 pairs   DPsub inspects   65,399 splits   #ccp =    560
chain  n=20:  DPsize inspects  17,545 pairs   DPsub inspects 2,096,920 splits  #ccp =  1,330
```

DPsub's splits match `#ccp` on the 10-clique and pass 2 million for the 1,330 pairs of the 20-chain. DPsize is the
other way around, on chains it stays within a small factor of the useful work but on the 10-clique it inspects about 11
times more pairs than it needs. Each of them does well where the other one does badly. What we want is an algorithm
that only looks at csg-cmp-pairs regardless of the shape of the graph, which is what DPccp does.

## DPccp: enumerate exactly the csg-cmp-pairs

DPccp comes at the problem from the other direction. DPsize and DPsub generate candidate pairs and then throw away the
ones that fail the csg-cmp-pair conditions, DPccp instead walks the query graph and only emits pairs that pass. It
builds connected subgraphs one at a time and for each one the connected complements that share an edge with it, so it
emits every csg-cmp-pair once. The DP table is filled along the way, each time a pair is emitted its two halves are
joined and the result is kept if it's the cheapest plan seen for that set. In the paper each pair costs
constant time to produce, the code below gives up a bit of that for readability and Part 6 goes through what a
production version does differently.

To avoid emitting a pair twice DPccp numbers the nodes v₀ … vₙ₋₁ (breadth-first, see below) and makes every connected
subgraph S belong to its minimal node: the enumeration starts a subgraph at each node vᵢ and never adds a node with
index < i to it, the prohibited set includes the prefix Bᵢ = {v₀..vᵢ}. Each S is therefore started from one seed,
min(S).

A subgraph is extended through its neighborhood. From a current connected set S with prohibited set X, compute the
neighborhood N = neighbors(S) \ X. Every S ∪ S′ for non-empty S′ ⊆ N is a *new, connected* superset, emit each. Then
recurse from each S ∪ S′ with prohibited set X ∪ N: whatever the recursion adds must come from *outside* N, so the same
final set can never be reached via two different subsets of N. Each connected S is emitted exactly once. (This is
`EnumerateCsgRec`.)

For the complements, the same machinery runs again, seeded from each neighbor v of S₁ (in descending index order), with
the prohibited set now containing B<sub>min(S₁)</sub> ∪ S₁ (complements must have min(S₂) > min(S₁), so each unordered
pair is emitted once) plus the already-tried smaller neighbors (Bᵥ ∩ N, so complements around different seed neighbors
cannot collide).

```rust
/// A statistics struct so we can inspect how much work DPccp does.
#[derive(Default, Debug)]
pub struct CcpStats {
    pub ccps_emitted: u64,
    /// every emitted pair, normalized, for verification in tests
    pub pairs: Vec<(u64, u64)>,
}

/// DPccp: dynamic programming driven by enumeration of
/// csg-cmp-pairs (connected subgraph / connected complement pairs).
pub fn dpccp(graph: &QueryGraph) -> (DpTable, CcpStats) {
    // Re-number in BFS order.
    let n = graph.n;
    let mut order = Vec::with_capacity(n);
    let mut seen = NodeSet::EMPTY;
    let mut queue = std::collections::VecDeque::new();
    for start in 0..n {
        if seen.contains(start) { continue; }
        queue.push_back(start);
        seen = seen.union(NodeSet::single(start));
        while let Some(v) = queue.pop_front() {
            order.push(v);
            for w in graph.adjacency[v].iter() {
                if !seen.contains(w) {
                    seen = seen.union(NodeSet::single(w));
                    queue.push_back(w);
                }
            }
        }
    }
    // old -> new index
    let mut renum = vec![0usize; n];
    for (new_idx, &old_idx) in order.iter().enumerate() {
        renum[old_idx] = new_idx;
    }
    let mut g = QueryGraph::new(order.iter().map(|&o| graph.card[o]).collect());
    for &(u, v, sel) in &graph.edges {
        g.add_edge(renum[u], renum[v], sel);
    }

    // Enumerate the csg pairs.
    let mut table = seed_leaves(&g);
    let mut stats = CcpStats::default();

    // Emit a csg-cmp-pair: combine best plans for both sides (both join
    // orders are relevant for asymmetric cost models; with C_out they tie).
    fn emit_ccp(g: &QueryGraph, table: &mut DpTable, stats: &mut CcpStats,
                s1: NodeSet, s2: NodeSet) {
        stats.ccps_emitted += 1;
        stats.pairs.push((s1.0.min(s2.0), s1.0.max(s2.0)));
        let left = table[&s1].clone();
        let right = table[&s2].clone();
        let cand = combine(g, &left, &right);
        consider(table, cand);
        let cand = combine(g, &right, &left);
        consider(table, cand);
    }

    // EnumerateCsgRec: grow the connected set S within V \ X.
    fn enumerate_csg_rec(
        g: &QueryGraph, table: &mut DpTable, stats: &mut CcpStats,
        s: NodeSet, x: NodeSet,
        emit: &mut dyn FnMut(&QueryGraph, &mut DpTable, &mut CcpStats, NodeSet),
    ) {
        let n_set = g.neighbors(s, x);
        if n_set.is_empty() { return; }
        for sp in n_set.nonempty_subsets() {
            emit(g, table, stats, s.union(sp));
        }
        for sp in n_set.nonempty_subsets() {
            enumerate_csg_rec(g, table, stats, s.union(sp), x.union(n_set), emit);
        }
    }

    // EnumerateCmp: for a csg S1, enumerate all connected complements S2
    // with min(S2) > min(S1), then emit the pair.
    fn enumerate_cmp(g: &QueryGraph, table: &mut DpTable, stats: &mut CcpStats,
                     s1: NodeSet) {
        let x = NodeSet::prefix(s1.min_elem()).union(s1);
        let n_set = g.neighbors(s1, x);
        // descending order of node index
        let mut nodes: Vec<usize> = n_set.iter().collect();
        nodes.reverse();
        for v in nodes {
            let s2 = NodeSet::single(v);
            emit_ccp(g, table, stats, s1, s2);
            let bv = NodeSet::prefix(v).intersect(n_set);
            let mut emit = |g: &QueryGraph, t: &mut DpTable, st: &mut CcpStats,
                            s2p: NodeSet| {
                if g.connected_pair(s1, s2p) {
                    emit_ccp(g, t, st, s1, s2p);
                }
            };
            enumerate_csg_rec(g, table, stats, s2, x.union(bv), &mut emit);
        }
    }

    // EnumerateCsg: seed with each single node, descending.
    for i in (0..n).rev() {
        let s = NodeSet::single(i);
        enumerate_cmp(&g, &mut table, &mut stats, s);
        let mut emit = |g: &QueryGraph, t: &mut DpTable, st: &mut CcpStats,
                        s1: NodeSet| {
            enumerate_cmp(g, t, st, s1);
        };
        enumerate_csg_rec(&g, &mut table, &mut stats, s, NodeSet::prefix(i), &mut emit);
    }

    // Map the table back to the caller's original numbering.
    fn remap_set(s: NodeSet, order: &[usize]) -> NodeSet {
        let mut out = NodeSet::EMPTY;
        for i in s.iter() {
            out = out.union(NodeSet::single(order[i]));
        }
        out
    }
    fn remap_plan(p: &Plan, order: &[usize]) -> Plan {
        match p {
            Plan::Leaf(i) => Plan::Leaf(order[*i]),
            Plan::Join(l, r) => Plan::Join(
                Box::new(remap_plan(l, order)), Box::new(remap_plan(r, order))),
        }
    }
    let table = table
        .into_iter()
        .map(|(k, e)| {
            let k2 = remap_set(k, &order);
            (k2, Entry { set: k2, card: e.card, cost: e.cost,
                         plan: remap_plan(&e.plan, &order) })
        })
        .collect();

    (table, stats)
}
```

`emit_ccp` calls `combine` for both orders of the inputs, unlike DPsize and DPsub. With C<sub>out</sub> both orders cost
the same and one call would do, I kept both so the code works unchanged with an asymmetric cost model.

### Tracing it on our example

DPccp first renumbers the relations breadth-first starting from R₀, so v₀ = R₀, v₁ = R₂ and v₂ = R₁, and the chain
becomes v₀ — v₁ — v₂. The main loop then seeds every node in descending order:

- v₂ (R₁) has no neighbors with a larger index, so nothing happens.
- v₁ (R₂) emits ({R₂}, {R₁}) with its only larger neighbor, then grows to {R₂, R₁}, which has no complement left since
  everything else is prohibited.
- v₀ (R₀) emits ({R₀}, {R₂}), grows that complement through R₂'s neighbor to emit ({R₀}, {R₂, R₁}), then grows itself
  to {R₀, R₂} and emits ({R₀, R₂}, {R₁}).

That's the four pairs from Part 1 and nothing else, against 9 candidates for DPsize and 5 for DPsub. By the time
({R₀}, {R₂, R₁}) is emitted the best plan for {R₂, R₁} is already in the table, because its only split was emitted while
seeding v₁.

```rust
#[test]
fn dpccp_on_the_example() {
    let g = example();
    let (table, stats) = dpccp(&g);
    // stats.pairs is in DPccp's breadth-first numbering, where
    // v₀ = R₀, v₁ = R₂ and v₂ = R₁
    let v = |nodes: &[usize]| set(nodes).0;
    assert_eq!(stats.pairs, vec![
        (v(&[1]), v(&[2])),    // ({R₂}, {R₁})
        (v(&[0]), v(&[1])),    // ({R₀}, {R₂})
        (v(&[0]), v(&[1, 2])), // ({R₀}, {R₂, R₁})
        (v(&[0, 1]), v(&[2])), // ({R₀, R₂}, {R₁})
    ]);
    assert_eq!(dpsize(&g).1, 9);
    assert_eq!(dpsub(&g).1, 5);
    // R₀ ⋈ (R₂ ⋈ R₁), the cost of 20 from Part 1
    assert_eq!(table[&set(&[0, 1, 2])].cost.round(), 20.0);
}
```

### Breadth-first numbering

The enumeration is correct for any numbering, turning the renumbering off doesn't change the results on 300 random
graphs. The paper numbers the nodes breadth-first and I kept that. Our implementation renumbers internally and remaps
the finished table and plans back, which is an easy step to forget: a first version of this code returned the table in
renumbered space, and the `three_classic_dps_agree` test below caught it because the *keys* disagreed with DPsub's table
even though the optimal cost matched.

### Correctness of the order

When a pair (S₁, S₂) is emitted, `emit_ccp` reads `table[&s1]` and `table[&s2]`, and those entries must already be
optimal. They are, because the enumeration emits every csg-cmp-pair splitting a set S before any pair in which S itself
is an operand, and every connected set's optimal plan is completed by the emission of its last split. Pairs
for S are emitted while growing structures seeded at min(S), and any pair (S, T) using S as an operand is emitted from a
seed ≤ min(S) with S reachable only after being fully assembled.

## Comparing the three approaches

`three_classic_dps_agree` and `dpccp_emits_each_pair_once` run DPsize, DPsub and DPccp on about a hundred random graphs
and check that they find the same optimal cost and plan the same sets, and that DPccp never emits a pair twice.
To get random graphs without pulling in a crate we use a small xorshift generator:

```rust
use std::collections::HashSet;

// tiny deterministic RNG (xorshift) to avoid dependencies
struct Rng(u64);
impl Rng {
    fn next(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        self.0 = x;
        x
    }
    fn f64(&mut self) -> f64 {
        (self.next() >> 11) as f64 / (1u64 << 53) as f64
    }
    fn below(&mut self, n: usize) -> usize {
        (self.next() % n as u64) as usize
    }
}

/// A random spanning tree over n relations plus up to `extra_edges` more
/// edges, with random cardinalities and selectivities.
fn random_connected(rng: &mut Rng, n: usize, extra_edges: usize) -> QueryGraph {
    let mut g = QueryGraph::new((0..n).map(|_| 10.0 + rng.f64() * 10000.0).collect());
    for v in 1..n {
        let u = rng.below(v);
        g.add_edge(u, v, 0.001 + rng.f64() * 0.999);
    }
    let mut have: HashSet<(usize, usize)> = g.edges.iter().map(|&(a, b, _)| (a, b)).collect();
    for _ in 0..extra_edges {
        let a = rng.below(n);
        let b = rng.below(n);
        if a == b { continue; }
        let key = (a.min(b), a.max(b));
        if have.insert(key) {
            g.add_edge(key.0, key.1, 0.001 + rng.f64() * 0.999);
        }
    }
    g
}

fn opt_cost(table: &DpTable, n: usize) -> f64 {
    table[&NodeSet::full(n)].cost
}

#[test]
fn three_classic_dps_agree() {
    let mut rng = Rng(7);
    for _ in 0..60 {
        let n = 2 + rng.below(9);
        let extra = rng.below(n + 3);
        let g = random_connected(&mut rng, n, extra);
        let (t1, _) = dpsize(&g);
        let (t2, _) = dpsub(&g);
        let (t3, _) = dpccp(&g);
        let (c1, c2, c3) = (opt_cost(&t1, n), opt_cost(&t2, n), opt_cost(&t3, n));
        assert!((c1 - c2).abs() < 1e-6 * c1.max(1.0), "dpsize {c1} vs dpsub {c2}");
        assert!((c1 - c3).abs() < 1e-6 * c1.max(1.0), "dpsize {c1} vs dpccp {c3}");
        // the tables must contain exactly the same connected sets
        let k2: HashSet<_> = t2.keys().collect();
        let k3: HashSet<_> = t3.keys().collect();
        assert_eq!(k2, k3);
    }
}

#[test]
fn dpccp_emits_each_pair_once() {
    let mut rng = Rng(42);
    for _ in 0..50 {
        let n = 3 + rng.below(8);
        let extra = rng.below(n);
        let g = random_connected(&mut rng, n, extra);
        let (_, s) = dpccp(&g);
        let uniq: HashSet<_> = s.pairs.iter().collect();
        assert_eq!(uniq.len(), s.pairs.len(), "duplicate ccp emitted");
    }
}
```

`work_counters` produces the counter table from the DPsub section and `dpccp_emits_exactly_the_closed_forms` checks
DPccp's emissions against the closed forms from Part 1:

```rust
#[test]
fn work_counters() {
    let cases = [
        (chain(10), 1_135, 1_981, 165),
        (star(10), 57_888, 19_171, 2_304),
        (clique(10), 306_991, 28_501, 28_501),
        (chain(15), 5_628, 65_399, 560),
        (chain(20), 17_545, 2_096_920, 1_330),
    ];
    for (g, size_pairs, sub_splits, ccps) in cases {
        assert_eq!(dpsize(&g).1, size_pairs);
        assert_eq!(dpsub(&g).1, sub_splits);
        assert_eq!(dpccp(&g).1.ccps_emitted, ccps);
    }
}

#[test]
fn dpccp_emits_exactly_the_closed_forms() {
    for n in 3..=10u64 {
        let k = n as usize;
        assert_eq!(dpccp(&chain(k)).1.ccps_emitted, (n * n * n - n) / 6);
        assert_eq!(dpccp(&cycle(k)).1.ccps_emitted, (n * n * n - 2 * n * n + n) / 2);
        assert_eq!(dpccp(&star(k)).1.ccps_emitted, (n - 1) * (1 << (n - 2)));
        assert_eq!(dpccp(&clique(k)).1.ccps_emitted, (3u64.pow(n as u32) - (1 << (n + 1)) + 1) / 2);
    }
}
```

DPccp only handles inner joins with binary predicates. Predicates that touch more than two relations, and non-inner
joins, do not fit a simple graph, and Part 3 generalizes DPccp to DPhyp, which is what HyPer, Umbra and DuckDB run.
Part 4 is about queries where even O(`#ccp`) is too much work.

## References

* Guido Moerkotte, Thomas Neumann, [Analysis of Two Existing and One New Dynamic Programming Algorithm for the
  Generation of Optimal Bushy Join Trees without Cross Products](https://www.vldb.org/conf/2006/p930-moerkotte.pdf),
  VLDB 2006.
* Guido Moerkotte, [Building Query Compilers](https://pi3.informatik.uni-mannheim.de/~moer/querycompiler.pdf).

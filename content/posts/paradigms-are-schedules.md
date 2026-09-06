+++
title = "Execution Paradigms Are Schedules"
description = "Charting the design space of query execution."
date = 2026-07-19
draft = false

tags = ["databases", "compilers"]
+++

In the [last post](https://deferworks.org/posts/inkfuse-cps/) I argued that the difference
between a HyPer-style compiled pipeline and Inkfuse's precompiled
fragments comes down to which continuations get reified before
runtime and which get inlined away. The post itself sparked a discussion
with [Alex Miller](https://transactional.blog/) about what an optimal IR
for query compilers would look like, this discussion made me look at some
old notes I wrote down a while ago while working on `raptor`[^raptor], `raptor` is
a query compiler I started working on a while ago based on the same ideas
as [Lingo](https://www.lingo-db.com/) it leverages [MLIR](https://mlir.llvm.org/)
to build a tower of IRs as dialects. You first lower a logical plan to the
`relalg` dialect which maps closely to logical plan nodes, for example
here's what the aggregation operator looks like in MLIR's tablegen constructs.

[^raptor]: The `raptor` implementation is not opensource yet because it's
           still not complete and it's still just a tinkering project I
           work on from time to time.

```c++
class RelAlg_Op<string mnemonic, list<Trait> traits = []>
    : Op<RelAlg_Dialect, mnemonic, traits>;

def RelAlg_AggregationOp : RelAlg_Op<"aggregation", [
    SingleBlockImplicitTerminator<"ReturnOp">
]> {
    let summary = "Group and aggregate";
    let description = [{
        Groups tuples by the grouping columns and computes aggregates.
        The region specifies the aggregate computations.

        Example:
        %1 = relalg.aggregation %0
            groupby [@R::@key]
            [#relalg.coldef<@R::@total : i64>]
            (%tuple: !relalg.tuple) {
            %val = relalg.getcol %tuple @R::@value : i64
            %sum = relalg.aggrfn sum %val : i64 -> i64
            relalg.return %sum : i64
        } : !relalg.tuplestream
    }];
    let arguments = (ins
        RelAlg_TupleStreamType:$input,
        ArrayAttr:$groupByColumns,
        ArrayAttr:$computedColumns
    );
    let results = (outs RelAlg_TupleStreamType:$result);
    let regions = (region SizedRegion<1>:$aggregates);
    let assemblyFormat = [{
        $input `groupby` $groupByColumns $computedColumns $aggregates
        attr-dict `:` type($result)
    }];
}
```

The above dialect is then lowered to the suboperator dialect `subop` which
you can think of as one tier lower.

```c++
def SubOp_LoopOp : SubOp_Op<"loop", [
    SingleBlockImplicitTerminator<"subop::YieldOp">
]> {
    let summary = "Loop over tuples with carry values";
    let description = [{
        Execute the body for each tuple, threading carry values through
        the loop. Used for aggregations and running computations.
    }];

    let arguments = (ins
        SubOp_TupleStreamType:$input,
        Variadic<AnyType>:$initCarry
    );
    let results = (outs Variadic<AnyType>:$finalCarry);
    let regions = (region SizedRegion<1>:$body);

    let assemblyFormat = [{
        $input `carry` `(` $initCarry `:` type($initCarry) `)` $body
        attr-dict `:` type($input) `->` type($finalCarry)
    }];
}

def SubOp_BufferType : SubOp_Type<"Buffer", "buffer"> {
    let summary = "A mutable buffer for storing tuples";
    let description = [{
        A buffer is a mutable container that can hold tuples. Used for
        materializing intermediate results and building hash tables.
    }];
    let parameters = (ins ArrayRefParameter<"mlir::Type">:$columnTypes);
    let assemblyFormat = "`<` $columnTypes `>`";
}

def SubOp_HashIndexType : SubOp_Type<"HashIndex", "hashindex"> {
    let summary = "A hash index over a buffer";
    let description = [{
        Provides hash-based access to tuples in a buffer. The key types
        determine which columns are used for hashing.
    }];
    let parameters = (ins
        ArrayRefParameter<"mlir::Type">:$keyTypes,
        ArrayRefParameter<"mlir::Type">:$valueTypes
    );
    let assemblyFormat = "`<` `keys` `:` $keyTypes `,` `values` `:` $valueTypes `>`";
}
```


The final conversion step lowers `subop` to [`scf`](https://mlir.llvm.org/docs/Dialects/SCFDialect/)
which is another tier lower and can be lowered to actual LLVM IR which
you can ship off to [Orc](https://llvm.org/docs/ORCv2.html) to get machine
code.

Of course as elegant as this looks there's a lot of hidden details notably
you need to scaffold some sort of runtime that gives you all the nitty gritty
stuff your query execution code needs. For example you need to track lifetimes
of Arrow buffers that represent your columnar data around, you need to expose
bindings to some sort of allocator to handle buffers and so on.

My theory on query execution is that if two engines that look nothing alike are the same program with different
decisions about where continuations live, then "vectorized" and "compiled" and "hybrid" are not rival architectures
so much as different points in some space, and it should be possible to name the axes of that space.

The reason I am bringing this up is that my notes also referred to a concept I initially described as
*sub-operator DAG + edge-located schedules* and since we can safely assume all operator DAGs are more
or less the same then the axes of the design space of query execution come down to what schedules you're
picking or specializaing for.

This post is an attempt to name these axes. The claim is that the execution paradigms we build systems
around are *schedules* of one declarative suboperator program[^program], and that the schedule lives
on the edges of the plan rather than the nodes. But I also want to keep the claim from staying too abstract
so in this post I also describe a small enough compiler that represents the IR, schedules
a fragments library and a JIT. The full compiler can be found in [schedules.py](https://deferworks.org/code/schedules.py).

[^program]: I am fairly sure with some extra effort one can describe this
            as an algebra and work out the formal details.

## Charting the design space of query execution

The design space of query execution has mostly been charted by
building one system per point. MonetDB/X100 gave us vectorized
interpretation, HyPer gave us data-centric compilation and for a
decade they were treated as being separate approaches with different
trade-offs Kersten et al.'s [comparison](https://db.cs.cmu.edu/papers/2018/p2209-kersten.pdf)
showed that which one wins depends mostly on the workload. Since then there has
been more hybrid designs like [ROF](https://www.vldb.org/pvldb/vol11/p1-menon.pdf) and
[Inkfuse](https://www.cs.cit.tum.de/fileadmin/w00cfj/dis/papers/inkfuse.pdf).

The most serious attempt to chart the space rather than add another point to it comes
from Gubner and Boncz [VOILA](https://www.vldb.org/pvldb/vol14/p1067-gubner.pdf) is a DSL
that describes what an operator does without pinning down the order or style in which
the operations run; from one VOILA program its backends can generate thousands of *flavors*: vectorized,
data-centric, and many hybrids in between that nobody had bothered to hand-build.
The follow-up [Excalibur](https://vldb.org/pvldb/vol16/p829-boncz.pdf), takes the next
step: if there are thousands of flavors and no static cost model can robustly pick among
them, search at runtime. Excalibur encodes a point in the space as a sequence of
mutations, casts exploration as a multi-armed bandit problem, and switches flavors mid-query.

## Algorithms + Schedules

Let's take a brief detour into the world of specialized compilers and talk about
[Halide](https://cacm.acm.org/research/halide/). Halide split image pipelines into
an *algorithm* which says what each stage computes and a *schedule*, which says
where and when values are computed and stored: the loop order, the tiling, what gets fused into
its consumer and what gets materialized into a buffer. This approach allows you to turn
the problem from "rewrite the program until it is fast" into "search a space of schedules for a fixed program"
with the compiler guaranteeing that every schedule computes the same thing[^legality].

[^legality]: The compiler guaranteeing that a schedule is legal is very different
             from encoding it into the rewrite rule. A handwavy analogy is how Rust
             makes memory safety a compiler checked property versus having it as a
             runtime property e.g. reference counting.

Query execution already has the algorithm half think back to Bandle and Giceva's
[sub-operator approach](https://www.vldb.org/pvldb/vol14/p2483-bandle.pdf) and Jungmair and Giceva's [declarative
sub-operators](https://www.vldb.org/pvldb/vol16/p3461-jungmair.pdf) decompose relational
operators into small reusable pieces scatter,gather, scan, fold, hash-partition and so on
which stop prescribing how they execute a declarative suboperator DAG says which values
flow where and nothing else.

In what follows we will mainly look at Q6 from TPC-H which is simple enough to write
a small compiler around but contains enough details to make this interesting.

```sql
select sum(l_extendedprice * l_discount) as revenue
from lineitem
where l_shipdate >= date '1994-01-01'
  and l_shipdate < date '1995-01-01'
  and l_discount between 0.05 and 0.07
  and l_quantity < 24;
```

We will represent suboperators as frozen dataclasses and the query plan will be just a list.

```python

# Filter based on an operator technically we want to pass in a predicate
# but to keep things simple we will just pass-in some comparison operations.
@dataclass(frozen=True)
class Filter:
    op: str      # "<", "<=", ">="
    lhs: str     # column name
    rhs: object  # int constant or column name

@dataclass(frozen=True)
class Map:
    op: str; lhs: str; rhs: object
    out: str     # name of the produced column

# Fold should allow arbitrary operations but again for simplicity we just assume
# summation.
@dataclass(frozen=True)
class Fold:
    col: str     # running sum of this column

# This is the query plan as in sub-operator representation.
Q6 = [
    Filter(">=", "shipdate", 8766),   # 1994-01-01
    Filter("<",  "shipdate", 9131),   # 1995-01-01
    Filter(">=", "discount", 5),
    Filter("<=", "discount", 7),
    Filter("<",  "quantity", 24),
    Map("*", "extprice", "discount", "revenue"),
    Fold("revenue"),
]
```

Note what is absent. There are no loops, no batches, no buffers and no mention of code generation
instead everything is declarative and `Q6` just says which values flow where and nothing else
which is what makes it an algorithm in Halide's sense.

```text
scan(lineitem) ── e1 ──> filter ── e2 ──> map(price × disc) ── e3 ──> fold(sum)
```

## Location Scheduling

My proposal is that the schedule lives on those edges and we annotate each one with a tuple
composed of a *data location* e.g. a register, a cache-resident buffer of some capacity, memory, a
*representation* (dense vector, selection vector, on down to whatever your engine speaks)
and a *granularity* (one tuple, a vector of 1024, a morsel). In code we can represent an annotation and a
schedule as:

```python
@dataclass(frozen=True)
class Edge:
    loc: str = "buf"    # "reg" | "buf"
    rep: str = "dense"  # "dense" | "sel"

@dataclass(frozen=True)
class Schedule:
    edges: tuple        # one Edge between each pair of adjacent ops
    vector: int = 1024
    tier: str = "auto"  # "auto" | "precompiled" | "jit"
```

We can then take the different paradigms we talked about before as just specializations and
describe them as functions:

```python
hyper = fuse_all(Q6)         # every edge Edge("reg")
vec   = stage_all(Q6)        # every edge Edge("buf", "dense")
rof   = stage_at(hyper, 4)   # hyper with one edit
```

`stage_at` is the most interesting one because it inserts a boundary by demoting an
edge from `reg` to `buf` which is what ROF does via pipeline buffers in between fused blocks.


```python
def stage_at(sched, i, rep="dense"):
    """Demote one edge from reg to buf. ROF is one application of this."""
    edges = list(sched.edges)
    edges[i] = Edge("buf", rep)
    return replace(sched, edges=tuple(edges))
```

The paradigms that we've been describing end up being just schedule assignments to the plan
we drew above.

```text
hyper:      e1: reg        e2: reg        e3: reg
vec:        e1: buf(1024)  e2: buf(1024)  e3: buf(1024)
rof:        e1: reg        e2: buf(1024)  e3: reg
```

Here is what the annotations mean, as the code each row should compile down to. With
every edge in `reg`, values flow between suboperators as local variables and the whole
pipeline is one loop:

```python
revenue = 0
for i in range(n):
    if not (shipdate[i] >= 8766): continue
    if not (shipdate[i] <  9131): continue
    if not (discount[i] >= 5):    continue
    if not (discount[i] <= 7):    continue
    if not (quantity[i] <  24):   continue
    revenue += extprice[i] * discount[i]
```

`e1` and `e2` appear nowhere: an edge in `reg` is a value that is never stored anywhere
nameable the continuation between suboperators inlined, and it is what Hyper's codegen
produces as LLVM IR.

With every edge in `buf`, each suboperator is its own loop and the edges are the lists between the loops:

```python
e1_price, e1_disc = [], []
for i in range(n):                    # filter writes survivors into e1
    if passes_predicate(i):
        e1_price.append(extprice[i])
        e1_disc.append(discount[i])
e2 = [p * d for p, d in zip(e1_price, e1_disc)]   # map drains e1
revenue = sum(e2)                                 # fold drains e2
```

One benefit of these loops is that there is nothing specific to Q6; it can be compiled once
and reused by any query, which is similar to Inkfuse's fragments. Do note that each boundary
introduces a copy which isn't the case in the fused version.

Inkfuse corresponds to the all-buf row, with the difference that its fragments are precompiled
instead of interpreted while Copy-and-patch keeps the boundary in registers: each stencil
ends with a tail call that passes its outputs in fixed registers to the next stencil, so the blocks
are precompiled but the edge between them is reg. This of course requires using the GHC calling convention
if you want to get a good idea about Copy-and-patch I recommend Alex's [article](https://transactional.blog/copy-and-patch/how-it-works).

How do we define a *region* ? Well we can define a region as the maximal tiling of sub-operators connected
by `reg` edges so building a region is just about splitting the pipeline at `buf` edges.

```python
def regions(ops, sched):
    out, start = [], 0
    for i, e in enumerate(sched.edges):
        if e.loc == "buf":
            out.append((start, i + 1))
            start = i + 1
    out.append((start, len(ops)))
    return out
```

`hyper` produces one region of seven suboperators, `vec` produces seven regions of one, and `rof` produces two
i.e the five filters fused, then map and fold fused. The actual fusion structure is not a property anyone
states we just derive it from where the buffers are.

There is a fourth annotation that has no Halide analog and it applies to regions rather
than edges which I will call *tiering*. A good way to think about tiering is how V8 for
example uses [4 tiers of compilers](https://v8.dev/blog/maglev) depending on different heuristics.
We can think of tiering in our case not in the context of JIT thresholds or hotness but instead
where the code for a region comes from in the baseline case we interpret it out of a library of single-node primitives
or look it up in a library of precompiled multi-node fragments or compile it for this query.

I think this allows us to answer the axis jamii called [the missing tier for query compilers](https://www.scattered-thoughts.net/writing/a-missing-tier-for-query-compilers/),
in terms of pipeline boundaries. Inkfuse ties the two together: a fragment is defined by both
its boundary and the fact that it is precompiled. Keeping tier separate from the edge annotations means
you can change one without the other: swap a JIT region for an equivalent precompiled block without touching
the buffers (this is what a fragment cache does), or move a buffer without changing how the code is produced
which is what ROF does.


## Two pipelines and a join

Q6 is a single straight pipeline, which is enough to introduce the
coordinates but not enough to stress them the scheduling questions people
actually argue about show up around joins, so before building the
tiers it is worth seeing what a join looks like in this notation.
TPC-H Q14:

```sql
select 100.00 * sum(case when p_type like 'PROMO%'
                         then l_extendedprice * (1 - l_discount)
                         else 0 end)
             / sum(l_extendedprice * (1 - l_discount)) as promo_revenue
from lineitem, part
where l_partkey = p_partkey
  and l_shipdate >= date '1995-09-01'
  and l_shipdate < date '1995-10-01';
```

Decomposed into suboperators, Q14 is two pipelines. Jungmair's IR and
Excalibur's low-level plan both break a hash join apart in roughly
this way:

```text
build: scan(part) ── b1 ──> insert(ht)

probe: scan(lineitem) ── e1 ──> filter(shipdate) ── e2 ──> probe(ht)
         ── e3 ──> gather(p_type) ── e4 ──> map(promo?, revenue) ── e5 ──> fold(sums)
```

`insert` builds a hash table keyed on `p_partkey`. The probe pipeline
cannot start until the build has finished, so `ht` is not an edge
between the pipelines; it is shared state, and the build/probe
dependency is what pipeline-oriented compilers call a pipeline
breaker. On the probe side, `probe` looks up `l_partkey` in the table
and `gather` fetches `p_type` out of the matched row. A monolithic
HashJoin operator does both steps internally. Splitting them apart
matters because the gather is a dependent load per matching row,
usually a cache miss, and once it is a separate node the schedule can
decide where it runs and on how many rows at a time.

The edge to look at is `e2`, between the filter and the probe. With
`e2: reg` the probe is inlined into the scan/filter loop, which is
how a fully fused pipeline runs this query: each surviving row probes
the hash table immediately, and when the table is bigger than cache
the load stalls with nothing else in flight to hide it. With
`e2: buf(1024)` the filter accumulates a vector of pending keys
before any probe runs, and a buffer of pending keys is the
precondition for software prefetching[^probe-stall]: while probing
key `i` you issue a prefetch for the bucket of key `i + k`, and by
the time the loop reaches it the bucket is in cache. This was the
original motivation for ROF, and in this notation the paper's stage
boundary in front of the probe is the annotation `e2: buf`.

[^probe-stall]: The longer version of this argument, with the latency
    arithmetic, is in the last post's footnote on ROF and
    prefetching; the canonical reference is Chen et al., *Improving
    Hash Join Performance Through Prefetching*.

The walkthrough compiler stops short of all of this: it handles
straight pipelines only, and Q14 is the first query that would force
a second pipeline, shared state, and a barrier into it. I'll come
back to what that state costs the formalism at the end.

## Tier one: the block library

Back to Q6 and the compiler. The first tier we will need to stitch together
is some pre-compiled primitives. Because we don't know what every query we
will be processing looks like we want to limit ourselves to a combination
of primitive operations, but how do we decide which combination of primitive
operations should we pre-compile ?

The edge annotations answer this because they are exactly what identifies a
compiled block. A block is code for some fused region; its interior edges were
inlined away, so what survives is the boundary: which suboperators the region
computes, plus the representation of each edge crossing in or out. I call that
tuple the region's *boundary key*, and in the compiler it is a plain tuple:

```python
def region_key(ops, sched, start, end):
    parts = tuple(shape_of(o) for o in ops[start:end])
    rin, rout = in_rep(sched, start), out_rep(sched, end - 1)
    if end - start == 1:
        return (*parts[0], rin, rout)     # e.g. ("filter", ">=", "const", "dense", "sel")
    return ("fused", parts, rin, rout)
```

Notice that the key doesn't refer to explicit column names or constants `("filter", ">=", "const", ...)`
covers `shipdate >= 8766` and `quantity >= 5` alike, because those arrive at the block as call-time parameters.
The key just holds what the code shape depends on and the runtime state holds what this query binds to.

The library itself is a dictionary from boundary keys to code:

```python
def build_library():
    lib = {}
    for sym in ("+", "*", "<", "<=", ">="):
        for sh in ("const", "col"):
            for rin, rout in (("dense", "dense"), ("dense", "sel"), ("sel", "sel")):
                lib[("filter", sym, sh, rin, rout)] = make_filter(sym, rin, rout)
            for rep in ("dense", "sel"):
                lib[("map", sym, sh, rep, rep)] = make_map(sym, rep)
    for rep in ("dense", "sel"):
        lib[("fold", "+", rep, None)] = make_fold(rep)
    # no fused key is ever inserted
    return lib
```

Here is `make_map`, trimmed of its constant-operand handling:

```python
def make_map(sym, rep):
    fn = OP_FNS[sym]
    def block(chunk, o, needed):      # o.lhs, o.rhs, o.out bind at call time
        lhs, rhs = chunk.cols[o.lhs], chunk.cols[o.rhs]
        out = [0] * chunk.n
        for i in indices(chunk):      # chunk.sel, or range(chunk.n) if dense
            out[i] = fn(lhs[i], rhs[i])
        return with_column(chunk, o.out, out)
    return block
```

The dense and sel variants differ in their iteration space and in the contract of
their output, which is why representation is part of the key we have the same suboperator
but a different edge type and different code. In a native engine the difference will
manifest itself in the form of instruction density, auto-vectorization and cache misses
because the dense body will be branch-free and auto-vectorizes and the sel body will have
pointer and indices chasing and not always vectorize.

Different representations require passing data across boundaries, a filter for example produces
its surviving rows as a selection vector and a downstream block that computes a property over
rows wants a dense vector. So we need a way to copy or coerce the data in any engine this will
be an implementation hidden detail and won't appear as a plan-level construct but in our case we
want to have a conversion node that materializes selection vectors into fresh columns.


```python
@dataclass(frozen=True)
class Compact:
    """sel -> dense: materialize the survivors a selection vector
    names into fresh columns."""

def make_compact():
    def block(chunk, o, needed):
        if chunk.sel is None:
            return chunk
        cols = {c: [chunk.cols[c][i] for i in chunk.sel]
                for c in chunk.cols if c in needed}
        return Chunk(cols, len(chunk.sel), None)

lib[("compact", "sel", "dense")] = make_compact()
```

Being an ordinary suboperator is the point. `Compact` sits in the op
list, `regions` splits around it like anything else, and it resolves
through the same dictionary lookup, so the conversion is a thing in the
plan rather than a thing inside somebody's filter loop. `compact` is
the one coercion the walkthrough implements. Two more belong to the
same family and are not in the code: gather fetches values through a
level of indirection (we met one fetching `p_type` through Q14's probe
result), and spill demotes a register region into a buffer.

Speaking of legality a schedule can run on the precompiled tier if every one of its regions's
keys is in the dictionary. `plan` does that check while resolving each region to a callable:


```python
def plan(ops, sched, lib):
    resolved = []
    for start, end in regions(ops, sched):
        key = region_key(ops, sched, start, end)
        if key in lib and sched.tier != "jit":
            resolved.append(bind(lib[key], ops[start]))        # tier: precompiled
        elif sched.tier == "precompiled":
            raise IllegalSchedule(f"{key} is not in the library")
        else:
            resolved.append(jit_region(ops, sched, start, end))  # tier: jit
    return resolved
```

There is no separate list of allowed schedules; the check is dictionary membership, so adding
a block to the library is what makes the schedules that need it legal. Another thing to notice
is that we don't have to fix one convention for whether filters emit selection vectors or compacted
output. The same works for Q14, with one addition: the boundary key of a probe region also names the
hash table's bucket layout, because a precompiled probe block commits to a bucket format the same way
a map block commits to a vector width.

## Physical Layouts as Schedule Decisions

Another thing that needs to be encoded is the physical layout of hashmap buckets
this only matters because our approach allows us to define an arbitrary layout
for `prob/build` hashmaps and any storage interface.

```python
EMPTY = -1

# (insert, layout="open") — open addressing with linear probing:
# two flat arrays, collisions resolved by walking to the next slot.
def build_open(keys, payloads, bits):
    mask = (1 << bits) - 1
    ht_keys = [EMPTY] * (1 << bits)
    ht_pay  = [0] * (1 << bits)
    for k, p in zip(keys, payloads):
        h = hash(k) & mask
        while ht_keys[h] != EMPTY:
            h = (h + 1) & mask
        ht_keys[h], ht_pay[h] = k, p
    return {"keys": ht_keys, "pay": ht_pay, "mask": mask}

# (insert, layout="chained") — bucket heads pointing into entry
# arrays, collisions resolved by a next-pointer chain.
def build_chained(keys, payloads, bits):
    mask = (1 << bits) - 1
    heads = [EMPTY] * (1 << bits)
    ekey, epay, enext = [], [], []
    for k, p in zip(keys, payloads):
        h = hash(k) & mask
        ekey.append(k); epay.append(p); enext.append(heads[h])
        heads[h] = len(ekey) - 1
    return {"heads": heads, "ekey": ekey, "epay": epay,
            "enext": enext, "mask": mask}
```

```python
# (probe, layout="open", in: dense, out: sel)
def probe_open(keys_in, ht):
    hits, pay = [], []
    for i, k in enumerate(keys_in):
        h = hash(k) & ht["mask"]
        while ht["keys"][h] != EMPTY:      # scan the linear-probe run
            if ht["keys"][h] == k:
                hits.append(i); pay.append(ht["pay"][h])
                break
            h = (h + 1) & ht["mask"]
    return hits, pay

# (probe, layout="chained", in: dense, out: sel)
def probe_chained(keys_in, ht):
    hits, pay = [], []
    for i, k in enumerate(keys_in):
        e = ht["heads"][hash(k) & ht["mask"]]
        while e != EMPTY:                  # walk the chain
            if ht["ekey"][e] == k:
                hits.append(i); pay.append(ht["epay"][e])
                break
            e = ht["enext"][e]
    return hits, pay
```

```python
lib[("insert", "open")]                     = build_open
lib[("insert", "chained")]                  = build_chained
lib[("probe", "open",    "dense", "sel")]   = probe_open
lib[("probe", "chained", "dense", "sel")]   = probe_chained
# no ("probe", "robinhood", ...) entry exists
```

```python
sched.state_layout = {"ht": "robinhood"}
# region_key for the probe region -> ("probe", "robinhood", "dense", "sel")
# not in lib -> IllegalSchedule on the precompiled tier, JIT otherwise
```

## Tier two: the JIT

When the lookup misses and the schedule isn't pinned, `plan` falls
through to the JIT, and in Python a JIT is pleasantly short[^short] we can just generate
source, `exec` it, return the function where `jit_region` walks the ops in the region
and emits one line per suboperator a `continue` guard per filter, a local per map and
an accumulation for the fold:

[^short]: Changing Python for C gives us the same brevity a good design approach for
          generating C code can be seen in this [post](https://wingolog.org/archives/2026/02/09/six-thoughts-on-generating-c)


```python
def jit_region(ops, sched, start, end, needed_out, name):
    src = [f"def {name}(chunk, state):"]
    # ... load the columns the region reads, pick dense or sel iteration,
    #     then one generated line per suboperator ...
    exec("\n".join(src), ns := {"Chunk": Chunk})
    return ns[name]
```

Unlike the library blocks, the generated code specializes the query into the source: column names
and constants are baked in, the way HyPer's codegen bakes them into LLVM IR. Running `hyper` with tracing
on prints what came out:

```text
region 0: ops[0:7] tier=jit
| def region0(chunk, state):
|     discount = chunk.cols['discount']
|     extprice = chunk.cols['extprice']
|     quantity = chunk.cols['quantity']
|     shipdate = chunk.cols['shipdate']
|     acc = state['acc']
|     idx = chunk.sel if chunk.sel is not None else range(chunk.n)
|     for i in idx:
|         if not (shipdate[i] >= 8766): continue
|         if not (shipdate[i] < 9131): continue
|         if not (discount[i] >= 5): continue
|         if not (discount[i] <= 7): continue
|         if not (quantity[i] < 24): continue
|         v_revenue = extprice[i] * discount[i]
|         acc += v_revenue
|     state['acc'] = acc
```

This is the fused loop we wanted to get back in the schedules section except we didn't explicitely
write it instead we defined a schedule `fuse_all(Q6)` and seven declarative sub-operators and the
code kind of falls out. If we change the schedule we end up with different code f`rof` produces two
smaller functions, one compacting survivors into lists at its boundary and one consuming them.

One argument that keeps coming back in the HyPer and Umbra papers is that code generation is legitimately
about trading off compile times for execution times. In the case of Umbra, HyPer's successor and more recenetly
Cedar this was addressed by tiering at the IR level by using two compilers.

In our case because we can decide on schedules before running the query for the first time we an benefit from
vectorization and pre-compiled fragments without ever triggering a compile and instead only trigger the compile
for queries we judge as hot or for UDFs[^udfs].

[^udfs]: UDFs are *"la bete noire"* of query execution and offering UDF support almost always means supporting
        another compiler. BigQuery for example supports Javascript for UDFs and while its not documented anywhere
        if I were to venture a guess I would bet some form of stripped down V8 is used or maybe even just V8 is
        used for those. The nice thing about UDFs is that while they can be black boxes, statistically you know
        you will execute them many times ~ O(rows) so compiling them straight away will be rewarding.
        An intuitive way to think about it is that you dispatch the UDF to both TurboFan (fast compile times)
        and Maglev (V8's optimizing compiler) and whichever finishes first can be used to process available data.
        This approach is only rewarding if you expect your I/O to be super fast e.g. if you dispatch I/O's before
        lowering the final plan as a sort of speculative warm up but if your I/O is over the network e.g. reading Parquet
        over S3 then you can just compile with the last tier.

> I want to interject for a moment and clarify something, a lot of database papers on query compilation don't
  seem to discuss the contextual trade-off with I/O i.e. when your data is available for processing and I think
  this information makes the compile time discussion more interesting.

> Another thing I wish to explore in the future is how much we can theoretically push down to process near data,
  compilation is very attractive here because of how imaginative you can get e.g. processing near NIC or NVMe
  which seems like a non-brainer for hyperscalers and hard to achieve in practice for your average database.

But note how we didn't have to do any of that and instead we just leverage our scheduling to get tiers for free. Of
course there is a compile time to produce machine code out of C code but this cost can be modeled into the scheduling
model and you can decide when to keep using pre-compiled blocks versus when to generated fused kernels.

Finally we define a reference interpreter to act as a correctness oracle and this comes from the same
model we've been cooking all along. The reference interpreter walks the pipeline a row at a time
and never looks at a schedule:

```python
def reference(table, ops):
    acc = 0
    for i in range(nrows(table)):
        row = {c: col[i] for c, col in table.items()}
        for o in ops:
            ...   # apply each suboperator to the row dict
    return acc
```

Every combination of `(schedule, tier)` can be asserted against the reference interpreter and when
something goes wrong you can just flip one annotation at a time and look at the results.
If for example the selection-vector schedule on the precompiled tier returns a revenue slightly off the
reference rerun it with `tier="jit"` and if it agrees then you know some precompiled block is at
fault. Back on the precompiled tier, flip the edges to dense one at a time and look at the map's
dense vectors which isolates the precompiled sel-variant of the map.

```text
reference (row at a time, no schedule): revenue = 64967015

hyper    revenue = 64967015 [ok]   tier = jit
vec      revenue = 64967015 [ok]   tier = precompiled
vec_sel  revenue = 64967015 [ok]   tier = precompiled
rof      revenue = 64967015 [ok]   tier = jit
```

## If you already speak MLIR

Everything above can be restated in MLIR terms, and it is worth doing
because most of the infrastructure already exists.
[LingoDB](https://www.vldb.org/pvldb/vol15/p2389-jungmair.pdf) showed
that query compilation fits MLIR's progressive-lowering model, and
Jungmair's declarative sub-operators are implemented as a dialect on
that stack. So the algorithm half of the split is already an MLIR
first citizen suboperators are ops, and the edges of the DAG are SSA
values so the schedule is just part of the type system.

```mlir
%rows      = subop.scan @lineitem
             : !sched.stream<4 x i64, dense, 1024, buf>
%survivors = subop.filter %rows
             : !sched.stream<2 x i64, sel, 1024, buf>
```

The schedule-as-a-separate-program idea exists too. The [transform
dialect](https://mlir.llvm.org/docs/Dialects/Transform/), Halide's
descendant inside MLIR, expresses tiling and fusion decisions as
their own IR whose payload is another program, an edge schedule is a
transform script over the suboperator dialect: fuse these ops into
one region, materialize this value at that type, at this capacity.
The walkthrough's `Schedule` value and `regions` function are a
pocket-sized version of exactly that.

Tiering is also naturally a follow through of MLIR's dialects picking a tier
means picking a lowering path in this case you lower a fused region through `scf`
to `llvm` and you get the JIT tier. Lower a region to a `func.call` against a pre-existing
library of precompiled blocks and you get the fragment tier.

What MLIR does not provide is the contents of that middle tier; someone still has to decide which fused blocks to
enumerate. LingoDB today lowers every pipeline through one flavor chosen by static rules; the edge-schedule proposal
allows you to turn that fixed path into a search space.

## Where the comparison is not correct

Halide has limits that query plans do not have. These limits let Halide
divide the algorithm from the schedule. Query plans break that division
in four ways:

- Stages hold state.
- Bounds change with the data.
- Domains have no order.
- Halide has no tier.

**Stages hold state.** A Halide stage is a pure function of its inputs
`fold` is not a pure function, and a hash-table build is not a pure
function. The walkthrough has only one item of state: the executor keeps
one accumulator and Q14 shows the small version of the problem i.e. the hash
table is common state with a barrier and no edge annotation can put the
probe before the end of the build.

Q1 is the TPC-H query that aggregates by group. Q1 shows the large
version of the problem. Its fold keeps one aggregate for each group.
Thus the edge into the fold does not carry values. The edge carries
updates to common state. The aggregation monoid controls which
reorderings and parallel splits are safe, and the schedule does not.

You must show the correctness guarantee again for state. An effect
system on suboperators can do this. The effect system must show which
edges carry values and which edges carry state. Halide has related work
with the name `rfactor`. `rfactor` divides a reduction into partial
states, one for each thread. This is the best example to start from.

**Bounds change with the data.** Halide knows its loop extents at compile
time. A query engine does not know the output cardinality of a filter
before it runs that filter. This cardinality controls which of the
subsequent schedules is the fastest. Thus you cannot select the correct
schedule before the query starts. Halide's autoscheduler does not have
this problem.

I think that you must not select one schedule. Do these steps instead,
as Excalibur does:

1. Make a short list of schedules.
2. Run each schedule in the list on one morsel.
3. Keep the schedule that is the fastest.

Two schedules with one different annotation have one different region of
code. Thus you can identify the change that made the schedule faster. A
different post will tell you how to make the short list. It will also
tell you the cost of an incorrect selection.

**Domains have no order.** Halide divides ordered grids into tiles. A
relation is a bag, and a bag has no order. But the important decisions
about blocks are tiles of a domain that has no order. Morsels and radix
partitions before a join are two examples. I think that a radix join is a
loop tile operation with a partition operation. I cannot show this yet,
and I want this result from the formalism.

**Halide has no tier.** Halide always compiles. Thus Halide never selects
between a precompiled library and a JIT. Query engines must control the
time to the first tuple. Thus the tier is not an internal detail here.
Inkfuse, Excalibur, and jamii's post are different mostly on this axis.

## Things I'm still not sure about

Whether the schedule language is complete. Morsel-driven parallelism
looks like a granularity annotation on the scan edge plus a rule
about which state is shared, but that rule is the part I can't state
precisely yet, and the shared-state half is the same thing I couldn't
express in CPS terms last time.

Whether the coercion algebra stays closed once nulls and
variable-length data show up. Every representation in this post is a
vector of fixed-width values, which is the easy case. Validity
bitmaps and string payloads each add representation axes, and I don't
know whether they add a manageable number of coercions or an
explosion of them.

And whether this is a genuinely different chart or VOILA with types.
The honest version of the differentiation, as I see it today: VOILA
puts the flavor in the code a backend generates for the operators,
and Excalibur searches over generated code; the edge view puts the
paradigm in the intermediates between suboperators, and makes the
code a derived artifact of edge decisions plus a tier. The
walkthrough is small, but it is a proof of that last part: its
library blocks and its generated regions are both downstream of the
same seven dataclasses and a tuple of edge annotations. I think that
relocation is what makes legality-by-presence expressible at all. But I have talked myself into framings like this
before and been wrong about which part mattered.

## References

* The walkthrough compiler from this post:
  [schedules.py](https://deferworks.org/code/schedules.py).
* Tim Gubner, Peter Boncz, [Charting the Design Space of Query
  Execution using VOILA](https://www.vldb.org/pvldb/vol14/p1067-gubner.pdf),
  PVLDB 14.
* Tim Gubner, Peter Boncz, [Excalibur: A Virtual Machine for Adaptive
  Fine-grained JIT-Compiled Query Execution based on
  VOILA](https://vldb.org/pvldb/vol16/p829-boncz.pdf), PVLDB 16.
* Maximilian Bandle, Jana Giceva, [Database Technology for the Masses:
  Sub-Operators as First-Class
  Entities](https://www.vldb.org/pvldb/vol14/p2483-bandle.pdf), PVLDB 14.
* Michael Jungmair, Jana Giceva, [Declarative Sub-Operators for
  Universal Data
  Processing](https://www.vldb.org/pvldb/vol16/p3461-jungmair.pdf),
  PVLDB 16.
* Michael Jungmair, André Kohn, Jana Giceva, [Designing an Open
  Framework for Query Optimization and
  Compilation](https://www.vldb.org/pvldb/vol15/p2389-jungmair.pdf)
  (LingoDB), PVLDB 15.
* Prashanth Menon, Todd Mowry, Andrew Pavlo, [Relaxed Operator Fusion
  for In-Memory Databases](https://www.vldb.org/pvldb/vol11/p1-menon.pdf),
  PVLDB 11.
* Jonathan Ragan-Kelley et al., [Halide: A Language and Compiler for
  Optimizing Parallelism, Locality, and Recomputation in Image
  Processing Pipelines](https://people.csail.mit.edu/jrk/halide-pldi13.pdf),
  PLDI 2013.
* Haoran Xu, Fredrik Kjolstad, [Copy-and-Patch
  Compilation](https://arxiv.org/abs/2011.13127), OOPSLA 2021.
* Jamie Brandon, [A missing tier for query
  compilers](https://www.scattered-thoughts.net/writing/a-missing-tier-for-query-compilers/).
* Timo Kersten et al., [Everything You Always Wanted to Know About
  Compiled and Vectorized Queries But Were Afraid to
  Ask](https://db.cs.cmu.edu/papers/2018/p2209-kersten.pdf), PVLDB 11.
* The [Inkfuse
  paper](https://www.cs.cit.tum.de/fileadmin/w00cfj/dis/papers/inkfuse.pdf),
  and the [previous post](https://deferworks.org/posts/inkfuse-cps/) on it.

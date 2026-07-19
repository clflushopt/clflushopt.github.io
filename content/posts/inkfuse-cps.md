+++
title = "Inkfuse, Continuations and Query Compilation"
description = "Notes on continuation-passing style and where it hides in compiled databases."
date = 2026-06-09

tags = ["databases", "compilers"]
+++

A few weeks ago I read Remy Wang's blog post on [continuation-passing
style](https://remy.wang/blog/cps.html) and the day after, I kept thinking
about [Inkfuse](https://www.cs.cit.tum.de/fileadmin/w00cfj/dis/papers/inkfuse.pdf),
a query engine I'd been picking apart on and off for a while. The two felt
related in a way I couldn't quite articulate, so I'm writing this to figure
out why.

Inkfuse is a vectorized-meets-compiled query engine out of TUM. It comes
from the same group that brought us HyPer, the system most people point at
when they want to talk about compiled query execution. If you've read
Neumann's 2011 paper on data-centric code generation, Inkfuse sits on the
same shelf, only with a different set of trade-offs that I'll get to later.
The implementation is available on [Github](https://github.com/wagjamin/inkfuse)
and reading it side by side with the paper made some things click that the paper
alone didn't.

CPS is what functional programmers reach for when they want to make
control flow explicit. Instead of a function returning a
value to its caller, the caller hands the function a continuation, a
"what-to-do-next" callable, and the callee calls it with the result. You can
compile any program to CPS, and the resulting program no longer has a call
stack in the usual sense: every call is a tail call, every return is just
another call.

The reason I started seeing CPS in Inkfuse is that data-centric compiled
query engines are under this lens a hand-written CPS transform of an iterator-based plan.

## Pull versus Push

The textbook way to execute a query plan is the Volcano iterator model. Each
operator exposes `open / next / close`. To get a tuple out of the root, you
call `next`, which calls `next` on its child, which calls `next` on its
child, and so on. The control flow walks down to the leaf[^leaf], picks up
a tuple, walks back up evaluating filters and projections, and hands a
tuple to the caller. The model gives you a clean separation between
operators but pays for it in virtual calls and instruction-cache misses,
both of which add up at high tuple rates.

[^leaf]: In a classical Volcano plan a leaf is any operator with no
    children — table scan, index scan, `VALUES`, or a materialized
    subquery/CTE reference. In pipeline-oriented compilation the
    boundary moves: a pipeline can also start from the probe side of a
    hash join or the read side of a grouping, both leaf-*like* from
    that pipeline's perspective because their build side has already
    materialized into a hash table before this pipeline runs.

To keep things concrete, here's the plan I'll keep coming back to. It's
the simplest query plan shape: a scan, a filter, an aggregate.

<figure class="diagram">
<svg viewBox="0 0 720 260" xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="qp-t qp-d">
  <title id="qp-t">Volcano query plan</title>
  <desc id="qp-d">A three-operator pipeline. Scan reads rows from table t, filter keeps rows where c is greater than 10, aggregate sums a times b.</desc>
  <defs>
    <marker id="qp-up" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="8" markerHeight="8" orient="auto">
      <path d="M 0 0 L 10 5 L 0 10" fill="none" stroke="currentColor" stroke-width="1.4"/>
    </marker>
  </defs>
  <g stroke="currentColor" fill="none">
    <rect x="60" y="20" width="600" height="220" stroke-width="1"/>
    <line x1="60" y1="93" x2="660" y2="93" stroke-width="0.75"/>
    <line x1="60" y1="166" x2="660" y2="166" stroke-width="0.75"/>
    <line x1="620" y1="225" x2="620" y2="40" stroke-width="0.75" stroke-dasharray="3 4" marker-end="url(#qp-up)"/>
  </g>
  <g fill="currentColor">
    <g font-family="Fraunces, Georgia, serif" font-weight="500" font-size="22">
      <text x="100" y="62">aggregate</text>
      <text x="100" y="135">filter</text>
      <text x="100" y="208">scan</text>
    </g>
    <g font-family="'Space Mono', ui-monospace, monospace" font-size="15">
      <text x="300" y="63">sum(a * b)</text>
      <text x="300" y="136">c &gt; 10</text>
      <text x="300" y="209">t(a, b, c)</text>
    </g>
    <g font-family="'Space Mono', ui-monospace, monospace" font-size="10" letter-spacing="2" opacity="0.55">
      <text x="640" y="132" text-anchor="end">DATA FLOW</text>
    </g>
  </g>
</svg>
<figcaption>Fig. 1 — Query plan for <code>SELECT sum(a * b) FROM t WHERE c &gt; 10</code>. Data flows from scan up to aggregate.</figcaption>
</figure>

In Volcano, running this query pulls from the root. The aggregate's `next`
calls the filter's `next`, which calls the scan's `next` in a loop until a
row passes the predicate, then the row walks back up. In Rust, with
iterators standing in for operators, that's roughly:

```rust
// A row tuple (a, b, c).
type Row = (i64, i64, i64);

fn run_pull(rows: Vec<Row>) -> i64 {
    rows.into_iter()
        .filter(|r| r.2 > 10)
        .map(|r| r.0 * r.1)
        .sum()
}
```

The HyPer model flips that. Instead of pulling from the top, you produce
from the bottom. The compiler walks the plan and calls `produce` on each
operator, top-down. When `produce` reaches a scan it emits a loop, and the
body of that loop is the `consume` of the scan's parent, whose body is the
`consume` of *its* parent, and so on up the tree. By the time code generation
is done, the pipeline is a single tight loop with no virtual dispatch and
the tuple lives in registers the whole way up. Neumann calls this data-centric
code generation. DuckDB implements a similar push model but using a vectorized
approach instead of code generation.

The same query in push form looks more like this:

```rust
fn scan(rows: &[Row], mut consume: impl FnMut(&Row)) {
    for r in rows {
        consume(r);
    }
}

fn filter(
    rows: &[Row],
    pred: impl Fn(&Row) -> bool,
    mut consume: impl FnMut(&Row),
) {
    scan(rows, |r| if pred(r) { consume(r) });
}

fn run_push(rows: &[Row]) -> i64 {
    let mut acc = 0;
    filter(rows, |r| r.2 > 10, |r| acc += r.0 * r.1);
    acc
}
```

The `consume` callback at the bottom is the aggregate's body. `filter`
takes it, wraps it in its own predicate check, and passes the wrapped
callback down to `scan`. After inlining, the whole thing collapses to a
single loop: the aggregate body sitting inside the filter body sitting
inside the scan body.

Looking at the shape of that we can see that each operator takes its parent's `consume` and
threads control into it. Nothing returns a tuple back up the chain; every
operator just hands off to the next consumer by calling its continuation.

That is what I pointed out as CPS here the `consume` function is the continuation
and the operator chain is the call chain and the IU bindings threaded through each operator
are the environment the continuation closes over.
A tuple passed to `consume` is the value the continuation receives and code generation is the
CPS transform itself, emitting machine code instead of source.

## Inkfuse

Pure HyPer-style compilation makes a different trade-off than dynamic dispatch
instead you pay an LLVM compilation tax up front. For short queries that tax can be longer than
just running the query in an interpreter. People have tried various ways around it: cheaper compiler
backends like custom bytecode VMs (NoisePage went this way), speculative compilation that runs an
interpreter and switches over to native code once it's ready, or breaking the pipeline into
pieces small enough that you don't have to generate fresh code for them at all.

That last option is more or less what Inkfuse does. Instead of compiling
each pipeline as a single monolithic function, it composes the pipeline out
of small fused fragments that are themselves precompiled ahead of time.
There is still a code-generation step, but it stitches fragments together
rather than emitting fresh IR for every operator combination. First-tuple
latency drops, and long-running queries still get most of the benefit of
full compilation.

I am going to draw an analogy that I don't believe might be fully accurate from a PL
perspective; if a compiled pipeline is a CPS-transformed query plan then a fused fragment
is a closed continuation i.e. a piece of compiled code that knows how to consume some tuple
shape and pass it along.

Stretching that further we can describe stitching fragments at runtime as a kind of
defunctionalization: you keep a tag that says which fragment runs next and the runtime
looks it up. Defunctionalization is the standard technique for turning higher-order CPS code into first-order code.

If we map this back to the Rust above `filter(rows, pred, |r| aggregate(r))` is a call
whose continuation is a closure over `pred` and `aggregate` becomes a pipeline like
`[expr_i64_gt, agg_sum_i64_mul]`: a list of names identifying precompiled fragments that the runtime chains by looking
each one up in the fragment library.

In the code base this all shows up as `Suboperator`. The interface is the
direct equivalent of the push closure I wrote in Rust above:

```cpp
struct Suboperator {
   /// Generate initial code for this operator when IUs are requested
   /// the first time. Will usually call open on all children and make
   /// the target IUs available to the parent operator.
   virtual void open(CompilationContext& context);
   /// All downstream consumers have been closed - this operator can be
   /// closed as well.
   virtual void close(CompilationContext& context);
   /// Consume a specific IU from one of the children.
   virtual void consume(const IU& iu, CompilationContext& context){};
   /// Consume once all IUs are ready.
   virtual void consumeAllChildren(CompilationContext& context){};
   // ...
};
```

The definition is from [Suboperator.h, L47-L63](https://github.com/wagjamin/inkfuse/blob/8b6b31b01da0/src/algebra/suboperators/Suboperator.h#L47-L63)

The `consume(iu, context)` call is the continuation when called it doesn't
move data but instead it emits code into `context` that consumes the IU[^iu]. The compiler
walks the DAG calling `consume` up through the parents, and what falls out
the other end is one fused loop body.

[^iu]: IU stands for "Information Unit" — Moerkotte's term from *Building
    Query Compilers* for a named value threaded through the plan. Think of
    it as a variable: a column read from a scan, an intermediate expression
    result, a group key. Each IU has a type and a lifetime; the compiled
    code lowers it to a register or a stack slot. Operators declare which
    IUs they consume from their children and which they produce for their
    parents, and the plan is well-formed exactly when every IU a consumer
    reads is produced somewhere above it in the tree.

## Relaxed operator fusion

The Inkfuse paper builds on relaxed operator fusion (ROF) by Menon et al.,
[*Relaxed Operator Fusion for In-Memory
Databases*](https://db.cs.cmu.edu/papers/2017/p1-menon.pdf). The idea in
ROF is that full pipeline fusion is sometimes too aggressive. Some
operators want to work on a batch of tuples at a time, either because they
want SIMD, or because they want to overlap memory stalls with computation
through software prefetching[^prefetch] by fusing everything into one tight
loop we kind of give up that batching.

[^prefetch]: The stall is usually a hash-table probe. Looking up a key in a
    table that's too big for cache means a load that misses L1, L2, L3 and
    ends at DRAM roughly 200-300 CPU cycles of waiting. In a tight one-tuple
    at-a-time loop there's nothing else to run during that wait,
    so the core just sits. Software prefetching turns the wait into useful
    work: with a buffer of pending keys, you issue a `prefetcht0` (x86;
    `prfm` on ARM) on the bucket for `keys[i + k]` while you do the actual
    probe for `keys[i]`. By the time iteration `i + k` comes around, its
    bucket is already in L1 and the probe hits cache. The lookahead `k`
    is tuned to memory latency divided by per-iteration cycle cost. Chen
    et al., *Improving Hash Join Performance Through Prefetching*, is the
    canonical reference. None of this works in a fully fused pipeline,
    because there is no buffer of upcoming keys to prefetch from.

ROF introduces stage boundaries inside an otherwise fused pipeline where between
two stages, tuples materialize into a small vector that sits in L1, and the
next stage consumes the vector. Inside a stage you still get the compiled,
tight-loop shape and across a stage boundary you get the vectorized shape.

Drawing it on the same query, the stage boundary sits between filter and
aggregate:

<figure class="diagram">
<svg viewBox="0 0 720 360" xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="rof-t rof-d">
  <title id="rof-t">Relaxed operator fusion — stage boundary</title>
  <desc id="rof-d">The same query plan, split into two stages by a buffer of tuples. Stage 1 (scan and filter) is a fused tight loop that pushes rows into a small vector. Stage 2 (aggregate) reads from that buffer, opening the door to SIMD and prefetching.</desc>
  <defs>
    <marker id="rof-up" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="8" markerHeight="8" orient="auto">
      <path d="M 0 0 L 10 5 L 0 10" fill="none" stroke="currentColor" stroke-width="1.4"/>
    </marker>
  </defs>
  <g stroke="currentColor" fill="none">
    <rect x="60" y="20" width="500" height="70" stroke-width="1"/>
    <rect x="60" y="110" width="500" height="36" stroke-width="1" class="buffer-band"/>
    <line x1="60" y1="118" x2="560" y2="118" stroke-width="0.5" stroke-dasharray="1 3"/>
    <line x1="60" y1="138" x2="560" y2="138" stroke-width="0.5" stroke-dasharray="1 3"/>
    <rect x="60" y="166" width="500" height="70" stroke-width="1"/>
    <rect x="60" y="256" width="500" height="70" stroke-width="1"/>
    <line x1="520" y1="256" x2="520" y2="242" stroke-width="0.75" marker-end="url(#rof-up)"/>
    <line x1="520" y1="166" x2="520" y2="152" stroke-width="0.75" marker-end="url(#rof-up)"/>
    <line x1="520" y1="110" x2="520" y2="96" stroke-width="0.75" marker-end="url(#rof-up)"/>
    <path d="M 600 20 L 610 20 L 610 90 L 600 90" stroke-width="1"/>
    <path d="M 600 166 L 610 166 L 610 326 L 600 326" stroke-width="1"/>
  </g>
  <g fill="currentColor">
    <g font-family="Fraunces, Georgia, serif" font-weight="500" font-size="22">
      <text x="90" y="63">aggregate</text>
      <text x="90" y="209">filter</text>
      <text x="90" y="299">scan</text>
    </g>
    <g font-family="'Space Mono', ui-monospace, monospace" font-size="15">
      <text x="280" y="63">sum(a * b)</text>
      <text x="280" y="209">c &gt; 10</text>
      <text x="280" y="299">t(a, b, c)</text>
    </g>
    <text x="310" y="134" text-anchor="middle" font-family="'Space Mono', ui-monospace, monospace" font-size="11" letter-spacing="2">BUFFER OF (a, b)</text>
    <g font-family="'Space Mono', ui-monospace, monospace" font-size="10" letter-spacing="1.4">
      <text x="622" y="49">STAGE 2</text>
      <text x="622" y="68" opacity="0.6">vectorized</text>
      <text x="622" y="235">STAGE 1</text>
      <text x="622" y="254" opacity="0.6">fused loop</text>
    </g>
  </g>
</svg>
<figcaption>Fig. 2 — ROF slices the pipeline at a materialization boundary. Stage 1 stays fused; stage 2 reads the buffer as a batch, where SIMD and prefetching become possible.</figcaption>
</figure>

Stage one is the same fused loop as before and stage two runs its own loop
over the buffer, which is the part the compiler can vectorize or
prefetch-pipeline. The aggregate's `consume` is still its body, it just
gets called by a different driver.

If you keep the CPS picture in your head, a stage boundary is the place
where the continuation gets trampolined. Rather than calling the next
consumer directly, you push the tuple into a buffer, return, and let the
runtime call the next consumer once the buffer fills up. This is also where
prefetching becomes possible: once you have a buffer of pending keys, you can issue prefetches
for the hash-table probes that will happen in the next stage and overlap
them with the work in the current one.

Kersten et al.'s [*Everything You Always Wanted to Know About Compiled and
Vectorized Queries But Were Afraid to
Ask*](https://db.cs.cmu.edu/papers/2018/p2209-kersten.pdf) is a useful read
in parallel with the Inkfuse paper. It compares the two execution models
head to head with controlled benchmarks. Once you've seen those numbers it
becomes hard to defend either model as universally better, and the
ROF/Inkfuse direction starts to look like the obvious thing to want.

There's also [Ngom et al. (DaMoN
2021)](https://db.cs.cmu.edu/papers/2021/ngom-damon2021.pdf), which I keep
coming back to whenever I think about the runtime layer around the
compiled code. Tangential to the CPS argument, but it shaped how I think
about where materialized state lives during execution, an important implementation
detail that is often not discussed in papers.

## Where I think the framing helps

I'm not claiming that calling Inkfuse a CPS engine gives you a new
optimization. It mostly doesn't, because the people building these systems
already understand what they're doing, they just describe it operationally.
The thing it gives me, as someone reading the papers and the code, is a
way to ask cleaner questions.

When I want to decide whether two operators can be fused, I can ask
whether the continuation between them stays inlined or has to be reified
into a buffer. For stage boundaries, the question becomes where in the
plan it pays to reify the continuation explicitly. And for understanding
fragments in Inkfuse, the question is what closed continuation a given
fragment encodes.

That last question is the one I find most useful. Inkfuse's fragment
library is, in effect, a set of compiled continuations parameterized by
tuple type. You can see exactly what that library looks like in
[ExpressionFragmentizer.cpp, L45-L58](https://github.com/wagjamin/inkfuse/blob/8b6b31b01da0/src/interpreter/ExpressionFragmentizer.cpp#L45-L58),
the piece that builds fragments for binary expressions:

```cpp
void ExpressionFragmentizer::fragmentizeBinary()
{
   // All binary operations on the same type.
   for (auto& type : types) {
      for (auto operation: op_types) {
         auto& [name, pipe] = pipes.emplace_back();
         auto& iu_1 = generated_ius.emplace_back(type, "");
         auto& iu_2 = generated_ius.emplace_back(type, "");
         auto& iu_out = generated_ius.emplace_back(
            ExpressionOp::derive(operation, {type, type}), "");
         auto& op = pipe.attachSuboperator(
            ExpressionSubop::build(nullptr, {&iu_out}, {&iu_1, &iu_2}, operation));
         name = op.id();
      }
   }
}
```

Every (type, op) pair becomes one named pipeline that gets baked into the
fragment shared object at build time. At query time, the runtime looks up
the fragment by name and chains it into the pipeline. The name in this case is the
defunctionalized continuation tag I mentioned earlier.

If you read the compile step as a CPS transform, the difference between
Inkfuse and a full HyPer-style compile is just *which* continuations get
reified ahead of time and which get built from source for this particular
query this is mostly a knob that you can tune and Inkfuse picks a setting
that favors latency without giving up much throughput.

The PL literature has two standard ways to eliminate first-class
continuations before runtime, and both show up here. One is to inline
every continuation call at compile time such that the continuation disappears
into the surrounding code effectively inlined and no closure survives. That is HyPer: one
monolithic function per pipeline and everything specialized to this query.
The other is defunctionalization where you give each continuation a name and
dispatch on it at runtime, that's what Inkfuse does each fragment is a named
continuation and the runtime chains them by name.

## Things I'm still not sure about

A few things I've been chewing on and haven't worked out.

One is how far the analogy goes when you bring parallelism in.
Morsel-driven parallelism (also TUM) gives each worker a chunk of input
and runs the pipeline on it. In CPS terms each worker has its own
continuation, but they share the join-side state. I haven't found a clean
way to express that and I don't think there is one at least not from
the literature I've read and I haven't looked much into parallelism
and concurrency in functional languages so there might be something there
to unearth.

Another is what happens at the operator boundaries that aren't really
operators: null handling, type dispatch, NULL-aware comparisons, all of these
become branches inside the compiled pipeline that aren't represented
in the plan, and I don't know whether the CPS framing helps or hurts in
reasoning about them. In fact I think this is one reason why this entire framing
might be "too pure" in the practical sense.


## References

* Remy Wang, [Continuation-passing
  style](https://remy.wang/blog/cps.html).
* The [Inkfuse
  paper](https://www.cs.cit.tum.de/fileadmin/w00cfj/dis/papers/inkfuse.pdf)
  from TUM.
* Menon, Mowry, Pavlo, [Relaxed Operator Fusion for In-Memory
  Databases](https://db.cs.cmu.edu/papers/2017/p1-menon.pdf).
* Kersten et al., [Everything You Always Wanted to Know About Compiled
  and Vectorized Queries But Were Afraid to
  Ask](https://db.cs.cmu.edu/papers/2018/p2209-kersten.pdf).
* [Ngom et al., DaMoN
  2021](https://db.cs.cmu.edu/papers/2021/ngom-damon2021.pdf).
* Thomas Neumann, [Efficiently Compiling Efficient Query Plans for Modern
  Hardware](https://www.vldb.org/pvldb/vol4/p539-neumann.pdf).
* Inkfuse source:
  [github.com/wagjamin/inkfuse](https://github.com/wagjamin/inkfuse).

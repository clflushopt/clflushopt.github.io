#!/usr/bin/env python3
"""Execution paradigms are schedules — a minimal compiler.

Runnable companion to the blog post. One declarative pipeline of
suboperators (the algorithm), several per-edge annotations (the
schedules), and two tiers (a precompiled block library and a JIT that
generates Python source and exec()s it). Every schedule must produce
the same answer as a row-at-a-time reference interpreter.

The mapping to the post:

  pipeline of suboperators      -> the algorithm
  Edge(loc, rep) per edge       -> the schedule
  maximal runs of reg edges     -> fused regions
  region -> boundary key        -> library lookup: legality by presence
  key missing from the library  -> fall back to the JIT tier
  Compact                       -> a coercion, sel -> dense

Run: python3 schedules.py
"""

import random
from dataclasses import dataclass, replace

# ---------------------------------------------------------------- algorithm

@dataclass(frozen=True)
class Filter:
    op: str      # "<", "<=", ">="
    lhs: str     # column name
    rhs: object  # int constant or column name

@dataclass(frozen=True)
class Map:
    op: str      # "+", "*"
    lhs: str
    rhs: object
    out: str     # name of the produced column

@dataclass(frozen=True)
class Fold:
    col: str     # running sum of this column

@dataclass(frozen=True)
class Compact:
    """Coercion, sel -> dense: materialize the survivors a selection
    vector names into fresh columns. A suboperator like any other, so it
    lands in a region and gets a boundary key."""

OP_FNS = {
    "+": lambda a, b: a + b,
    "*": lambda a, b: a * b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
}

# TPC-H Q6, integer-encoded: dates as epoch days (1994-01-01 = 8766,
# 1995-01-01 = 9131), discount in percent.
Q6 = [
    Filter(">=", "shipdate", 8766),
    Filter("<", "shipdate", 9131),
    Filter(">=", "discount", 5),
    Filter("<=", "discount", 7),
    Filter("<", "quantity", 24),
    Map("*", "extprice", "discount", "revenue"),
    Fold("revenue"),
]

# ----------------------------------------------------------------- schedule

@dataclass(frozen=True)
class Edge:
    loc: str = "buf"    # "reg" | "buf"
    rep: str = "dense"  # "dense" | "sel"

@dataclass(frozen=True)
class Schedule:
    edges: tuple        # one Edge between each pair of adjacent ops
    vector: int = 1024
    tier: str = "auto"  # "auto" | "precompiled" | "jit"

def fuse_all(ops, **kw):
    return Schedule(tuple(Edge("reg") for _ in range(len(ops) - 1)), **kw)

def stage_all(ops, **kw):
    return Schedule(tuple(Edge("buf") for _ in range(len(ops) - 1)), **kw)

def stage_at(sched, i, rep="dense"):
    """Demote one edge from reg to buf. ROF is one application of this."""
    edges = list(sched.edges)
    edges[i] = Edge("buf", rep)
    return replace(sched, edges=tuple(edges))

def with_rep(sched, i, rep):
    edges = list(sched.edges)
    edges[i] = replace(edges[i], rep=rep)
    return replace(sched, edges=tuple(edges))

def regions(ops, sched):
    """Split the pipeline at buf edges: a region is a maximal run of ops
    connected by reg edges."""
    out, start = [], 0
    for i, e in enumerate(sched.edges):
        if e.loc == "buf":
            out.append((start, i + 1))
            start = i + 1
    out.append((start, len(ops)))
    return out

def in_rep(sched, op_index):
    # the representation flowing into ops[i] is the annotation of the
    # edge before it; the scan always produces dense vectors
    return "dense" if op_index == 0 else sched.edges[op_index - 1].rep

def out_rep(sched, op_index, nops):
    return None if op_index == nops - 1 else sched.edges[op_index].rep

# -------------------------------------------------------------- boundary key

def shape(rhs):
    return "const" if isinstance(rhs, int) else "col"

def region_key(ops, sched, start, end):
    """What identifies a compiled block: the suboperators inside the
    region and the representation of the edges crossing its boundary.
    Constants and column names are runtime parameters, not part of the
    key (the static/dynamic split)."""
    parts = []
    for i in range(start, end):
        o = ops[i]
        if isinstance(o, Filter):
            parts.append(("filter", o.op, shape(o.rhs)))
        elif isinstance(o, Map):
            parts.append(("map", o.op, shape(o.rhs)))
        elif isinstance(o, Compact):
            parts.append(("compact",))
        else:
            parts.append(("fold", "+"))
    rin = in_rep(sched, start)
    rout = out_rep(sched, end - 1, len(ops))
    if end - start == 1:
        return (*parts[0], rin, rout)
    return ("fused", tuple(parts), rin, rout)

# ------------------------------------------------------- chunks and columns

@dataclass
class Chunk:
    cols: dict          # name -> list of ints, position-aligned
    n: int
    sel: list = None    # active indices, or None when dense

def indices(chunk):
    return chunk.sel if chunk.sel is not None else range(chunk.n)

def written_bytes(chunk):
    """Bytes a block wrote to produce this chunk (8-byte values; a sel
    output writes only the index list, the columns are shared)."""
    if chunk.sel is not None:
        return 8 * len(chunk.sel)
    return 8 * chunk.n * len(chunk.cols)

# ------------------------------------------------- tier 1: the block library
#
# Each block is closed over (op symbol, operand shape, representations)
# only. Column names and constants arrive at call time, so no block
# knows anything about Q6.

def make_filter(sym, rin, rout):
    fn = OP_FNS[sym]
    def block(chunk, o, needed):
        rhs = o.rhs if isinstance(o.rhs, int) else None
        lhs = chunk.cols[o.lhs]
        rhs_col = None if rhs is not None else chunk.cols[o.rhs]
        keep = [i for i in indices(chunk)
                if fn(lhs[i], rhs if rhs is not None else rhs_col[i])]
        if rout == "sel":
            return Chunk(chunk.cols, chunk.n, keep)
        cols = {c: [chunk.cols[c][i] for i in keep]
                for c in chunk.cols if c in needed}
        return Chunk(cols, len(keep), None)
    return block

def make_map(sym, rep):
    fn = OP_FNS[sym]
    def block(chunk, o, needed):
        lhs = chunk.cols[o.lhs]
        rhs = o.rhs if isinstance(o.rhs, int) else chunk.cols[o.rhs]
        get = (lambda i: rhs) if isinstance(o.rhs, int) else (lambda i: rhs[i])
        out = [0] * chunk.n
        for i in indices(chunk):
            out[i] = fn(lhs[i], get(i))
        cols = {c: v for c, v in chunk.cols.items() if c in needed}
        cols[o.out] = out
        return Chunk(cols, chunk.n, chunk.sel)
    return block

def make_fold(rep):
    def block(chunk, o, state):
        col = chunk.cols[o.col]
        state["acc"] += sum(col[i] for i in indices(chunk))
    return block

def make_compact():
    def block(chunk, o, needed):
        if chunk.sel is None:
            return chunk
        cols = {c: [chunk.cols[c][i] for i in chunk.sel]
                for c in chunk.cols if c in needed}
        return Chunk(cols, len(chunk.sel), None)
    return block

def build_library():
    lib = {}
    for sym in ("+", "*", "<", "<=", ">="):
        for sh in ("const", "col"):
            for rin, rout in (("dense", "dense"), ("dense", "sel"),
                              ("sel", "sel")):
                lib[("filter", sym, sh, rin, rout)] = make_filter(sym, rin, rout)
            for rep in ("dense", "sel"):
                lib[("map", sym, sh, rep, rep)] = make_map(sym, rep)
    for rep in ("dense", "sel"):
        lib[("fold", "+", rep, None)] = make_fold(rep)
    # The one coercion: the representations disagree, so something has to
    # convert, and the conversion is a block like any other.
    lib[("compact", "sel", "dense")] = make_compact()
    # State layouts: insert/probe enumerated per bucket format, the
    # same presence rule applied to state instead of edges.
    lib[("insert", "open")] = build_open
    lib[("insert", "chained")] = build_chained
    lib[("probe", "open", "dense", "sel")] = probe_open
    lib[("probe", "chained", "dense", "sel")] = probe_chained
    # No fused key is ever inserted. A schedule whose region spans more
    # than one suboperator finds nothing here: legality by presence.
    return lib

# ---------------------------------------------- state layouts (Q14 preview)
#
# The pipeline executor handles straight pipelines only, but the key
# discipline extends to stateful suboperators unchanged: a probe
# block's loop dereferences one specific bucket format, so the format
# is part of its boundary key, exactly as a map block's key names the
# representation of its input edge. These blocks are not wired into
# plan(); layout_demo() exercises them directly.

EMPTY = -1

def build_open(keys, payloads, bits):
    """(insert, layout="open") — open addressing with linear probing:
    two flat arrays, collisions resolved by walking to the next slot."""
    mask = (1 << bits) - 1
    ht_keys = [EMPTY] * (1 << bits)
    ht_pay = [0] * (1 << bits)
    for k, p in zip(keys, payloads):
        h = hash(k) & mask
        while ht_keys[h] != EMPTY:
            h = (h + 1) & mask
        ht_keys[h], ht_pay[h] = k, p
    return {"keys": ht_keys, "pay": ht_pay, "mask": mask}

def build_chained(keys, payloads, bits):
    """(insert, layout="chained") — bucket heads pointing into entry
    arrays, collisions resolved by a next-pointer chain."""
    mask = (1 << bits) - 1
    heads = [EMPTY] * (1 << bits)
    ekey, epay, enext = [], [], []
    for k, p in zip(keys, payloads):
        h = hash(k) & mask
        ekey.append(k); epay.append(p); enext.append(heads[h])
        heads[h] = len(ekey) - 1
    return {"heads": heads, "ekey": ekey, "epay": epay,
            "enext": enext, "mask": mask}

def probe_open(keys_in, ht):
    """(probe, layout="open", in: dense, out: sel)"""
    hits, pay = [], []
    for i, k in enumerate(keys_in):
        h = hash(k) & ht["mask"]
        while ht["keys"][h] != EMPTY:      # scan the linear-probe run
            if ht["keys"][h] == k:
                hits.append(i); pay.append(ht["pay"][h])
                break
            h = (h + 1) & ht["mask"]
    return hits, pay

def probe_chained(keys_in, ht):
    """(probe, layout="chained", in: dense, out: sel)"""
    hits, pay = [], []
    for i, k in enumerate(keys_in):
        e = ht["heads"][hash(k) & ht["mask"]]
        while e != EMPTY:                  # walk the chain
            if ht["ekey"][e] == k:
                hits.append(i); pay.append(ht["epay"][e])
                break
            e = ht["enext"][e]
    return hits, pay

def layout_demo(lib):
    """Build the same table in both formats, probe with the matching
    block, and show the presence rule for a layout nobody enumerated."""
    rng = random.Random(14)
    part_keys = list(range(0, 400, 2))
    payloads = [k * 10 for k in part_keys]
    probe_keys = [rng.randrange(400) for _ in range(1000)]
    results = {}
    for layout in ("open", "chained"):
        ht = lib[("insert", layout)](part_keys, payloads, bits=10)
        results[layout] = lib[("probe", layout, "dense", "sel")](probe_keys, ht)
    assert results["open"] == results["chained"]
    print(f"   open == chained: {len(results['open'][0])} hits "
          f"out of {len(probe_keys)} probes")
    missing = ("probe", "robinhood", "dense", "sel")
    assert missing not in lib
    print(f"   boundary key {missing} is not in the library; a schedule "
          f"declaring that layout is illegal on the precompiled tier")

# --------------------------------------------------------- tier 2: the JIT
#
# Generates Python source for one fused region, specialized to this
# query (column names and constants baked in, the way HyPer would),
# then exec()s it.

def operand(o_rhs, locals_):
    if isinstance(o_rhs, int):
        return str(o_rhs)
    if o_rhs in locals_:
        return locals_[o_rhs]
    return f"{o_rhs}[i]"

def jit_region(ops, sched, start, end, needed_out, name):
    body_ops = ops[start:end]
    read, produced = set(), set()
    for o in body_ops:
        if isinstance(o, Compact):   # a fused loop is already dense
            continue
        if isinstance(o, Fold):
            if o.col not in produced:
                read.add(o.col)
        else:
            if o.lhs not in produced:
                read.add(o.lhs)
            if isinstance(o.rhs, str) and o.rhs not in produced:
                read.add(o.rhs)
            if isinstance(o, Map):
                produced.add(o.out)
    has_fold = isinstance(body_ops[-1], Fold)
    if not has_fold:
        read |= set(needed_out) - produced
    read_cols = sorted(read)
    src = [f"def {name}(chunk, state):"]
    for c in read_cols:
        src.append(f"    {c} = chunk.cols['{c}']")
    if not has_fold:
        for c in sorted(needed_out):
            src.append(f"    out_{c} = []")
    else:
        src.append("    acc = state['acc']")
    src.append("    idx = chunk.sel if chunk.sel is not None"
               " else range(chunk.n)")
    src.append("    for i in idx:")
    locals_ = {}  # columns produced by maps inside this region
    for o in body_ops:
        if isinstance(o, Compact):
            continue
        if isinstance(o, Filter):
            lhs = locals_.get(o.lhs, f"{o.lhs}[i]")
            src.append(f"        if not ({lhs} {o.op} "
                       f"{operand(o.rhs, locals_)}): continue")
        elif isinstance(o, Map):
            lhs = locals_.get(o.lhs, f"{o.lhs}[i]")
            var = f"v_{o.out}"
            src.append(f"        {var} = {lhs} {o.op} "
                       f"{operand(o.rhs, locals_)}")
            locals_[o.out] = var
        else:
            val = locals_.get(o.col, f"{o.col}[i]")
            src.append(f"        acc += {val}")
    if not has_fold:
        for c in sorted(needed_out):
            val = locals_.get(c, f"{c}[i]")
            src.append(f"        out_{c}.append({val})")
        outs = ", ".join(f"'{c}': out_{c}" for c in sorted(needed_out))
        first = sorted(needed_out)[0]
        src.append(f"    return Chunk({{{outs}}}, len(out_{first}), None)")
    else:
        src.append("    state['acc'] = acc")
    text = "\n".join(src)
    ns = {"Chunk": Chunk}
    exec(text, ns)
    return ns[name], text

# ---------------------------------------------------------------- executor

class IllegalSchedule(Exception):
    pass

def cols_needed_after(ops, end):
    """Which columns must cross the boundary after `end`: everything a
    downstream op reads that a downstream map doesn't itself produce."""
    need, produced = set(), set()
    for o in ops[end:]:
        if isinstance(o, Compact):   # reads no column, produces none
            continue
        if isinstance(o, Fold):
            if o.col not in produced:
                need.add(o.col)
        else:
            if o.lhs not in produced:
                need.add(o.lhs)
            if isinstance(o.rhs, str) and o.rhs not in produced:
                need.add(o.rhs)
            if isinstance(o, Map):
                produced.add(o.out)
    return need

def plan(ops, sched, lib, trace=False):
    """Resolve every region to a callable and a tier. This is the whole
    compiler: split at buf edges, look each region up, JIT the misses."""
    resolved = []
    for k, (start, end) in enumerate(regions(ops, sched)):
        key = region_key(ops, sched, start, end)
        needed = cols_needed_after(ops, end)
        if key in lib and sched.tier != "jit":
            block = lib[key]
            if isinstance(ops[start], Fold):
                fn = lambda c, st, b=block, o=ops[start]: b(c, o, st)
            else:
                fn = lambda c, st, b=block, o=ops[start], nd=needed: \
                    b(c, o, nd)
            tier = "precompiled"
            src = None
        elif sched.tier == "precompiled":
            raise IllegalSchedule(
                f"boundary key {key} is not in the library; this schedule "
                f"is illegal on the precompiled tier")
        else:
            fn, src = jit_region(ops, sched, start, end, needed,
                                 f"region{k}")
            tier = "jit"
        if trace:
            print(f"  region {k}: ops[{start}:{end}] tier={tier} key={key}")
            if src:
                print("\n".join("  | " + l for l in src.splitlines()))
        resolved.append(fn)
    return resolved

def tier_of(ops, sched, lib):
    """Where a schedule's code comes from, without running it: every
    region resolved by lookup, or some region left to the JIT."""
    if sched.tier != "auto":
        return sched.tier
    keys = [region_key(ops, sched, s, e) for s, e in regions(ops, sched)]
    hits = sum(k in lib for k in keys)
    return ("precompiled" if hits == len(keys)
            else "jit" if hits == 0 else "mixed")

def execute(table, ops, sched, lib, trace=False):
    fns = plan(ops, sched, lib, trace)
    n = len(next(iter(table.values())))
    mapped = {o.out for o in ops if isinstance(o, Map)}
    scan_cols = cols_needed_after(ops, 0) - mapped
    state = {"acc": 0}
    boundary_bytes = 0
    for lo in range(0, n, sched.vector):
        hi = min(lo + sched.vector, n)
        chunk = Chunk({c: table[c][lo:hi] for c in scan_cols}, hi - lo, None)
        for fn in fns:
            out = fn(chunk, state)
            if out is not None:
                boundary_bytes += written_bytes(out)
                chunk = out
    return state["acc"], boundary_bytes

# --------------------------------------------------------------- reference

def reference(table, ops):
    """Row at a time, schedule-ignorant. The oracle every schedule and
    tier must agree with."""
    n = len(next(iter(table.values())))
    acc = 0
    for i in range(n):
        row = {c: v[i] for c, v in table.items()}
        for o in ops:
            if isinstance(o, Filter):
                rhs = o.rhs if isinstance(o.rhs, int) else row[o.rhs]
                if not OP_FNS[o.op](row[o.lhs], rhs):
                    break
            elif isinstance(o, Map):
                rhs = o.rhs if isinstance(o.rhs, int) else row[o.rhs]
                row[o.out] = OP_FNS[o.op](row[o.lhs], rhs)
            elif isinstance(o, Compact):
                pass                    # representation has no meaning row at a time
            else:
                acc += row[o.col]
    return acc

# --------------------------------------------------------------------- demo

def make_lineitem(n=50_000, seed=6):
    rng = random.Random(seed)
    return {
        "shipdate": [rng.randrange(8400, 9500) for _ in range(n)],
        "discount": [rng.randrange(0, 11) for _ in range(n)],
        "quantity": [rng.randrange(1, 51) for _ in range(n)],
        "extprice": [rng.randrange(100, 10_000) for _ in range(n)],
    }

def main():
    table = make_lineitem()
    lib = build_library()
    oracle = reference(table, Q6)
    print(f"library: {len(lib)} blocks, all single-suboperator")
    print(f"reference (row at a time, no schedule): revenue = {oracle}\n")

    # The paradigms as schedule values. ROF really is HyPer plus one
    # edit, and the sel-vector interpreter is the dense one with the
    # representation flipped on the post-filter edges.
    hyper = fuse_all(Q6)
    vec = stage_all(Q6)
    vec_sel = vec
    for i in range(len(Q6) - 1):
        vec_sel = with_rep(vec_sel, i, "sel")
    rof = stage_at(hyper, 4)  # buffer between the filters and map+fold

    named = [("hyper = fuse_all(q6)", hyper),
             ("vec = stage_all(q6)", vec),
             ("vec_sel = vec + sel edges", vec_sel),
             ("rof = stage_at(hyper, 4)", rof)]

    for label, sched in named:
        print(f"-- {label}")
        result, _ = execute(table, Q6, sched, lib, trace=(sched is hyper))
        ok = "ok" if result == oracle else "MISMATCH"
        print(f"   revenue = {result} [{ok}], tier = {tier_of(Q6, sched, lib)}\n")
        assert result == oracle

    # Legality by presence: the fused schedule pinned to the
    # precompiled tier asks for a boundary key nobody enumerated.
    print("-- hyper pinned to the precompiled tier")
    try:
        execute(table, Q6, replace(hyper, tier="precompiled"), lib)
    except IllegalSchedule as e:
        print(f"   {e}")

    # The same presence rule applied to state formats.
    print("\n-- physical layouts (Q14 preview)")
    layout_demo(lib)

if __name__ == "__main__":
    main()

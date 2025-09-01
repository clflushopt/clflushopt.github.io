---
title: "Looking at main-memory join algorithms - brief tour"
summary: "Notes and things I learned from working on the SIGMOD 2025 contest."
date: 2025-05-07
---

# Where it all started.

When I am out of things to play with I like sampling project ideas from Alex
Miller's [archive of SIGMOD contests](https://transactional.blog/sigmod-contest/).

I like the SIGMOD contests for two reasons, first they are well defined and scoped
and have a baseline test harness to work from and second they are database problems.

So a few weeks ago when I found out about the 2025 challenge I decided it would
be a great time to jump in. Especially since I spent the month or two before doing
some non-database stuff so I was itching for a relatively small project before going
back to my other non-small projects.

## The work at hand.

The original task description can be found [here](https://transactional.blog/sigmod-contest/2025) but
tl;dr we need to implement an in-memory join pipeline that will, part of the larger system, execute
queries from the [JOB](https://www.vldb.org/pvldb/vol9/p204-leis.pdf) benchmark.

The challenge constrains your changes to be on a single file [execute.cpp](https://github.com/SIGMOD-25-Programming-Contest/base/blob/main/src/execute.cpp)
and disallows using third-party libraries[^1].

All benchmarks and tests were ran on a Ryzen 9950X machine with 64GBs of memory running on Fedora.

```c++
// Architecture from `uname -srm`.
#define SPC__X86_64

// CPU from `/proc/cpuinfo`.
#define SPC__CPU_NAME "AMD Ryzen 9 9950X 16-Core Processor"

// AMD Ryzen 9 9950X is single node and has 16 cores and 32 threads.
#define SPC__CORE_COUNT                     16
#define SPC__THREAD_COUNT                   32
#define SPC__NUMA_NODE_COUNT                1
#define SPC__NUMA_NODES_ACTIVE_IN_BENCHMARK 1

// Main memory per NUMA node (MB).
#define SPC__NUMA_NODE_DRAM_MB 61884

// Obtained from `lsb_release -a`.
#define SPC__OS "Fedora Linux 41"

// Obtained from: `uname -srm`.
#define SPC__KERNEL "Linux 6.13.5-200.fc41.x86_64 x86_64"

// AMD: possible options are AVX, AVX2, and AVX512.
#define SPC__SUPPORTS_AVX

// Cache information from `getconf -a | grep CACHE`.
#define SPC__LEVEL1_ICACHE_SIZE 32768
#define SPC__LEVEL1_ICACHE_ASSOC
#define SPC__LEVEL1_ICACHE_LINESIZE 64
#define SPC__LEVEL1_DCACHE_SIZE     49152
#define SPC__LEVEL1_DCACHE_ASSOC    12
#define SPC__LEVEL1_DCACHE_LINESIZE 64
#define SPC__LEVEL2_CACHE_SIZE      1048576
#define SPC__LEVEL2_CACHE_ASSOC     16
#define SPC__LEVEL2_CACHE_LINESIZE  64
#define SPC__LEVEL3_CACHE_SIZE      33554432
#define SPC__LEVEL3_CACHE_ASSOC     16
#define SPC__LEVEL3_CACHE_LINESIZE  64
#define SPC__LEVEL4_CACHE_SIZE      0
#define SPC__LEVEL4_CACHE_ASSOC
#define SPC__LEVEL4_CACHE_LINESIZE
```

## Initial thoughts


[^1]: I might have cheated here, so because of a lack of time since I mostly
did this on some nights and weekends I used an off the shelf hashmap instead
of writing my own.

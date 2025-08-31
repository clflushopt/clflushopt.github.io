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
long story short we need to write an in-memory join pipeline that has to be fast. Now, the codebase we
have to work in is in C++ so that means I won't be very happy while doing this but since I wanted to
finish before the submission deadline, because after the deadline the leaderboard was taken down, I wasn't
going to port it to Rust before working on it.

The challenge itself constrains you to touching a single file [execute.cpp](https://github.com/SIGMOD-25-Programming-Contest/base/blob/main/src/execute.cpp)
and you are not allowed the use of third-party libraries[^1].

Now like every C++ projects you end up working on, chances are it won't build from the get go which was
the case here since there's an include for `<hardware.h>` which doesn't exist in the original repo this
was easy to fix, just write our own file with our own machine specs.

My machine in this case my desktop equipped with a Ryzen 9950X all benchmarks and numbers were computed
on it so you might have a different baseline than I did and it's also a bit tricky to compare to the open
leaderboard but still.

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
#define SPC__OS "Fedora Linux 41 (KDE Plasma)"

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

Now that the code is building we can finally start tinkering about it, this will be fun !

# Query execution models in database systems


[^1]: I might have cheated here, so because of a lack of time since I mostly
did this on some nights and weekends I used an off the shelf hashmap instead
of writing my own.

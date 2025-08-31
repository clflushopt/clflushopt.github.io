---
title: "Understanding type guards in tracing JIT compilation"
summary: "How a JIT compilers collect type information and use to speculatively compile fast code."
date: 2025-05-07
---

JIT compilers in all shapes or form are some of my favorite topics - the idea
of running code targeting an architecture on a machine using a different one
has fascinated me since I've learned about it for the [first time](https://web.stanford.edu/class/cs343/resources/binary-translation.pdf).

My goal today is 

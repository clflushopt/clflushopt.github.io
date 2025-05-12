---
title : "Learning compilers - lessons learned 6 months later"
description: "Things I learned when working on compilers"
summary: "What happens when you start writing compilers."
date: 2024-12-31
draft: true
---

More than six months ago I developped an itch, I wanted to learn more about
compilers and so it went.

This wasn't my first time exploring compilers, I've had a course at college
from which I don't remember much but it got me curious to pick up books
and discover more. But even after writing toy DSLs, interpreters and even
emitting enough line of assembly to sum two numbers I still didn't feel
satisfied.

I wanted the whole experience from writing a scanning engine to emitting IR
and running optimization passes on the IR before doing code generation.
But because it didn't feel enough I also wanted to learn about JIT compilers.

This is an informal walk through my experience, I won't explain compilers
or anything compiler related. I will instead describe quirks, shortcomings
and all the failures I've had along the way.

This post serves as many things :

* Review about compiler books.
* Why is it so hard to write a JIT compiler.
* x86, ARM64, macOS and Linux internals, ABIs and a lot of useless knowledge.
* Compiler data structures, terminology and other dark wizard jargon.

## Rocky beginnings

I started working on two projects in parallel a *runtime* for a subset of
JavaScript and a *compiler* for a language I stitched up together one night
called [axion](https://github.com/jmpnz/axion).

It looks like any "hacked in one night" language, but with four atomic types,
basic control flow and function calls we had the foundations for what could
be a world changing language.

The following snippet contains most of the important stuff. You will notice
that there are no record types, classes, methods or postfix operators.

```rust
use serde::Deserialize;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Data {
    #[serde(rename = "statusCode")]
    status_code: Option<i32>,
    error: Option<String>,
    message: Option<String>,
    disruptions: Option<Vec<Disruption>>,
    lines: Option<Vec<Line>>,
    #[serde(rename = "lastUpdatedDate")]
    last_updated_date: Option<String>,
}
```

```javascript
// This is a global variable
let answer : integer = 42;

// This is a function that demonstrates loops and contionals.
function sum_until_answer(x:integer) -> integer {
    while (x != 42) {
        if (x > 42) {
            x = x - 1;
        } else {
            x = x + 1;
        }
    }
    return x;
}

// This function demonstrates scoping rules and function calls.
function is_it_the_answer(answer:integer) -> boolean {
    if (answer == 42) {
        return true;
    } else {
        answer = sum_until_answer(answer);
        return true;
    }
}

// The "main" function defines the program entry point.
function main() -> void {
    is_it_the_answer(answer);
}
```

## We have an AST

While the compiler does end up generating x86 assembly that can be built as
an object file, and executed on a machine I still needed to write tests
to ensure that the generated assembly produces the correct results

One way to test the code generation part is to write an emulator for whatever
the subset of assembly I was using. Since I wasn't using the entire x86
instruction test this turned out to be easy and rewarding.

The emulator implemented in `emu.rs` is about 500 lines of code, it processes
a textual stream of assembly and emulates the instruction. The final emulator
state which contains an MMU and the usual registers can then be used to ensure
the returned value is correct.

But before we start talking about all the ways codegen works there's still
a middle end, the IR generation pass that I didn't mention. `axion` uses Bril
as an IR and does various optimization passes on the IR before generating the
final assembly. [Bril](https://capra.cs.cornell.edu/bril/) is an educational IR
for [CS6120](https://www.cs.cornell.edu/courses/cs6120/2022sp/self-guided/) a
compilers class I highly recommend checking out.

Using Bril turned out to be a no brainer and by watching the lectures and doing
the assignments (I used Python) you end up with a pretty good understanding of
compilers. The IR generation pass and optmization passes are implemented in
`ir.rs` and `optim.rs` respectively.

## The elephant in the room

One thing I failed to mention was that the compiler was written in Rust, which
in retrospect turned out to be an exellent choice !

While working on the runtime I used C++ for two reasons, the first is memory
management and the second was old habits. By memory management I don't mean it
in the nice way, but more like C++ has enough freedoms about memory management
that writing a toy garbage collector is easy. If you add `-fsantizie=address`
you pretty much have something that is more or less debuggable. It takes a lot
of time and patience but you can make it.

I don't have experience with writting a GC in Rust, but if I had to do it again
I would pick Rust. Mostly, because I now have more experience and understand the
kind of architectural designs the Rust borrow checking rules imposes on you.

But for the compiler (no runtime) Rust turned out to be an exellent choice, it
feels like OCaml which I find great for this kind of work but at the same time
has great docs, a large community and exellent tooling above all else.

## Things worthy of exploration

Writing a compiled language without something like LLVM isn't an easy task.
The implementer needs to be familiar with file formats across the different
target triples, ISA's for any architecture they wish to support and many other
details. While it was rewarding to work on `axion`, it wasn't really fun as I
spent more time on things that weren't that much **fun** but just tedious
and complex.

Writing an interpreted language on the other hand is so much more fun as
a greenfield project, an interpreter is a fertile ground to understand software
performance and practice it.

It is also much easier to innovate on and iterate, whether on the runtime
itself or language features, whichever picks your interest.

I think if I were to write another programming language, It would be one with
a runtime and interpreter and vm so I don't have to deal with all the quirks of build
environments and linking and can spend more time having *fun*.

While reading on the topic of faster interpreters I found a few really good
references that I wasn't expecting the Lua 5.0 technical paper which can be
found [here](https://www.lua.org/doc/jucs05.pdf) and the [MoarVM for Perl
6](https://github.com/MoarVM/MoarVM).

I also found the [V8 blog](https://v8.dev/blog) to be a really good reference
all around, not just if you're working on interpreters and runtimes but any
software engineer would learn a lot about the design trade offs and how they
approach things. Here's a list of some of my favorite blog posts.

- [Pointer compression in Oilpan](https://v8.dev/blog/oilpan-pointer-compression)
- [Heap snapshots](https://v8.dev/blog/speeding-up-v8-heap-snapshots)
- [Garbage Collection in C++](https://v8.dev/blog/high-performance-cpp-gc)
- [Pointer compression in V8](https://v8.dev/blog/pointer-compression)
- [Faster parsing](https://v8.dev/blog/scanner)
- [Background compilation](https://v8.dev/blog/background-compilation)...

## References

The following books and papers were invaluable in getting a better understanding
of how everything pieces together. While `axion` doesn't implement any optimizations
such as hoisting, strength reduction or register allocation it was still helpful
to learn and pick up the concepts.

Books on type theory and programming languages were very hard to put into practice
but I got more than I wanted from Pierce's Types and Programming Languages and
The Little Type. I didn't read the entire thing but a few chapters from both helped
get a better understanding about what types are and how type checking rules can
be thought of.

* Daniel P.Friedman and David Thrane [The Little
  Typer](https://mitpress.mit.edu/9780262536431/the-little-typer/)

* Benjamin Pierce [Types and Programming Languages](https://www.cis.upenn.edu/~bcpierce/tapl/)

* Chris Lattner [LLVM](https://dl.acm.org/doi/10.5555/977395.977673)

* Aho et al. [Code Generation using Tree Matching and Dynamic
  Programming](https://dl.acm.org/doi/10.1145/69558.75700)

* Poletto [Linear Scan Register Allocation](https://dl.acm.org/doi/10.1145/330249.3302500)

* Chaitin [Register Allocation & Spilling via Graph
  Coloring](https://dl.acm.org/doi/10.1145/800230.806984)

## Method and Tracing JIT compilers an introduction.

The guide I wish existed when I wanted to understand JIT compilers.

## Why write a JIT ?

Well I assume the answer is [because you can](https://youtu.be/dxTNqYAWISs?t=30).

## Prelude

This blog post will try and explain all the ways you can write a JIT for many
types of use cases. I will discuss interpreters with both *method* and *tracing*
JITs, I will discuss an amalgamation of jargon that is used to describe JITs
and will also show how JITs can be used to write emulators which is also cool.

## What is the JIT

JIT or Just-in-time in computer science often refers to just in time compilation
which is used to build self contained native assembly code **while** interpreting
an AST (rare) or bytecode.

The reason you might want to write a JIT is *primarly* speed, because writing a JIT
is a complicated endeavor. Not because JITs require a kind of knowledge that you can
only acquire by reaching a wise toad at the top of a mountain, but it's something
close.

Let's first discuss where the complication comes from when writing a JIt, bear with me
this will help us later on.

There are 4 main reasons writing a JIT is hard :

* It's not *architecture independant* for each architecture you plan to target you
need to write something akin to a *JIT library* that takes assembly instructions
in some form (declarative API like asm::jit or macro based like dynasmrt) and spits
out assembled instructions (bytes). Now that you have the bytes you need to choose
which OS you plan to target, because well, the assembly instruction bytes will need
to be written to memory (aligned to a page of course, duh.) and then marked as executable
(using macOS, good luck !). Then you have to *cast* the bytes to a function pointer
and call that function, of course you should have taken ABI considerations into account
aligned on 8 bytes instead of 16 ? Well SEGFAULT. Using Windows, did you allocate shadow
space ? You want to support floating point arguments, just give up.

* It's pretty much impossible to debug, your code will interpret some bytecode and
  and move some data around in structs. You then find a function worth of the task
  so you run the codegen, you assemble the bytes and go through the ritual of putting
  all the pieces together before calling your function. Then what ? Well that's
  where you will remember *oh my JIT has no calling convention, oh wait did I pass
  a pointer to the VM stack in assembly ? Wait, why is this pointer NULL, SEGFAULT.

* Assuming you've managed to do all the above, the next thing on your checklist
  would be, should I only support leaf functions ? By leaf functions, I mean functions
  that don't call other functions. If the answer is yes, then good for you, if
  the answer is no, then well you have to think about SEGFAULT.

* The fourth reason, is that dealing with all the above is such a pain that sometimes
  you will feel like abondonning, but fear not, we are here to conquer fear and write
  a **** JIT.

## Writing a JIT in 9 steps

Before we write a JIT let's invent our own programming language, our language
will not be like those toy arithmetic calculator that serve the same purpose
as `bc` but will instead have everything a nicely behaved programming language
would have. Functions, loops, conditionals and primitive types.

Here's a snippet that contains everything our language will contain :

```javascript

function doPainfulThings(a,b,c,d,e)  {
    sum = 0;
    for (i = 0;i < 100;i++) {
        if (a > b && c < d) {
            sum += a * b + c * d;
        } else {
            sum += i;
        }
    }
    return sum;
}

```

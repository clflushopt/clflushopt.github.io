
+++
title = "Approaching Agentic Programming"
description = "Notes and reflections on the magical technology blessed upon us."
date = 2026-06-09
draft = true
tags = ["programming", "opinion"]
+++

I really don't know why I am writing this now, I have resisted chiming in publicly on this for nearly 3 years now or
more or less. But I feel like I've been seeing too much opinions on both extremes that sadly scare me but not for
the reasons you'd think. They also make me quite excited for the future, that I think we should all embrace that of
SLot Oriented Programming.

I want to preface everything by saying a bit about myself, I've been doing machine learning in some way or from since
2016. I've read the books and done the courses, I worked through their assignments and implemented the algorithms an
even more funny fact is that I did my MSc on fine-tuning early language models BERT, T5 and the such on NL2SQL datasets
to create a model that would have been blased by GPT-3 on release. I've read the Attention paper, the Annotated Attention
paper then I worked through Karpathy's mini-course then I worked through Flash Attention and realized that whew things
are finally getting serious and then I was hit by the DeepSeek train. So I trained my little models then fine-tuned them
and distilled from other models and did "We have RLHF at Home". It was a lot of fun but it wasn't something that truly
captured me once I understood how it works, for some reason Machine Learning just never clicked for me enough to consume
my time. I unfortunately have the rare database disease and I am consumed by those so I am spending most of my time digging
around them and yes I know that in 6 months all databases will be replaced by LLMs because you don't need SQL when you
can just ask in English but for some reason I just can't seem to accept that fact and I keep somewhat pondering databases.

I learned about prompt engineering when it was going to be our job and then unlearned everything when reasoning models
became the norm, I did the build your own harnesses thing for a while then converged on Claude Code, Pi then Claude Code
again. So I want you to know, internally understand that I actually been there, did that and I am not speaking out of
surface level knowledge, this is not based on vibes.

## Hmm this looks peachy

My first ever experience with consumer oriented LLMs i.e. Chat GPT was in February 2023 when it came up during a work
conversation. One of my co-workers at that time mentionned how it was good at being a DM and co-hosting his DnD parties
so once I came back home later that day and given I don't actually care about DnD I signed up and proceeded to ask the
Wizard a question that was nagging at me at the time; "I have this small need for a DSL to represent assembly micro-kernels
and I heard about this thing called copy and patch can we use this to hot-time patch some query operators at runtime".

Now understand that at this moment I didn't expect much and yet the little model that could wrote this 50 lines example
of a how to do this for an equality operator (I was toying with a vectorized boolean expression runtime at the time). It
didn't work and it was a bit wonky but I copied the output into Vim and kept hammering it until I ended up with what I
had in my mind in "thought form" but skipped the "sit down and make it concrete part" by using this little wizard to do
a prototype for me.

Fast forward to today and I use Claude Code almost every day, both at work and at home. But why then am I disillusioned
with what everyone seems to try and sell me and why am I not worried about being replace ? And why do I think opinions
such as "coding is solved" and "programming will be solved in 6 months 6 months ago" or "you don't need to understand it"
are just water down the stream and not a nugget of golden wisdom spoken by the wise monk at the top of the mountain ?

I think the answer to that question comes down, to its essence to two facts :

1. People are talking their book (in the finance sense).
2. People at least most vocal ones are not actually good programmers.

## Is it good at something

Most surprising to me the answer machine is actually very good at finding answers. One of my favorite things about using
Claude is that I can formulate a search query in the most vague sense possible "I know there's somewhere in Spark where
I can hook on a given optimizxation rule in Catalyst and export it somewhere" and it will come up with an answer and a
very accurate one. In fact digging around large codebases has become quite trivial, I barely use `rg` or `grep` nowadays.

Another thing I found great use for is surfacing bugs, while most people believe software is actually tested and runs
under a quality assurance process; the reality is that not all software is tested and sometimes a trivial bug can sit
there for a while just because no one has exercised the proper incantation. Sure "deterministic simulation testing" as
hot as it is can be useful here but the reality is almost no one writes software with that in mind, people don't sit
around at write `TimeProvider` interfaces so they can later inject faults in it. Most code out there just calls
`time.Now()`. As tough a pill to swallow no one in the right mind reads +26358,-8674 changes, back in the day we used
to request changes, push back for the change to be split then wait 2 weeks for all the merge conflicts to be fixed then
finally spend two weeks reviewing the change set with occasional 3 day breaks to fix more merge conflicts in between.
But nowadays Claude can slurp the change and find most of what your average engineer out there would think of as issues.

I mean sure we are not writing masterpieces and I understand the value of the first approach and how in an ideal world
software can be written like monks transcribed the bible but the reality is in your day to day job the change will often
be what keeps the lights on and the soup hot. It's just something you learn to live with as you grow reality is very
imperfect and software is very messy so sacrifices must be made. But if its your own software that you treat like your
own creation a tableau on which you work during your own time, then do the code review, push against the change or just
don't ship it.

I posit this as a scenario because we have all been there and we have all come to gripes with it; but the reality is
shareholder value is what we are technically paid to work. The fact that sometimes we end up painting a masterpiece is
just the occasional work accident, after all swathes of developers come and go in a codebase and hard is the toil of
maintaining it during all that time but it is what it is.

## Where are you going

Sorry I feel like I ranted way too much in that last one, hopefully if you are reading this and feel it's you do not
feel bad we are all that person.

Back to Claude, I think one facet of this that folks fail to capture is that you have to think of it as a slot machine
even if everyone else tells you it's not a slot machine but an actual intelligent, reasoning being. I mean come on read
the research there must be some reasoning for it to do that.

This might feel counter-intuitive you are reading research (hopefully its true because no one has or actually can reproduce
meaningfully or replicate so let's just call it wink-wink-science) but at the same time I am pushing the slot machine
argument. Its very simple, if you have used agents for coding you have felt it; that metallic taste of randomness that
persists in the air after you tell it "please fix this and for the last time make no mistakes" for the n-th time in a
session. I myself have found myself in this situation more times than I can recall before I learned that this slot
machine can be hacked to produce what I want.

If you imagine yourself sitting in front of a problem and thinking of a solution then that solution is just one dot
in solution-space, or rather a **path**. You can take the path and each one of your colleagues might come up with a
different one, if you collect 10,000 engineers and put them in front of your problem you will get different solutions
with varying "tastes" and Claude is effectivelly 10,000 enginners only they are not crammed in an additive manner.

So what you do is "prompt", "hint" and keep truncating solution space until you land on the solution that you might
have come up with then you being a perfectionist would make a change here and there and the problem is fixed.

Of course it's not that clear cut because the model likes to yap and yap it will, if you don't understand your problem
then the agent can't help you there because it can't really understand what you can't understand at least not in a way
that can be meaningfully applied.

The reason most AI code out there is slop is not really an artefact of the model as it is an artefact of the user and
the reality, the tough pill to swallow for most is this. Because you don't understand, you have no mouth and yet you must
scream.

## Beyond the tool there are principles (for some).

The Zig project for example bans AI contributions and I agree with them; the reason is not the AI isn't good. The AI can
be good, otherwise no one would use it. The problem as always, across opensource, companies and life is people. You can
be in a position to indict that but the reality is preferences are very hard to understand and predict. There isn't one
bucket of "bad people using AI" and "good people using AI" sometimes folks are just tired college graduates who need a
job and heard that making open-source contributions is a good way to do and under the sense of immediacy ended up taking
a shortcut.

This wasn't possible before agents become we didn't have a giant machine capable of giving you back thousands of lines
of code at the vaguest request but it is possible now and the reality is we as a community have to adapt.

Banning AI contributions (blanket bans) is a very hard thing to enforce so some folks came up with a vouching system
you can vouch for people you trust won't use AI or you can vouch for folks who made an initial effort to understand
the problem you are solving and will use AI because its faster that way.

I think both approaches are good, in fact anything beyond merging Claude Bot PRs directly is a sensible approach here.

## Failing upwards

There is an observation I omitted until now that I want to discuss exclusively and that is the **line**. Ever since it
was introduced in any way AI assisted programming drew a line in history, we can call it pre-AI and post-AI.


# Impossibility boundary

## Scope

This note separates mathematical impossibilities from engineering goals. None
of the results below is a stop condition. They define the strongest honest
claim MathSVG Absolute may make.

## 1. No injective lossless codec shortens every string

For a fixed length \(n\), there are \(2^n\) input bit strings. There are only

\[
\sum_{k=0}^{n-1}2^k=2^n-1
\]

bit strings shorter than \(n\). A lossless encoder must be injective over the
inputs its decoder accepts. By the pigeonhole principle, at least one
\(n\)-bit input cannot map to fewer than \(n\) bits. A self-describing archive
also pays framing overhead. Therefore MathSVG must retain an exact literal
fallback and report expansion on incompressible data.

## 2. No general codec wins every specialised codec on every input

For any fixed input \(x\), construct a valid specialised decoder whose format
contains a one-bit code for \(x\) and a literal escape for every other input.
That codec beats any general codec whose archive for \(x\) is longer than one
bit. Repeating this construction for different inputs gives mutually
incompatible specialists. A finite general codec cannot Pareto-dominate every
possible specialist on every input.

MathSVG therefore freezes a finite baseline set, exact versions, commands and
datasets. Dominance is meaningful only inside that declared experiment.

## 3. Kolmogorov complexity is not computable in general

If a total algorithm computed the shortest program length \(K(x)\) for every
string, it could decide whether any program shorter than a bound produces
\(x\), which entails deciding termination for arbitrary programs. This
contradicts the undecidability of the halting problem. MathSVG does not claim
to find the shortest possible description. It finds the smallest actual
archive inside a finite, versioned DSL and a bounded deterministic search.

## 4. One configuration cannot generally optimise every metric

Archive size, encode time, decode time, peak memory, energy and random-access
cost are distinct objectives. A configuration can spend more search to reduce
size, add an index to improve random access while increasing size, or buffer
more state to improve throughput while increasing memory. Unless one candidate
is no worse in every coordinate, the result is a Pareto set rather than a
single total optimum.

MathSVG publishes profile-specific vectors

\[
V(c)=(S,T_e,T_d,M,E,A)
\]

and never replaces them with an undocumented scalar score.

## 5. Exact optimisation is relative to a finite search space

Let \(D_v\) be a versioned finite DSL catalogue, \(C(X,p)\) the candidates
enumerated by profile \(p\), and \(B_p\) its work and memory budget. MathSVG
may exactly choose

\[
\arg\min_{g\in C(X,p),\,work(g)\le B_p}
|\operatorname{Serialize}_{D_v}(g)|.
\]

It may not call that result a global optimum over all programs, transforms or
partitions. `SAFE_PRUNE` requires an admissible lower bound. A
`HEURISTIC_SKIP` or `BUDGET_STOP` is recorded and measured against an oracle
sample.

## Strongest permitted success claim

The stretch claim is:

> On the frozen datasets, machines, baseline binaries/configurations and
> confidence policy, at least one frozen MathSVG profile Pareto-dominates every
> frozen baseline configuration.

Until every dominance certificate row passes, reports instead list the exact
baseline, file/corpus, blocking metric, confidence state and remaining gap.

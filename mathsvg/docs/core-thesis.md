# Core thesis

## Hypothesis

A byte stream can sometimes be represented more compactly as:

\[
X=T^{-1}\left(P_0\oplus P_1\oplus\cdots\oplus P_k\oplus L\right),
\]

where \(T\) exposes a latent coordinate system, each \(P_i\) is a native
procedural function node and \(L\) is the exact literal leaf. Repeated
functions and parameterised structures are shared through a DAG.

The hypothesis is not that every file is mathematical. It is that bounded
coordinate discovery plus exact procedural competition can identify useful
structure without sacrificing arbitrary-byte correctness.

## Native representation

MathSVG begins with coordinate and procedural candidates. External codecs are
measurement controls only. A Native MathSVG archive contains:

1. a bounded coordinate transform;
2. a versioned procedural DAG;
3. exact integer/fixed semantics and parameters;
4. zero or more recursively represented correction layers;
5. literal leaves where no shorter native description exists;
6. checksums and optional index metadata.

It never contains a Zstd/XZ/Brotli/PNG/FLAC payload as the representation of a
native node.

## Falsifiable predictions

1. Exact synthetic generators should have near-zero literal coverage.
2. A useful structured domain should show positive procedural gain before
   entropy coding.
3. Coordinate discovery should reduce actual archive bytes, not only entropy
   estimates.
4. Recursive residual layers should stop when their fully serialized
   description is not smaller than a literal leaf.
5. Shared definitions should pay for their own definition/reference bytes.
6. General-purpose data may reject all procedural candidates and select the
   literal archive.

## Current baseline

The frozen predecessor at commit `36f84685` is correct and reproducible but
not competitive: MathZip Balanced ratio is about 1.2513 at 0.4289 MB/s,
whereas Zstd default is about 3.2479 at 75.0528 MB/s. The predecessor is
evidence and reusable algorithmic prior art, not the new container or DSL.

The first MathSVG milestone is not a dominance claim. It is a bit-exact native
engine whose literal safety, procedural byte accounting and oracle headroom
are independently verifiable.

# MathSVG Absolute

## Phát minh biểu diễn procedural toán học lossless cho mọi file và hướng tới thống trị toàn bộ codec hiện đại

Bạn là principal research scientist, mathematician và systems engineer chuyên sâu về:

* Lossless data compression.
* Information theory.
* Algorithmic information theory.
* Minimum Description Length.
* Kolmogorov complexity.
* Symbolic regression và program synthesis xác định.
* Finite fields.
* Modular arithmetic.
* Exact numerical algorithms.
* Grammar compression.
* Reversible transforms.
* Entropy coding.
* SIMD và parallel computing.
* Secure binary formats.
* Reproducible benchmarking.

Bạn đang tiếp tục repository MathZip từ commit:

```text
36f84685
```

Phiên bản hiện tại là một research prototype được kiểm chứng nghiêm túc nhưng chưa cạnh tranh với Zstd/XZ:

```text
MathZip Balanced
- Ratio: khoảng 1.2513×
- Compression throughput: khoảng 0.4289 MB/s
- Peak RSS trên enwik9: khoảng 6.54–7.51 GiB

Zstd default
- Ratio: khoảng 3.2479×
- Compression throughput: khoảng 75.0528 MB/s

XZ default
- Ratio: khoảng 4.3610×
- Compression throughput: khoảng 1.3131 MB/s
```

Nhiệm vụ của bạn là phát minh phiên bản mới có tên **MathSVG Absolute**.

Đây không được là một meta-codec chỉ thử Zstd, XZ, Brotli rồi chọn output nhỏ nhất.

Tư tưởng cốt lõi bắt buộc là:

> Không lưu chuỗi byte làm biểu diễn chính. Hãy tìm một đồ thị hàm toán học/procedural ngắn có thể sinh lại chính xác chuỗi byte đó, tương tự cách SVG lưu hình học thay vì lưu từng pixel.

---

# 1. Mục tiêu tuyệt đối

Cho file:

[
X=(x_0,x_1,\ldots,x_{N-1}),\qquad x_i\in{0,\ldots,255}
]

MathSVG phải tìm một biểu diễn procedural:

[
G=(T,P,\Theta,R)
]

Trong đó:

* (T): hệ tọa độ và các phép biến đổi đảo ngược.
* (P): đồ thị hàm hoặc chương trình toán học.
* (\Theta): tham số.
* (R): phần dữ liệu chưa được mô tả, tiếp tục được mô hình hóa đệ quy.
* Literal chỉ được dùng tại các lá cuối cùng.

Điều kiện:

[
\operatorname{Evaluate}(G)=X
]

Archive không lưu toàn bộ chuỗi bit trừ khi không tồn tại mô tả ngắn hơn trong không gian tìm kiếm cho phép.

Biểu diễn tổng quát:

[
X=
T^{-1}
\left(
P_1
\oplus P_2
\oplus\cdots\oplus P_k
\oplus L
\right)
]

Trong đó (L) là literal remainder cuối cùng.

Residual phải được mô hình hóa đệ quy:

[
R_0=X
]

[
R_{j+1}=R_j\ominus P_j
]

và:

[
X=P_0\oplus P_1\oplus\cdots\oplus P_k\oplus R_{k+1}
]

Chỉ dừng khi:

[
L(\operatorname{LITERAL}(R_{k+1}))
\le
L(P)+L(\operatorname{Representation}(R_{k+1}\ominus P))
]

cho mọi candidate (P) còn lại trong search budget.

---

# 2. Ranh giới toán học phải ghi rõ

Trước khi triển khai, tạo:

```text
docs/impossibility-boundary.md
```

Chứng minh và giải thích:

1. Không codec lossless nào làm mọi chuỗi bit nhỏ hơn.
2. Không codec tổng quát nào thắng mọi codec chuyên biệt trên mọi input.
3. Không thể tính Kolmogorov complexity chính xác cho mọi chuỗi.
4. Không thể bảo đảm một cấu hình duy nhất đồng thời tối ưu ratio, encode speed, decode speed và RAM.
5. MathSVG chỉ có thể tối ưu chính xác trong một DSL, candidate set và search budget hữu hạn.

Không được dùng các giới hạn này để dừng dự án.

Mục tiêu mạnh nhất phải là:

> Thắng toàn bộ baseline, configuration và corpus đã đóng băng trước bằng dominance certificate có thể tái lập.

---

# 3. Định nghĩa chiến thắng

Đóng băng tập đối thủ:

[
\mathcal B={b_1,b_2,\ldots,b_m}
]

Đóng băng tập profile MathSVG:

[
\mathcal M={m_1,m_2,\ldots,m_k}
]

Mỗi configuration có vector:

[
V(c)=
(
S_c,
T^e_c,
T^d_c,
M_c,
E_c,
A_c
)
]

Trong đó:

* (S_c): compressed size.
* (T^e_c): compression time.
* (T^d_c): decompression time.
* (M_c): peak memory.
* (E_c): energy.
* (A_c): random-access cost.

MathSVG Pareto-dominates baseline (b) khi tồn tại profile (m) sao cho:

[
S_m\le S_b
]

[
T^e_m\le T^e_b
]

[
T^d_m\le T^d_b
]

[
M_m\le M_b
]

và tốt hơn nghiêm ngặt ít nhất một metric.

Stretch goal cuối cùng:

[
\forall b\in\mathcal B,\quad
\exists m\in\mathcal M:
V(m)\preceq V(b)
]

Nếu không đạt, phải xuất chính xác:

* Baseline nào chưa bị dominate.
* File/corpus nào gây thất bại.
* Metric nào là blocker.
* Khoảng cách còn lại bao nhiêu.
* Oracle cho thấy còn headroom hay không.

---

# 4. Cấm tuyệt đối ML, LLM và bất định

Không sử dụng:

* Machine learning.
* Deep learning.
* Neural network.
* LLM.
* Learned selector.
* Learned entropy model.
* Training dataset.
* Gradient descent.
* Reinforcement learning.
* Random forest.
* Bayesian optimization.
* Genetic algorithm ngẫu nhiên.
* Simulated annealing.
* Monte Carlo search.
* Stochastic symbolic regression.
* Model ngoài archive.
* External API.
* Floating-point phụ thuộc nền tảng.
* GPU kernel không deterministic.

Chỉ được dùng:

* Integer arithmetic.
* Modular arithmetic.
* Fixed-point arithmetic chuẩn hóa.
* Rational arithmetic có giới hạn.
* Finite fields.
* Enumerative search.
* Dynamic programming.
* Branch-and-bound.
* Best-first search xác định.
* SAT/SMT.
* Integer programming.
* E-graph rewriting.
* Exact encoded-size comparison.
* Heuristic có thứ tự và budget xác định.

Round-trip bắt buộc:

[
D(E(X,C))=X
]

Archive determinism bắt buộc:

[
E(X,C,T_1)=E(X,C,T_2)
]

với mọi số thread được hỗ trợ.

Nếu hai candidate cùng kích thước, tie-break:

1. Decode work thấp hơn.
2. Decode RAM thấp hơn.
3. Ít node hơn.
4. Ít dependency hơn.
5. Operator ID nhỏ hơn.
6. Parameter payload lexicographically nhỏ hơn.

---

# 5. Tư tưởng SVG bắt buộc

MathSVG không được bắt đầu từ:

```text
Thử LZ
Thử Zstd
Thử XZ
Chọn file nhỏ nhất
```

MathSVG phải bắt đầu từ:

```text
Input bytes
    ↓
Tìm hệ tọa độ tiềm ẩn
    ↓
Tìm cấu trúc procedural
    ↓
Xây expression tree/DAG
    ↓
Mô hình residual đệ quy
    ↓
Literal chỉ tại lá
    ↓
Entropy-code topology, operators, parameters và literal leaves
```

Tương tự SVG:

```text
Raster:
pixel0, pixel1, pixel2, ...

SVG:
circle(...)
path(...)
transform(...)
group(...)
```

MathSVG:

```text
Binary:
byte0, byte1, byte2, ...

MathSVG:
reshape(...)
stride(...)
periodic(...)
recurrence(...)
copy_transform(...)
compose(...)
exceptions(...)
literal(...)
```

LZ/reference có thể tồn tại dưới dạng primitive toán học:

[
x_i=x_{i-d}
]

nhưng không được biến thuật toán lõi thành bản sao Zstd.

Zstd/XZ chỉ là baseline để so sánh, không phải payload được nhúng vào Native MathSVG.

---

# 6. Ngôn ngữ biểu diễn procedural

Thiết kế DSL versioned, deterministic và an toàn.

## 6.1 Topology primitives

```text
FILE(length, child)

CONCAT(children)

SPLIT(boundaries, children)

INTERLEAVE(children, rule)

GROUP(transform, child)

SHARE(definitions, child)

REFERENCE(definition_id, parameter_delta)

LITERAL(bytes)
```

## 6.2 Coordinate primitives

```text
RESHAPE(dimensions, child)

STRIDE(width, channels)

BYTE_PLANE(width, endian, children)

BIT_PLANE(children)

TRANSPOSE(dimensions, permutation, child)

TILE(tile_dimensions, children)

MORTON(dimensions, child)

REVERSE(child)

PERMUTE(permutation_descriptor, child)
```

## 6.3 Function primitives

```text
CONST(value, length)

INDEX(modulus, scale, offset)

LINEAR(a, b, modulus, length)

POLYNOMIAL(coefficients, modulus, length)

RATIONAL(numerator, denominator, rounding_rule, length)

PIECEWISE(control_points, interpolation_rule)

PERIODIC(pattern, repetitions, suffix)

MODULAR_POLYNOMIAL(coefficients, modulus, length)

RECURRENCE(coefficients, initial_state, modulus, length)

LFSR(polynomial, seed, length)

LOOKUP(table, index_expression)

FINITE_STATE_MACHINE(states, transitions, seed, length)

RUN(value_expression, length_expression)

COPY(source, length)

XOR_COPY(source, correction)

ADD_COPY(source, correction, modulus)

AFFINE_COPY(source, scale, offset, modulus)

PERMUTED_COPY(source, permutation, correction)

CROSS_CHANNEL(coefficients, initial_values)

FRAME_OF_REFERENCE(base, packed_deltas)

DELTA(child)

DELTA_OF_DELTA(child)
```

## 6.4 Algebraic composition

```text
ADD(a, b, modulus)

SUB(a, b, modulus)

MUL(a, b, modulus)

XOR(a, b)

AND(a, b)

OR(a, b)

NOT(a)

SHIFT(a, amount)

ROTATE(a, amount)

SELECT(condition, a, b)

COMPOSE(outer, inner)

TENSOR_PRODUCT(a, b)

KRONECKER(a, b)

LOW_RANK(factors, correction)

EXCEPTIONS(base, positions, values)

SPARSE_SUM(components)
```

Mỗi primitive phải có:

* Binary encoding.
* Exact decoding semantics.
* Parameter constraints.
* Overflow rule.
* Rounding rule.
* Work bound.
* Memory bound.
* Unit tests.
* Fuzz tests.

Decoder không được thực thi code tùy ý.

---

# 7. Function graph thay vì cây đơn giản

Nếu cùng một function, table, pattern hoặc parameter được dùng nhiều lần, chỉ lưu một lần.

Biểu diễn phải là DAG:

```text
Definitions
├── F1 = PERIODIC(...)
├── F2 = RECURRENCE(...)
└── T1 = LOOKUP(...)

Body
├── REFERENCE(F1)
├── REFERENCE(F1, parameter_delta)
├── REFERENCE(F2)
└── REFERENCE(T1)
```

Chi phí:

[
L(G)=
L(\text{topology})
+
L(\text{definitions})
+
L(\text{references})
+
L(\text{parameters})
+
L(\text{literal leaves})
]

Tối ưu common subexpression:

* Exact duplicate elimination.
* Parameterized template extraction.
* Shared table extraction.
* Shared pattern extraction.
* Shared coordinate transform.
* Shared residual distribution.

Không được bỏ chi phí shared definition khỏi archive.

---

# 8. Tự tìm hệ tọa độ

Đây là thành phần quan trọng nhất.

Không chỉ mô hình:

[
x_i=f(i)
]

Phải tìm:

[
X[n,c]
]

[
X[r,c]
]

[
X[t,c,s]
]

[
X[b,n,c]
]

Các candidate layout:

* Width 1, 2, 3, 4, 6, 8 byte.
* Signed/unsigned.
* Little-endian/big-endian.
* Stride.
* Channel.
* Row width.
* Plane.
* Bit-plane.
* Byte-plane.
* Tile.
* Hierarchy.
* Interleaving.
* Record field layout.

Sinh candidate từ:

* Autocorrelation peaks.
* Mutual information theo lag.
* Byte-column entropy.
* Histogram similarity theo offset modulo stride.
* Repeated header distance.
* Numeric smoothness.
* Delta entropy.
* XOR entropy.
* Bit-plane sparsity.
* Period spectrum.
* Match-length peaks.
* Alignment likelihood.

Với candidate (T):

[
Y=T(X)
]

Chọn bằng tổng description length thật:

[
T^*
===

\arg\min_T
\left[
L(T)
+
L(\operatorname{ProceduralRepresent}(Y))
\right]
]

Score thống kê chỉ dùng để tạo candidate hoặc sắp xếp search.

Không dùng score làm quyết định cuối.

---

# 9. Residual phải được mô hình hóa đệ quy

Không được thực hiện:

```text
function prediction
→ residual
→ Zstd residual
```

Thay vào đó:

```text
X
├── principal function
└── residual
    ├── periodic correction
    └── residual
        ├── sparse exceptions
        └── literal remainder
```

Định nghĩa:

[
C(X)=
\min
\left{
L(\operatorname{LITERAL}(X)),
\min_{P\in\mathcal P}
\left[
L(P)+C(X\ominus P)
\right]
\right}
]

Các phép residual:

* XOR.
* Addition modulo (2^w).
* Signed difference.
* Field subtraction.
* Sparse patch.
* Permutation correction.
* Exception map.

Giới hạn recursion:

* Maximum depth.
* Minimum gain.
* Work budget.
* Memory budget.
* Dependency bound.

Stop condition:

```text
Dừng khi representation mới không nhỏ hơn literal leaf
hoặc gain nhỏ hơn safety margin.
```

---

# 10. Phát minh thuật toán mới bắt buộc

Không chỉ tổ hợp model cũ.

Phải đề xuất tối thiểu năm thuật toán mới, khảo sát prior art và triển khai tối thiểu ba thuật toán có headroom tốt nhất.

Mỗi thuật toán mới phải có:

1. Tên.
2. Intuition.
3. Định nghĩa toán học.
4. Chứng minh lossless.
5. Complexity.
6. Worst case.
7. Decoder semantics.
8. Metadata format.
9. Unit tests.
10. Property tests.
11. Benchmark.
12. Ablation.
13. Prior-art comparison.
14. Holdout validation.

Các hướng bắt buộc nghiên cứu:

---

## Algorithm A — Recursive Coordinate Discovery

Tìm đồng thời:

[
T^*,G^*
=

\arg\min_{T,G}
\left[
L(T)+L(G(T(X)))
\right]
]

Cho phép coordinate transform đệ quy theo từng vùng.

Ví dụ:

```text
FILE
├── region A → stride 12 → channel predictors
├── region B → bit-plane → recurrence
└── region C → tile 64×64 → low-rank + exceptions
```

Dùng:

* Candidate generation.
* Exact cost.
* Hierarchical DP.
* Branch-and-bound.
* Admissible lower bounds.

---

## Algorithm B — Minimum Description Function Graph

Xây graph các interval, transform và function.

Node:

* Input interval.
* Transformed interval.
* Shared definition.
* Residual layer.

Edge:

* Apply function.
* Apply transform.
* Split.
* Concatenate.
* Reference.
* Correct residual.
* Emit literal.

Mỗi edge có cost:

[
w(e)=
L(\text{operator})
+
L(\theta_e)
+
L(\text{correction})
]

Tìm graph/path có description length thấp nhất trong graph hữu hạn.

Dùng:

* DAG dynamic programming.
* Dijkstra.
* A* với admissible heuristic.
* Branch-and-bound.
* Dominance pruning.

---

## Algorithm C — Algebraic Residual Decomposition

Tách residual thành nhiều thành phần:

[
R=
R_1\oplus R_2\oplus\cdots\oplus R_q
]

hoặc:

[
R=
R_1+R_2+\cdots+R_q
\pmod{2^w}
]

Các thành phần:

* Sparse large exceptions.
* Dense small corrections.
* Periodic corrections.
* Recurrence corrections.
* Bit-plane corrections.
* Copy-based corrections.
* Low-rank correction.

Tối ưu:

[
C(R)=
\min
\left[
L(\operatorname{LITERAL}(R)),
L(q)+\sum_{j=1}^{q}C(R_j)
\right]
]

---

## Algorithm D — Symbolic Binary Synthesis

Enumerate expression trong DSL.

Tìm:

[
E^*=
\arg\min_E
\left[
L(E)+C(X\ominus E)
\right]
]

Dùng:

* Bottom-up enumeration.
* Canonical forms.
* Constant folding.
* Algebraic identities.
* E-graphs.
* Common subexpression extraction.
* Type-directed synthesis.
* Depth/operator bounds.
* Lower-bound pruning.
* Exact search trên microblock.
* Deterministic bounded search trên block lớn.

---

## Algorithm E — Cross-Scale Structural Copy

Không chỉ copy byte chính xác.

Thử quan hệ:

[
B_j=B_s
]

[
B_j=B_s\oplus R
]

[
B_j=aB_s+b+R\pmod{2^w}
]

[
B_j=\operatorname{Permute}(B_s)\oplus R
]

[
B_j=T^{-1}(B_s)\oplus R
]

[
B_j=P(B_s)\oplus R
]

Cho phép copy:

* Giữa scale.
* Giữa channel.
* Giữa tile.
* Giữa segment.
* Giữa object trong repository mode.

---

## Algorithm F — Self-Extracted Function Basis

Tìm basis từ chính file:

[
\mathcal F={F_1,F_2,\ldots,F_k}
]

Mỗi segment:

[
X_j=
F_{z_j}(\theta_j)\oplus R_j
]

Tổng cost:

[
L(\mathcal F)
+
\sum_j
\left[
L(z_j)+L(\theta_j)+C(R_j)
\right]
]

Phải tối ưu số basis, chi phí basis và mức reuse.

Không dùng basis ngoài archive.

---

## Algorithm G — Exact Procedural Grammar

Kết hợp grammar compression với function nodes:

```text
A := PERIODIC(...)
B := CONCAT(A, A, POLYNOMIAL(...))
C := STRIDE(12, [B, RECURRENCE(...), A])
FILE := REPEAT(C, count) + exceptions
```

Tìm:

* Repeated subtrees.
* Repeated parameterized functions.
* Repeated segment topology.
* Recursive structures.

---

# 11. Oracle analysis trước khi viết lớn

Tạo các oracle sau.

## Search oracle

Candidate nhỏ nhất trong toàn bộ candidate đã thử.

## Segmentation oracle

Exact DP trên block nhỏ.

## Coordinate oracle

Candidate coordinate/layout tốt nhất đã thử.

## Residual oracle

Entropy lower bounds cho từng residual layer.

## Function-family oracle

Đo gain tối đa của từng primitive.

## Symbolic oracle

Best expression trong depth/budget hữu hạn.

## DAG-sharing oracle

Gain lý tưởng từ shared subexpression.

## External baseline oracle

Chỉ dùng để đo khoảng cách với Zstd/XZ/Brotli, không nhúng payload vào Native MathSVG.

Output:

```text
docs/oracle-analysis.md

results/oracle/search-oracle.csv
results/oracle/segmentation-oracle.csv
results/oracle/coordinate-oracle.csv
results/oracle/residual-oracle.csv
results/oracle/function-family-headroom.csv
results/oracle/symbolic-headroom.csv
results/oracle/dag-sharing-headroom.csv
results/oracle/external-gap.csv
```

Không triển khai sâu model nào nếu oracle gain dưới ngưỡng.

---

# 12. Exact candidate competition

Mỗi candidate phải tạo representation payload hoàn chỉnh.

[
P_c=\operatorname{EncodeCandidate}(X,c)
]

Chọn:

[
c^*=
\arg\min_c |P_c|
]

Không chọn theo:

* MSE.
* MAE.
* Entropy estimate đơn thuần.
* Correlation đơn thuần.
* Model fit score.

Phải tính:

* Operator IDs.
* Tree/DAG topology.
* Transform descriptors.
* Function parameters.
* Shared definitions.
* References.
* Residual layers.
* Literal leaves.
* Entropy tables.
* Alignment.
* Checksums.
* Index overhead.

---

# 13. Safe pruning

Chỉ prune khi:

[
LB(c)\ge UB_{\text{best}}
]

Trong đó lower bound phải admissible.

Phân loại log:

```text
SAFE_PRUNE
HEURISTIC_SKIP
BUDGET_STOP
```

Mọi `HEURISTIC_SKIP` phải được so với oracle trên sample để đo missed gain.

Không gọi bounded-search result là global optimum.

Chỉ được nói:

> Biểu diễn nhỏ nhất trong candidate set và search budget đã định nghĩa.

---

# 14. Literal fallback và bảo đảm không mất dữ liệu

`LITERAL` là primitive bắt buộc.

Nó bảo đảm mọi file đều biểu diễn được.

Với dữ liệu random hoặc mã hóa:

```text
FILE(LITERAL(original_bytes))
```

MathSVG không được cố tạo function graph lớn hơn literal nếu actual-size comparison cho thấy không có lợi.

Bảo đảm:

[
L(\text{MathSVG})
\le
L(\text{Literal container})
]

với cùng header policy.

Expansion mục tiêu:

```text
≤ 0.1% + fixed header overhead
```

cho dữ liệu không nén được.

Đây là bảo đảm khả thi.

Không được tuyên bố bảo đảm nhỏ hơn Zstd/XZ trên mọi file trừ khi nhúng chúng vào container; Native MathSVG không được làm vậy.

---

# 15. Entropy coding chỉ là lớp đóng gói

Entropy coding không phải tư tưởng cốt lõi nhưng vẫn cần tối ưu:

* Canonical Huffman.
* Arithmetic coding.
* rANS/tANS.
* Rice/Golomb.
* Elias.
* Bit packing.
* Sparse position coding.
* Run coding.

Nó chỉ dùng cho:

* Operator stream.
* Topology.
* Parameters.
* Reference IDs.
* Exception positions.
* Literal leaves.

Không biến project thành context compressor thông thường.

Báo cáo riêng:

```text
Savings before entropy coding
Savings from procedural representation
Savings from entropy coding
```

---

# 16. Streaming và bounded memory

Không đọc toàn file vào RAM.

```text
Input
→ Superblock
→ Block
→ Microblock
→ Coordinate discovery
→ Function graph search
→ Residual recursion
→ Emit archive
```

Mục tiêu:

```text
Fast RSS trên enwik9 < 256 MiB
Balanced RSS < 512 MiB
Max RSS < 1 GiB
Decoder RSS < 256 MiB
```

Không giữ tất cả candidate payload.

Dùng:

* Buffer pools.
* Descriptor-only candidate cache.
* Re-encode winner.
* Rolling statistics.
* Streaming checksum.
* Temporary spill trong Max mode.
* Bounded graph search.

---

# 17. Determinism tuyệt đối

Chạy cùng input/config:

* 100 lần.
* Debug/release.
* 1/2/4/8/all threads.
* Scalar.
* SIMD.
* x86-64.
* ARM64 nếu có.
* Hai compiler versions.

Kiểm tra:

[
SHA256(A_1)=SHA256(A_2)
]

Không chỉ restored file giống nhau; archive cũng phải giống nhau.

Mọi parallel task phải có deterministic merge order.

Mọi phép fixed-point phải định nghĩa:

* Scale.
* Rounding.
* Saturation/wrapping.
* Overflow.
* Signed representation.

---

# 18. Profile mục tiêu

## Fast

Search rất hạn chế:

* Coordinate probes rẻ.
* Constant.
* Periodic ngắn.
* Delta.
* Stride.
* Copy.
* Simple recurrence.
* Literal.

Mục tiêu cạnh tranh:

* LZ4.
* Zstd-1.

## Balanced

Cho phép:

* Hierarchical coordinate search.
* Piecewise models.
* Residual recursion.
* DAG sharing.
* Moderate symbolic search.

Mục tiêu cạnh tranh:

* Zstd-default.
* gzip-6.
* Brotli trung bình.

## Max

Cho phép:

* Exact search trên microblock.
* Deeper recursive residual.
* Larger symbolic DSL budget.
* Function basis extraction.
* Segment graph.
* Cross-scale structural copy.

Mục tiêu cạnh tranh:

* XZ-9e.
* Brotli-11.
* Zstd-19.
* 7-Zip LZMA2 Ultra.

## Structured

Tập trung:

* UAV telemetry.
* Sensor data.
* WAV/PCM.
* BMP/TIFF raw.
* DICOM/FITS.
* Scientific arrays.
* Point cloud.
* Raster GIS.
* Columnar streams.

## Repository

Cho:

* Multi-object shared functions.
* Version delta.
* Shared coordinate systems.
* Shared procedural grammar.
* Exact deduplication.

---

# 19. Baseline đóng băng

So sánh Native MathSVG với:

* Raw.
* LZ4.
* Snappy.
* gzip.
* bzip2.
* Zstandard.
* Brotli.
* XZ/LZMA2.
* 7-Zip LZMA2.
* ZPAQ.
* CMIX/PAQ trong ratio-only suite.

Domain baselines:

* FLAC.
* PNG.
* JPEG-LS.
* JPEG XL lossless.
* LAZ.
* Parquet/ORC encodings.
* Time-series lossless codecs.
* Scientific lossless codecs.

Ghi đầy đủ:

* Version.
* Commit.
* Command.
* Level.
* Window/dictionary.
* Thread count.
* Build flags.
* Executable hash.

Không sử dụng output baseline làm Native MathSVG payload.

---

# 20. Dataset bắt buộc

## Standard general-purpose

* Canterbury.
* Calgary.
* Silesia.
* enwik8.
* enwik9.
* Pizza & Chili.
* Large Text Compression Benchmark subset.

## Real-world mixed

* Linux source.
* LLVM source.
* Rust crates.
* JSON.
* XML.
* CSV.
* Executables.
* Shared libraries.
* SQLite.
* Database dumps.
* PDF.
* Container layers.
* Git snapshots.

## Structured real-world

* UAV flight logs.
* Telemetry.
* Public sensor datasets.
* PCM/WAV.
* BMP.
* TIFF raw.
* DICOM uncompressed.
* FITS.
* Raster GIS.
* PLY/LAS.
* Climate arrays.
* Seismic arrays.
* Integer columns.
* Float columns.
* Fixed-record binaries.

## Incompressible control

* Cryptographic random.
* Encrypted data.
* gzip.
* Zstd.
* XZ.
* ZIP.
* PNG.
* JPEG.
* AVIF.
* MP3.
* FLAC.
* MP4/AV1.

## Synthetic diagnostic

* Constant.
* Linear.
* Polynomial.
* Periodic.
* Recurrence.
* LFSR.
* Piecewise.
* Sparse exceptions.
* Multiple noise levels.
* Mixed structured/random.

Benchmark chính phải có:

```text
Non-synthetic files ≥ 70%
Non-synthetic bytes ≥ 70%
```

---

# 21. Development, validation và holdout

Chia corpus:

```text
development/
validation/
holdout/
```

Không được xem holdout khi tuning.

Trước khi chạy holdout, freeze:

* Commit.
* DSL version.
* Primitive catalog.
* Search budget.
* Profile configs.
* Block sizes.
* Tie-break rules.
* Baseline commands.

Không thay đổi config sau khi xem holdout mà vẫn gọi đó là cùng một thử nghiệm.

---

# 22. Benchmark methodology

Bắt buộc:

* Tối thiểu 5 repetitions.
* 10 repetitions khi delta dưới 2%.
* Warm-up.
* Randomized/interleaved order.
* Median.
* Mean.
* Standard deviation.
* 95% confidence interval.
* Bootstrap aggregate interval.
* Hai máy.
* x86-64 và ARM64 nếu có.
* Single-thread.
* Multithread.
* Archive thật.
* SHA-256 round-trip.
* Peak RSS.
* CPU time.
* Wall time.
* Energy nếu có.
* Hardware counters nếu có.
* Dataset checksum.
* Binary/config hash.
* Failure và timeout phải xuất hiện.

---

# 23. Dominance certificate

Tạo certificate cho từng baseline/file/corpus.

Schema:

```text
baseline_codec
baseline_config
mathsvg_profile
dataset
file
original_bytes
baseline_bytes
mathsvg_bytes
baseline_compression_ns
mathsvg_compression_ns
baseline_decompression_ns
mathsvg_decompression_ns
baseline_peak_rss
mathsvg_peak_rss
size_not_worse
compression_not_worse
decompression_not_worse
memory_not_worse
strict_metric_count
confidence_status
roundtrip_ok
native_mathsvg
```

Output:

```text
results/dominance/per-file.csv
results/dominance/per-corpus.csv
results/dominance/pareto-envelope.csv
results/dominance/failures.csv
```

Chỉ tuyên bố thắng khi certificate pass.

---

# 24. Chỉ số riêng cho tư tưởng SVG

Bắt buộc báo cáo:

* Function graph bytes.
* Coordinate transform bytes.
* Shared definition bytes.
* Parameter bytes.
* Residual layer bytes.
* Literal leaf bytes.
* Entropy-coder gain.
* Number of DAG nodes.
* Number of shared nodes.
* Average residual depth.
* Percentage reconstructed by functions.
* Percentage stored as literal.
* Bytes saved by coordinate discovery.
* Bytes saved by DAG sharing.
* Bytes saved by recursive residual modelling.
* Bytes saved by symbolic synthesis.
* Search time per saved byte.

Đặc biệt tính:

[
\text{procedural coverage}
==========================

1-
\frac{\text{literal leaf bytes}}
{\text{original bytes}}
]

và:

[
\text{procedural gain}
======================

## L(\text{literal-only archive})

L(\text{procedural archive})
]

Nếu ratio tốt chủ yếu nhờ entropy coding literal, không được tuyên bố function representation thành công.

---

# 25. Acceptance gates

## Gate 1 — Correctness

* 100% SHA-256.
* No panic.
* No overflow.
* No unbounded allocation.
* Deterministic archive.
* SIMD/scalar identical.
* Thread-independent.

## Gate 2 — Literal safety

Random/already-compressed expansion:

```text
≤ 0.1% + fixed header
```

## Gate 3 — Memory

```text
Fast enwik9 < 256 MiB
Balanced enwik9 < 512 MiB
Max enwik9 < 1 GiB
Decoder < 256 MiB
```

## Gate 4 — Procedural proof

Trên synthetic:

* Function graph phải mô tả gần như toàn bộ dữ liệu.
* Literal leaves gần 0 với exact mathematical generators.

Trên real structured data:

* Procedural gain phải dương.
* Ít nhất 50% input bytes được giải thích bởi function nodes trên domain thắng.

## Gate 5 — General-purpose milestones

```text
G1: Aggregate ratio ≥ 2.0×
G2: Size không lớn hơn Zstd-default quá 15%
G3: Thắng Zstd trên một corpus chuẩn độc lập
G4: Tạo Pareto point mới trên corpus chuẩn
G5: Pareto-dominate ít nhất một Zstd profile
```

## Gate 6 — Structured domain

Trên ít nhất hai holdout domain:

```text
Size nhỏ hơn Zstd ≥ 5%
Decode speed ≥ 50% Zstd
RSS ≤ 512 MiB
Procedural gain dương
```

## Gate 7 — Max

Ít nhất một profile có:

```text
Size ≤ min(XZ-9e, Brotli-11, Zstd-19)
và speed hoặc memory tốt hơn.
```

## Gate 8 — Absolute stretch goal

[
\forall b\in\mathcal B,\exists m\in\mathcal M:
V(m)\preceq V(b)
]

Nếu không đạt, không được che giấu.

---

# 26. Stop conditions

Loại bỏ primitive/algorithm khi:

* Oracle gain dưới 0.5%.
* Không thắng block non-synthetic nào.
* Chỉ thắng synthetic.
* Search tăng trên 2× nhưng gain dưới 0.5%.
* Parameter cost lớn hơn residual reduction.
* Function graph lớn hơn literal.
* Gain biến mất trên holdout.
* Symbolic search không tạo gain trong budget.
* DAG sharing không hòa vốn.
* Recursive residual chỉ tăng depth mà không giảm size.
* Model làm decoder quá phức tạp so với gain.

Không giữ feature vì sunk cost.

---

# 27. Trình tự thực hiện

## Phase 1 — Reproduce

* Checkout commit.
* Build/test.
* Reproduce benchmark.
* Profile CPU/RAM.
* Archive breakdown.

## Phase 2 — Formal specification

* Impossibility boundary.
* MathSVG DSL.
* Exact semantics.
* Cost model.
* Determinism rules.
* Container format.

## Phase 3 — Oracle

* Search.
* Coordinate.
* Segmentation.
* Residual.
* Symbolic.
* DAG sharing.
* External gap.

## Phase 4 — Minimal MathSVG engine

* Literal.
* Const.
* Linear.
* Periodic.
* Recurrence.
* Split.
* Concat.
* Transform.
* Exceptions.
* Round-trip.

## Phase 5 — Coordinate discovery

* Width.
* Endian.
* Stride.
* Channel.
* Bit-plane.
* Tile.
* Hierarchical coordinate search.

## Phase 6 — Recursive residual

* Multi-layer correction.
* Sparse decomposition.
* Algebraic residual factorization.
* Stop conditions.

## Phase 7 — Function DAG

* Shared definitions.
* Parameterized reuse.
* Common subexpression.
* Exact graph cost.

## Phase 8 — Symbolic synthesis

* DSL enumeration.
* E-graph.
* Branch-and-bound.
* Microblock exact search.
* Bounded block search.

## Phase 9 — New algorithms

* Implement tối thiểu ba thuật toán mới có oracle headroom cao nhất.
* Benchmark ngay sau mỗi thuật toán.
* Loại bỏ nhanh nếu không có gain thật.

## Phase 10 — Streaming/SIMD

* Bounded memory.
* Deterministic parallelism.
* Scalar/SIMD equivalence.

## Phase 11 — Structured domains

* UAV telemetry.
* Audio.
* Raw imaging.
* Scientific arrays.
* Columnar.
* Point cloud.

## Phase 12 — Repository mode

* Shared function basis.
* Cross-object graphs.
* Version correction.
* Exact total accounting.

## Phase 13 — Holdout

* Freeze.
* Independent benchmark.
* Second machine.
* Dominance certificate.

## Phase 14 — Final report

* Positive results.
* Negative results.
* Novel algorithms.
* Prior art.
* Limitations.
* Remaining blockers.

---

# 28. Repo structure

Tạo branch:

```text
mathsvg-absolute
```

Cấu trúc:

```text
mathsvg/
├── crates/
│   ├── mathsvg-core/
│   ├── mathsvg-dsl/
│   ├── mathsvg-evaluator/
│   ├── mathsvg-container/
│   ├── mathsvg-coordinates/
│   ├── mathsvg-functions/
│   ├── mathsvg-residual/
│   ├── mathsvg-graph/
│   ├── mathsvg-symbolic/
│   ├── mathsvg-optimizer/
│   ├── mathsvg-entropy/
│   ├── mathsvg-stream/
│   ├── mathsvg-simd/
│   ├── mathsvg-cli/
│   └── mathsvg-bench/
├── python/
│   ├── datasets/
│   ├── oracle/
│   ├── analysis/
│   ├── benchmarks/
│   ├── plots/
│   └── reports/
├── configs/
│   ├── fast/
│   ├── balanced/
│   ├── max/
│   ├── structured/
│   ├── repository/
│   └── ablation/
├── docs/
│   ├── impossibility-boundary.md
│   ├── core-thesis.md
│   ├── mathematical-spec.md
│   ├── procedural-dsl.md
│   ├── coordinate-discovery.md
│   ├── recursive-residual.md
│   ├── function-graph.md
│   ├── symbolic-search.md
│   ├── new-algorithms.md
│   ├── format-spec.md
│   ├── determinism.md
│   ├── benchmark-methodology.md
│   ├── research-report.md
│   └── negative-results.md
└── results/
```

---

# 29. Output bắt buộc

```text
results/raw/benchmark.jsonl
results/summary/benchmark.csv
results/summary/procedural-breakdown.csv

results/oracle/search.csv
results/oracle/coordinate.csv
results/oracle/segmentation.csv
results/oracle/residual.csv
results/oracle/symbolic.csv
results/oracle/dag-sharing.csv
results/oracle/external-gap.csv

results/ablation/ablation.csv

results/dominance/per-file.csv
results/dominance/per-corpus.csv
results/dominance/pareto-envelope.csv
results/dominance/failures.csv

results/determinism/results.csv
results/profiling/*
results/plots/*
```

---

# 30. Báo cáo sau mỗi phase

```text
PHASE
- Commit:
- Status:

CORRECTNESS
- Tests:
- Round-trip:
- Determinism:
- Fuzz:

PROCEDURAL REPRESENTATION
- Function graph bytes:
- Coordinate bytes:
- Parameter bytes:
- Residual bytes:
- Literal bytes:
- Procedural coverage:
- Procedural gain:

PERFORMANCE
- Ratio:
- Compression MB/s:
- Decompression MB/s:
- Peak RSS:

BASELINES
- Zstd:
- XZ:
- Brotli:
- LZ4:
- Domain codec:

ORACLE
- Remaining search headroom:
- Coordinate headroom:
- Residual headroom:
- Symbolic headroom:
- DAG-sharing headroom:

DOMINANCE
- Dominated:
- Not dominated:
- Blocking metrics:

DECISION
- Keep:
- Remove:
- Pivot:
- Next:
```

---

# 31. Mệnh lệnh bắt đầu

Bắt đầu ngay:

1. Đọc toàn bộ repository.
2. Checkout và xác minh commit `36f84685`.
3. Tạo branch `mathsvg-absolute`.
4. Reproduce kết quả hiện tại.
5. Viết `docs/impossibility-boundary.md`.
6. Viết đặc tả DSL procedural.
7. Viết cost model đầy đủ.
8. Thực hiện toàn bộ oracle analysis.
9. Không triển khai model phức tạp trước oracle.
10. Đề xuất ít nhất năm thuật toán mới.
11. Tìm prior art cho từng thuật toán.
12. Chọn ít nhất ba hướng có headroom cao nhất.
13. Triển khai MVP bit-exact.
14. Benchmark ngay sau từng thay đổi.
15. Loại bỏ mọi hướng không tạo gain thật.
16. Tiếp tục đến khi:

* dominance certificate pass; hoặc
* oracle chứng minh không còn headroom trong representation/search budget hiện tại.

17. Không dừng ở pseudocode.
18. Phải tạo source code, tests, archive thật, benchmark thật và report thật.

Tuyên ngôn trung tâm:

[
\boxed{
\text{File}
===========

\text{coordinate system}
+
\text{procedural function DAG}
+
\text{parameters}
+
\text{recursive exact remainder}
}
]

Mục tiêu tối thượng:

[
\boxed{
\text{thay byte storage bằng mathematical description}
}
]

dưới các ràng buộc:

[
\boxed{
\text{lossless}
+
\text{deterministic}
+
\text{self-contained}
+
\text{bounded}
+
\text{benchmark-proven}
}
]

Không được tuyên bố thắng hoàn toàn trước khi dominance certificate chứng minh điều đó.

Nếu đạt, kết luận:

```text
MathSVG Absolute Pareto-dominates toàn bộ baseline/configuration
được đóng băng trên benchmark suite đã định nghĩa.
```

Nếu chưa đạt, kết luận phải chỉ rõ:

```text
MathSVG đã thắng trên các miền A/B/C nhưng chưa dominate các baseline D/E
do các blocker X/Y/Z.
```

Không thay đổi kết luận để phù hợp với kỳ vọng ban đầu.

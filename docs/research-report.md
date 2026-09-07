# MathZip: mô hình toán học từng đoạn với residual lossless

Trạng thái: **báo cáo Full đã hoàn tất và có evidence thực nghiệm đã xác
minh**. Full `20260726T044843Z-213f07c3` hoàn thành exact grid 725 input × 34
cấu hình = 24.650/24.650 row, 0 failure, 73.950 measured trial, một warm-up +
ba repetition, inline metrics và publication evidence đầy đủ. Run không
resume, dùng revision sạch
`e60f423b5f85d1716eb2356f2460f97ba4227885`, source-tree SHA-256
`55b6a32cc5abedcdcba8989065be83cbaa8600bede102e73c39306bb58153e63`
và binary SHA-256
`366ca45932751ecb10d52f4db3f4806b94acd44e3ec9ccbf45e33b45bd82e228`.
Mọi row đều có 3/3 repetition thành công, archive deterministic và restored
SHA-256 đúng input.

Quick `20260724T134555Z-8b4c06c2` (477/477 row), focused enwik8
`20260724T031043Z-f6e4fbfe` (9/9), corrected ablation
`20260725T170925Z-dde3921f` (252/252), Git Copy, residual, Recursive, focused
Silesia, Bit-plane, Segmentation và Max timeout regression tiếp tục là
evidence bổ sung/cô lập. Historical ablation
`20260724T032334Z-aca5bed4` vẫn bất biến: 250/252 thành công, hai Max warm-up
timeout và causal confound V13/V14/V17 được giữ nguyên; `--strict
--allow-failures` pass các evidence check còn lại với đúng hai warning. Tính
cả Full, 12 run artifact trong bộ evidence chính chứa 77.979 measured trial
thành công. Snapshot Quick lịch sử được giữ riêng thêm 1.404 trial, đưa tổng
toàn bộ 13 result document được track lên 79.383. Các kết luận dưới đây chỉ áp
dụng cho workload, revision và máy đã đo; hoàn thành Full không biến chúng
thành claim universal.

## Abstract

MathZip khảo sát một codec lossless, format-agnostic biểu diễn byte stream bằng
transform đảo ngược, partition thích nghi, prediction model toán học và
residual exact. Encoder tối ưu Minimum Description Length bằng số byte archive
serialize thật; decoder dùng integer/fixed semantics tự chứa trong container.
Full đo synthetic size/noise/encrypted matrix, Canterbury, Calgary, toàn bộ
Silesia, enwik8, enwik9, Pizza & Chili subset, mixed corpus và Git snapshots,
cùng Raw, gzip, bzip2, XZ, Zstandard, LZ4, Brotli và 7-Zip.

Trên tổng 725 workload row (có chủ ý gồm cả file riêng và stream `combined`),
MathZip Fast/Balanced/Max đạt ratio lần lượt 1,0787/1,2513/1,2572 và throughput
nén 1,7906/0,4289/0,1388 MB/s. Zstd default đạt 3,2479 và 75,0528 MB/s; XZ
default đạt 4,3610 và 1,3131 MB/s. Balanced nhỏ hơn Zstd default ở 125/725
input nhưng lớn hơn ở 600; 121 win thuộc synthetic. Bốn win ngoài corpus
synthetic là Calgary `geo`, generated BMP/WAV và Silesia `x-ray`; cả bốn dùng
stride transform và chậm hơn Zstd từ 41,47 đến 1.666,43 lần. Chỉ generated BMP
đặt MathZip trên Pareto frontier size/thời gian nén của toàn bộ 34 cấu hình
ngoài synthetic.

enwik9 1 GB cho kết quả Balanced 847.388.476 B (ratio 1,1801; 1,0210 MB/s;
peak RSS 6.860,98 MiB), so với Zstd default 312.548.986 B (3,1995; 92,6114
MB/s; 42,22 MiB) và XZ default 230.153.052 B (4,3449; 1,2166 MB/s; 95,03
MiB). Pizza & Chili tạo 24.440.603 B với Balanced, so với 2.225.772 B của
Zstd và 609.144 B của XZ. Max chỉ giảm 0,4627% byte so Balanced trên toàn Full
nhưng dùng 3,090× thời gian; 87/725 archive Max còn lớn hơn Balanced.

Corrected/focused ablation vẫn cho causal evidence: adaptive nhỏ hơn fixed
9,670%; Zstd residual giảm 68,9505% so custom; Copy chỉ giảm 0,0440% và
Recursive 0,0902% trên focused pairs. Full cho thấy residual coding chiếm
66,39--82,16% compression wall và coded residual chiếm 99,69--99,91% archive
ở Balanced/Max. Kết luận thực nghiệm là MathZip nhận ra một niche structured,
đặc biệt record/stride-like và một số synthetic polynomial/linear, nhưng
không cạnh tranh tổng dụng với Zstd/XZ; search cost và peak memory hiện quá
lớn.

## 1. Introduction

Codec byte-stream phổ dụng khai thác các dạng redundancy khác nhau: repeated
substrings, symbol contexts, runs, block permutations hoặc entropy lệch. Một
byte stream cũng có thể chứa đoạn được sinh bởi quy luật ngắn như cấp số modulo,
đa thức, chu kỳ hoặc recurrence, xen kẽ đoạn gần ngẫu nhiên. MathZip hỏi liệu
một tập model toán học nhỏ và decoder đơn giản có mô tả một số đoạn như vậy
ngắn hơn dictionary/context codec hay không.

Ý tưởng “function + residual” là:

\[
\hat y_i=f_j(i;\theta_j),\qquad
r_i=(y_i-\hat y_i)\bmod256
\]

hoặc \(r_i=y_i\oplus\hat y_i\). Model không cần dự đoán hoàn hảo: residual giữ
mọi thông tin để khôi phục bit-exact. Vấn đề khó không phải fit error thấp mà là
chọn transform, boundary, model, parameters và coder sao cho tổng metadata +
residual thật sự nhỏ hơn raw.

Ba mục tiêu nghiên cứu:

1. xác định workload nơi model toán học tạo residual đủ rẻ để bù overhead;
2. đo đóng góp riêng của segmentation, model family và entropy coding;
3. xác định Pareto frontier size/search cost/decode cost, kể cả kết quả âm.

MathZip không giả định universal superiority. “Format-agnostic” nghĩa là codec
không parse PNG/ELF/XML semantics; nó không bảo đảm byte order tình cờ biểu lộ
smoothness có ý nghĩa.

## 2. Related work

### 2.1 Nền tảng information theory và description length

Shannon đặt nền tảng source coding và entropy trong *A Mathematical Theory of
Communication* (Claude E. Shannon, 1948,
[DOI](https://doi.org/10.1002/j.1538-7305.1948.tb01338.x)). Entropy là lower
bound thống kê dưới source model, nhưng không tự chỉ ra model phù hợp cho một
file cá thể.

Kolmogorov đưa ra cách nhìn algorithmic description của một object trong
*Three Approaches to the Quantitative Definition of Information* (A. N.
Kolmogorov, 1965,
[bản gốc/metadata](https://www.mathnet.ru/eng/ppi68)). Solomonoff liên hệ prior
với độ dài program trong *A Formal Theory of Inductive Inference, Part I*
(Ray J. Solomonoff, 1964,
[DOI](https://doi.org/10.1016/S0019-9958(64)90223-2)). Hai hướng này giải thích
trực giác “mô tả ngắn”, nhưng shortest program tổng quát không phải một
optimization khả thi cho codec.

Minimum Message Length xem model/classification như một encoding trong *An
Information Measure for Classification* (C. S. Wallace và D. M. Boulton, 1968,
[DOI](https://doi.org/10.1093/comjnl/11.2.185)). Rissanen trình bày nguyên lý
chọn model bằng shortest data description trong *Modeling by Shortest Data
Description* (Jorma Rissanen, 1978,
[DOI](https://doi.org/10.1016/0005-1098(78)90005-5)). MathZip dùng một phiên
bản operational: cost là byte format thật, không phải chỉ negative
log-likelihood.

### 2.2 Dictionary, context và block transform

LZ77 mã hóa repeated substring bằng backward reference trong *A Universal
Algorithm for Sequential Data Compression* (Jacob Ziv và Abraham Lempel, 1977,
[DOI](https://doi.org/10.1109/TIT.1977.1055714)); LZ78 xây dictionary phrase
trong *Compression of Individual Sequences via Variable-Rate Coding* (Jacob
Ziv và Abraham Lempel, 1978,
[DOI](https://doi.org/10.1109/TIT.1978.1055934)). LZW là biến thể thực dụng
trong *A Technique for High-Performance Data Compression* (Terry A. Welch,
1984, [DOI](https://doi.org/10.1109/MC.1984.1659158)). Copy model của MathZip
là baseline/hybrid từ cùng nguyên lý, không phải đóng góp toán học mới; ablation
phải tắt nó.

Prediction by Partial Matching dùng context dài thích nghi trong *Data
Compression Using Adaptive Coding and Partial String Matching* (John G. Cleary
và Ian H. Witten, 1984,
[DOI](https://doi.org/10.1109/TCOM.1984.1096090)). Context model học conditional
symbol probabilities; MathZip thay vào một catalog function theo vị trí/giá
trị trước đó. Hai cách có thể tạo residual distribution giống nhau trên một số
input nhưng model cost và inductive bias khác nhau.

Burrows--Wheeler Transform sắp xếp block đảo ngược để gom context tương tự
trong *A Block-sorting Lossless Data Compression Algorithm* (Michael Burrows
và David J. Wheeler, 1994, DEC SRC Research Report 124,
[PDF](https://bitsavers.org/pdf/dec/tech_reports/SRC-RR-124.pdf)). BWT là
transform ứng viên tương lai, không thuộc core v1 tối thiểu.

Grammar compression thay repeated phrase bằng nonterminal có cấu trúc phân cấp.
SEQUITUR được trình bày trong *Identifying Hierarchical Structure in Sequences:
A Linear-Time Algorithm* (Craig G. Nevill-Manning và Ian H. Witten, 1997,
[arXiv](https://arxiv.org/abs/cs/9709102),
[DOI](https://doi.org/10.1613/jair.374)). MathZip model catalog không tìm grammar
tổng quát; Periodic/Copy chỉ bao phủ một phần repetition.

### 2.3 Entropy coding

Arithmetic coding tách probability model khỏi channel coder; một mô tả và
implementation kinh điển là *Arithmetic Coding for Data Compression* (Ian H.
Witten, Radford M. Neal và John G. Cleary, 1987,
[DOI](https://doi.org/10.1145/214762.214771)). Asymmetric Numeral Systems cung
cấp một họ entropy coder trạng thái nguyên trong *Asymmetric Numeral Systems*
(Jarek Duda, 2009, [arXiv](https://arxiv.org/abs/0902.0271)). MathZip MVP có
coder Raw/RLE/ZeroRun/Sparse/BitPack và một Zstandard level-3 hybrid tắt mặc
định để tách đóng góp entropy coder; Huffman, arithmetic coding và rANS là
extension cần ablation riêng.

### 2.4 Piecewise approximation và bit-plane

Piecewise linear model gần với polygonal approximation: *An Iterative Procedure
for the Polygonal Approximation of Plane Curves* (Urs Ramer, 1972,
[DOI](https://doi.org/10.1016/S0146-664X(72)80017-0)) giảm số control points
dưới một error criterion. MathZip khác ở chỗ mọi error vẫn được residual encode
và model được chọn theo tổng bit, không theo tolerance lossy.

Bit/byte shuffling gom bit có statistical role tương tự khi dữ liệu có record
structure. *A Compression Scheme for Radio Data in High Performance Computing*
(Kiyoshi Masui và cộng sự, 2015,
[DOI](https://doi.org/10.1016/j.ascom.2015.07.002),
[arXiv](https://arxiv.org/abs/1503.00638)) mô tả Bitshuffle trong một scientific
data pipeline. MathZip thử bit-plane không cần biết schema, nên lợi ích,
plane-boundary byte mixing và metadata phải được đo thay vì suy từ workload
typed.

### 2.5 Learned model và implicit representation

*The Case for Learned Index Structures* (Tim Kraska, Alex Beutel, Ed H. Chi,
Jeffrey Dean và Neoklis Polyzotis, 2018,
[DOI](https://doi.org/10.1145/3183713.3196909),
[arXiv](https://arxiv.org/abs/1712.01208)) xem index như model dự đoán vị trí.
Đây không phải file compression, nhưng cùng gợi ý thay table/data structure bằng
model + correction.

SIREN biểu diễn signal bằng network tuần hoàn trong *Implicit Neural
Representations with Periodic Activation Functions* (Vincent Sitzmann, Julien
N. P. Martel, Alexander W. Bergman, David B. Lindell và Gordon Wetzstein, 2020,
[NeurIPS](https://proceedings.neurips.cc/paper_files/paper/2020/hash/53c04118df112c13a8c34b38343b9c10-Abstract.html),
[arXiv](https://arxiv.org/abs/2006.09661)). COIN lưu quantized network weights
như code cho ảnh trong *COIN: COmpression with Implicit Neural representations*
(Emilien Dupont, Adam Goliński, Milad Alizadeh, Yee Whye Teh và Arnaud Doucet,
2021, [arXiv](https://arxiv.org/abs/2103.03123)). Những công trình này chủ yếu
đánh giá signal/image representation, thường có lossy rate-distortion;
MathZip v1 không dùng neural model vì cần exact arbitrary-byte recovery,
determinism và decoder tự chứa.

### 2.6 Deduplication và delta compression

Content-addressed block storage coalesces duplicate content trong *Venti: A New
Approach to Archival Data Storage* (Sean Quinlan và Sean Dorward, 2002,
[USENIX FAST](https://www.usenix.org/conference/fast-02/venti-new-approach-archival-data-storage)).
VCDIFF định nghĩa portable delta instruction format trong *The VCDIFF Generic
Differencing and Compression Data Format* (David G. Korn, James J. MacDonald,
Jeffrey C. Mogul và Kiem-Phong Vo, 2002,
[RFC 3284](https://www.rfc-editor.org/rfc/rfc3284)). MathZip v1 chỉ có
intra-archive backward Copy; cross-object dedup/shared dictionary là future
work và phải tính mọi external state nếu được so như standalone compression.

### 2.7 Program synthesis như compression

Tìm program ngắn sinh đúng bytes là giới hạn khái niệm của “function as data”,
gần algorithmic information và Solomonoff induction hơn một codec có
termination bound. MathZip chủ ý thay program synthesis không giới hạn bằng
catalog model hữu hạn, parameter grammar canonical và residual fallback. Điều
này mất khả năng biểu diễn arbitrary short program nhưng cho decoder audit được,
không thực thi code và có cost chính xác.

## 3. Method

Cho \(Y=T(X)\) và partition \(Y_1\Vert\cdots\Vert Y_M\), segment \(j\) chọn
model \(f_j\), parameters \(\theta_j\), residual operation và coder. Cost:

\[
C = L(H)+L(T)+
\sum_{j=1}^{M}
\big[L(S_j)+L(\theta_j)+L(R_j)\big]+L(F).
\]

`L` là số bit serialize thật của format v1. Search có thể dùng entropy hoặc
fit error để đề xuất/prune, nhưng winner được serialize trước quyết định.

Transform v1 registry gồm Identity, Delta modulo 256, previous-byte XOR,
Bit-plane và stride byte transpose. Model catalog mục tiêu gồm Raw, Constant,
Affine, Polynomial modulo 256, Periodic, Recurrence, Piecewise Linear, Run và
Sparse, Copy. Status support thực tế nằm trong
[implementation-status.md](implementation-status.md).

Residual AddModulo/XOR bảo đảm:

\[
y_i=(\hat y_i+r_i)\bmod256
\quad\text{hoặc}\quad
y_i=\hat y_i\oplus r_i.
\]

V1 đăng ký năm custom coder Raw/RLE/ZeroRun/Sparse/BitPack và một Zstandard
frame hybrid. Zstd residual dùng level 3 ở encoder tham chiếu, tắt trong
Fast/Balanced/Max và chỉ bật bằng config explicit để không trộn lợi ích model
toán với entropy coder ngoài.

Mọi arithmetic decode được đặc tả bằng integer semantics. Harmonic model bị
hoãn tới khi Q-format/LUT/rounding có test vectors đa kiến trúc.

## 4. Adaptive partition và optimization

Fixed-size segmentation tạo baseline. Encoder hiện đề xuất boundary từ đủ tám
nhóm tín hiệu yêu cầu: entropy byte, histogram divergence, mean/variance,
lag-one autocorrelation, periodicity, compression-ratio probe, bit density và
prediction residual. Các feature dùng số nguyên/Q8 trên hai cửa sổ bằng nhau
và threshold cố định; Adaptive hợp change points với fixed anchors. Unit test
có cả ca hai cửa sổ cùng histogram, mean, variance, entropy và bit density
nhưng khác thứ tự byte. Trên tập boundary hữu hạn, shortest path dynamic
programming chọn:

\[
D[j]=\min_{i<j}\{D[i]+E(i,j)\},
\]

với \(E(i,j)\) là candidate segment serialize nhỏ nhất. ChangePoint/Adaptive áp
minimum/maximum segment length và bounded predecessor lookback; Fixed nối từng
cặp anchor nên chấp nhận cả block nhỏ hơn minimum và block cuối ngắn.

Chế độ Recursive opt-in stream-evaluate exact edge cost rồi tối ưu thêm số leaf
\(\ell\le2^d\), với \(d=\texttt{max\_tree\_depth}\); chỉ path thắng được fit lại
để materialize payload. Nó áp min/max nghiêm ngặt cho searched leaf, prune theo
feasibility và Pareto cost/leaf-count, cap persistent frontier ở 2.000.000
state, cap toàn candidate set (gồm endpoints/backbone) ở 262.144 boundary, và
giữ leaf list phẳng trong container v1. Whole-file Raw fallback toàn cục vẫn
miễn segmentation bounds.
Vì mọi flat partition tối đa \(2^d\) leaf đều có một binary tree depth tối đa
\(d\), không cần serialize topology hay split token. Beam search, encoded-cost
lower bound và early stopping vẫn là roadmap. Chế độ này có source/test nhưng
có đúng một focused comparison depth 4; không được khái quát kết quả đó sang
Full hoặc depth khác. Tie-break ổn định giữ deterministic archive.

Copy fitting dùng index bốn-byte trên candidate boundary, giới hạn source lùi
1 MiB và tối đa 16 candidate hợp lệ thay vì quét mọi byte. Đây là tối ưu search
encoder; archive vẫn chỉ lưu backward distance không chồng lấn.

Objective mở rộng:

\[
J=C+\lambda T_\mathrm{decode}+\mu M_\mathrm{decode}.
\]

Kết quả size vẫn báo riêng \(C\); \(\lambda,\mu\) không được dùng để che một
archive lớn hơn.

## 5. File format

V1 có fixed 80-byte `MZIP` header, length-delimited transform/segment payload,
và footer `MZFT` + SHA-256 của header/payload. Header lưu original/transformed/
payload size, counts, SHA-256 original và CRC-32. Segment prefix 36 byte lưu
offset/length/model/residual IDs, parameter/residual lengths và decoded CRC-32.

Parser dùng decode limits, checked arithmetic, canonical uLEB128, ID allowlist,
contiguous segment invariant và exact payload consumption. SHA/CRC phát hiện
corruption chứ không xác thực nguồn. Đặc tả normative:
[format-spec.md](format-spec.md).

Default decoder chặn archive 256 MiB, output 128 MiB, 65.536 segment, aggregate
inverse-transform work và aggregate model work ở tám lần output limit. CLI
compress mặc định chặn input 512 MiB. Benchmark corpus lớn chỉ tăng các limit
bằng argv explicit được giữ trong raw result.

## 6. Experimental setup

Protocol đầy đủ: [benchmark-methodology.md](benchmark-methodology.md).

| Field | Giá trị |
|---|---|
| Source revision | Full: `e60f423b5f85d1716eb2356f2460f97ba4227885`; Quick: `6dbc3860910e8d310cfa8bbeadbf97f0529beff1`; Git Copy: `163fc78cbadc0fdb5888781080baade7b576811d`; residual: `a130e30389cad1efe6db3ffad7764022a446531e`; Recursive: `aac547e57647b0135c555c0b59a5b0c01db476ea`; Silesia: `0e3bd26e9883b7b6d6ed51def05b1822efbda6bf`; Bit-plane: `1703b73f3ef51753ed1f92ea5e2ce28da9bcdf22`; Segmentation: `3c864d915b0d2452da712cd691796a4bdf987876`; Max regression: `e6ce84274a45b5a6e4738844921f9b374439c643`; corrected ablation: `e5366fa7c5c1cd5801bb9cc86b19a09592403137`; enwik8/historical ablation: `235d190a20e64b6ddc5ce002379809ebbe8fe0cd`; đều sạch và ổn định trong từng run |
| Source tree SHA-256 | Full: `55b6a32cc5abedcdcba8989065be83cbaa8600bede102e73c39306bb58153e63`; Quick: `91797df21983656b4d8020958e3619a646f46dfe86a4560d854e22483838c196`; Git Copy: `6397e3d01b12d567f54c378f036e785ee107916f8d9ab873b13f41a1031e0fd2`; residual: `6e0ac49416c7a7d9db4d492df6fa3a125c8ea1556fd7f053580499a9cc85e7dd`; Recursive: `0164eab3f424d99e9976ecfe9b1f5ceeff2c8fea88b62624c7b4adb3ad8bf33f`; Silesia: `dd883471e0a661461fd56cf892f01dfbc90a8ec2e9d192d87f07b8e0b5e4cfad`; Bit-plane: `612a5cf7d57a2607bfc8c297bf9bf08214ac25e065ea4d9a08ee881ddac733ac`; Segmentation: `9c9501edf2a575addc6f45ae5b5fce31352031a79d412d6993674c994d83991d`; Max regression: `e0f67fc713a0684287f72014b3c539c2d369324137d1fc77367b08bb27b5516e`; corrected ablation: `e3813b1523ee29c09f8b60b9d7a06844219e21c20114e3a3ce5762ae75b5323a`; legacy: `0d566a5ec2985f9e6bdc3ece3cdeb897525b487a61c5b409304e3db7306ce05d` |
| MathZip binary SHA-256 | Full: `366ca45932751ecb10d52f4db3f4806b94acd44e3ec9ccbf45e33b45bd82e228`; Quick: `6568f05094d05eac86a12ee608bc10c34c268080a25636bbaa8b9b2f59f63549`; Git Copy: `c4cc0e9b8a8469f70e5c9d594b127f5bf2eb6ad6a402388bb4595772197c2437`; residual: `a2818ffa822338c2ffa00159b15739a0618417aa8a74a417e3656758181d75d6`; Recursive: `8a40009eafaac4ae63a396de1751d4a4f8519130b66aba49f1a7fb1e427c5559`; Silesia: `485c9cdd7533e34d1a18592bd6652d4ba590b62e6188e0a1a63558614152d154`; Bit-plane: `237c4515d958afcc373078badbec902e7736a3582891b734fd4ba555a326094f`; Segmentation: `1b5e433a5321b00ecd7969bf1259db52637b8fee8abddb6e379123fe86bd37f2`; Max regression: `60b440370688da1926dabd9a0b1c5fc304969dce095c2abc2e86d03845d828f2`; corrected ablation: `728494e5c0d1ba0aad42a039b056d26372cd60b9a744c0a4ad1744552084cd30`; legacy: `b1fb6882cc7be18c01cdf11c9c66507d8090b5aa0e161ec11ca518b50f254f78` |
| Hardware | Intel Xeon E5-2680 v3 2,50 GHz, 16 logical/physical core, RAM 16.715.317.248 B |
| OS/kernel | Linux x86_64, glibc 2.39, kernel `7.0.0-28-generic`, ext4 |
| Toolchain | rustc/cargo 1.97.1; Python 3.12.3 |
| Baseline | gzip 1.12, bzip2 1.0.8, XZ 5.4.5, Zstd 1.5.5, LZ4 1.9.4, Brotli 1.1.0, 7-Zip 23.01 |
| Corpus integrity | Manifest và SHA-256 per-input được lưu và verify trong từng raw row |
| Timing | 1 warm-up + 3 repetition, median successful repetitions; Full có mọi codec ở 1 thread và thêm XZ/Zstd/7-Zip ở 4 thread; MathZip chỉ có 1 thread |
| RSS | `/proc` process-tree sampling mỗi 10 ms khi khả dụng |
| Thiếu environment field | CPU governor và container image digest không khả dụng; run ghi rõ lý do |

Mỗi repetition ghi output file thật, archive SHA-256, restored SHA-256, thời
gian, CPU và RSS. Mười run cũ trong bảng dưới dùng protocol legacy `probe`,
trong đó metrics probe nằm ngoài lượt timing và archive probe phải cùng
SHA-256 với archive đo. Corrected ablation và Full dùng `inline`: timed
compression tự sinh sidecar, không chạy compression lần hai. Hai protocol
được ghi và strict-verify riêng, không trộn speed claim.
Trạng thái các profile:

| Profile/run | Input × codec/variant | Row thành công | Timeout/op | Verification |
|---|---:|---:|---:|---|
| Full `20260726T044843Z-213f07c3` | 725 × 34 | 24.650/24.650 | 5.400 s | inline, strict exact-grid + 15-plot pass, 0 warning |
| Quick `20260724T134555Z-8b4c06c2` | 53 × 9 | 477/477 | 120 s | strict exact-grid pass, 0 warning |
| enwik8 `20260724T031043Z-f6e4fbfe` | 1 × 9 | 9/9 | 3.600 s | strict pass, 0 warning |
| Corrected ablation `20260725T170925Z-dde3921f` | 12 × 21 | 252/252 | 600 s | inline, strict exact-grid + 15-plot pass, 0 warning |
| Historical ablation `20260724T032334Z-aca5bed4` | 12 × 21 | 250/252 | 600 s | immutable legacy; strict evidence pass với 2 recorded-failure warning |
| Git Copy `20260724T140810Z-c825d7e1` | 15 × 3 | 45/45 | 3.600 s | strict exact-grid pass, 0 warning |
| Residual `20260724T143401Z-3bb520a6` | 12 × 3 | 36/36 | 600 s | strict exact-grid pass, 0 warning |
| Recursive `20260724T161541Z-70dfeff6` | 10 × 3 | 30/30 | 600 s | strict exact-grid pass, 0 warning |
| Silesia `20260724T165449Z-4ef6e508` | 12 × 9 | 108/108 | 3.600 s | strict exact-grid pass, 0 warning |
| Bit-plane `20260724T174812Z-583dbb61` | 14 × 3 | 42/42 | 600 s | strict exact-grid pass, 0 warning |
| Segmentation `20260725T051723Z-a5f0fdfc` | 10 × 9 | 90/90 | 600 s | strict exact-grid pass, 0 warning |
| Max regression `20260725T055807Z-2e6ce6d4` | 2 × 2 | 4/4 | 600 s | strict exact-grid pass, 0 warning |

Full phủ 644 synthetic input, Canterbury 11 file + một stream gộp, Calgary 14
file + một stream gộp, toàn bộ 12 file Silesia, enwik8, enwik9, một Pizza &
Chili repetitive subset, 24 mixed-generated file, 11 Git snapshot file + bốn
stream gộp. Ba MathZip mode và Raw được đo cùng toàn bộ baseline bắt buộc ở
single thread; XZ, Zstd và 7-Zip còn có 4-thread variants. Exact grid hash là
`d0e49183f58d18b88a7eb2877b72e9f3cb757961f322dae3befdd04779d59a22`,
config hash là
`213f07c3f01c6e13b1721131073e6d0f4f5e592e15ae0cbe24c5705aba0940bf`.
Run mất 129.095,967 s elapsed, không resume, và giữ source/config/input grid,
host identity cùng executable hash ổn định trước/sau.

Quick và các focused run vẫn được giữ vì chúng cô lập factor hoặc revision
lịch sử. Corrected ablation gồm tám input synthetic 64 KiB và bốn Canterbury.
Residual focused dùng lại đúng 12 input đó; Recursive dùng tám synthetic cùng
`alice29.txt`/`sum`; Git Copy dùng 11 file cùng bốn concatenation từ ba
snapshot tự sinh. Bit-plane dùng chín input 64 KiB và năm input 31 B;
Segmentation dùng mười input; Max regression replay đúng
`kennedy.xls`/`ptt5`.

Quick hiện tại đo một ciphertext 4 KiB từ encrypted generator v2 và giữ exact
grid/checkpoint identity. Snapshot Quick 52 input
`20260724T025637Z-8563453d` trước encrypted v2 vẫn được giữ bất biến như
legacy-v1 evidence. Trên 52 input chung, toàn bộ MathZip archive size và
archive SHA-256 giống hệt giữa hai revision; thay đổi residual-size scan không
đổi format/output.

## 7. Results

Tất cả ratio aggregate dưới đây là
\(\sum\text{original}/\sum\text{compressed}\), không phải mean per-file.
Bảng Quick dùng các file riêng của Canterbury/Calgary, không cộng lại row
`combined` vốn chứa cùng bytes lần thứ hai.

### 7.1 Full benchmark

Full hoàn thành 24.650/24.650 row, tất cả `status=ok`, 3/3 measured repetition,
round-trip đúng SHA-256 và deterministic archive. Có 73.950 trial record thành
công, 0 error/unavailable row; `scientifically_compliant_run`,
`timing_protocol_compliant` và `publication_evidence_complete` đều `true`.
Tổng sau đây dùng đúng 725 workload row cho mỗi single-thread codec. Nó hữu
ích để mô tả grid nhưng không phải prior tự nhiên: 644/725 input là synthetic,
và các corpus Canterbury/Calgary/Git có cả file riêng lẫn stream gộp nên bytes
được đếm như workload riêng.

| Codec | Archive B | Ratio | Bit/B | Comp MB/s | Decomp MB/s | Peak RSS MiB |
|---|---:|---:|---:|---:|---:|---:|
| MathZip Fast | 1.360.602.258 | 1,0787 | 7,4162 | 1,7906 | 40,2409 | 6.700,871 |
| Zstd Fast | 514.994.261 | 2,8499 | 2,8071 | 112,7961 | 218,6248 | 15,320 |
| XZ Fast | 414.819.064 | 3,5382 | 2,2610 | 8,8390 | 39,7308 | 10,355 |
| MathZip Balanced | 1.172.912.871 | 1,2513 | 6,3932 | 0,4289 | 26,1787 | 6.860,984 |
| Zstd default | 451.897.733 | 3,2479 | 2,4632 | 75,0528 | 198,1147 | 42,230 |
| XZ default | 336.549.840 | 4,3610 | 1,8344 | 1,3131 | 47,3062 | 95,031 |
| MathZip Max | 1.167.485.737 | 1,2572 | 6,3636 | 0,1388 | 28,6123 | 7.693,367 |
| Zstd Max | 347.562.270 | 4,2229 | 1,8945 | 1,1114 | 186,7161 | 227,547 |
| XZ Max | 315.698.036 | 4,6491 | 1,7208 | 0,8002 | 46,6063 | 675,027 |

So sánh ghép đúng cùng input, thread và level cho kết quả sau. “Gain byte”
dương nghĩa là MathZip nhỏ hơn; time ratio lớn hơn một nghĩa là MathZip chậm
hơn.

| MathZip | Baseline | Size W/T/L | Weighted gain byte | Sum comp-time ratio |
|---|---|---:|---:|---:|
| Fast | Zstd Fast | 57/0/668 | -164,198% | 62,993× |
| Fast | XZ Fast | 59/0/666 | -227,999% | 4,936× |
| Balanced | Zstd default | 125/0/600 | -159,553% | 174,988× |
| Balanced | XZ default | 105/0/620 | -248,511% | 3,062× |
| Max | Zstd Max | 112/0/613 | -235,907% | 8,008× |
| Max | XZ Max | 101/0/624 | -269,811% | 5,765× |

Trong 125 Balanced win trước Zstd default, 121 thuộc corpus `synthetic`. Chỉ
bốn input ngoài corpus đó nhỏ hơn Zstd; cả bốn đều chọn stride transpose:

| Input | Original B | MathZip B | Zstd B | Size gain | Comp-time MathZip/Zstd |
|---|---:|---:|---:|---:|---:|
| Calgary `geo` | 102.400 | 67.242 | 69.223 | 2,862% | 1.666,43× |
| generated `sample.bmp` | 36.918 | 878 | 34.740 | 97,473% | 419,20× |
| generated `sample.wav` | 88.244 | 9.848 | 88.260 | 88,842% | 1.584,67× |
| Silesia `x-ray` | 8.474.240 | 5.141.178 | 6.086.279 | 15,528% | 41,47× |

Balanced chỉ thắng XZ default trên BMP và WAV. Với Max/Zstd Max, ba win ngoài
synthetic là BMP, WAV và `x-ray`; `x-ray` chỉ nhỏ hơn 0,289% và Max vẫn chậm
hơn 5,51×. Trên Pareto frontier size/thời gian nén của đủ 34 cấu hình, MathZip
Balanced/Fast/Max xuất hiện ở 62/37/35 input; ngoài synthetic chỉ
`sample.bmp` có MathZip Balanced trên frontier. Không MathZip mode nào nằm trên
frontier khi cộng toàn bộ 725 workload row.

Kết quả theo corpus xác nhận endpoint gain không phân bố rộng:

| Corpus/slice | Original B | MathZip Balanced | Zstd default | XZ default |
|---|---:|---:|---:|---:|
| Canterbury files (11) | 2.810.784 | 1,5261 | 4,4273 | 5,7098 |
| Calgary files (14) | 3.141.622 | 1,3627 | 3,0890 | 3,7142 |
| Silesia (12) | 211.938.580 | 1,2727 | 3,1848 | 4,3048 |
| enwik8 | 100.000.000 | 1,1866 | 2,8179 | 3,7914 |
| enwik9 | 1.000.000.000 | 1,1801 | 3,1995 | 4,3449 |
| Pizza & Chili subset | 46.968.181 | 1,9217 | 21,1020 | 77,1052 |
| Mixed generated (24) | 839.363 | 1,3798 | 2,0700 | 2,2339 |
| Git combined (4) | 302.080 | 4,7540 | 287,6952 | 224,0950 |
| Synthetic (644) | 95.601.506 | 2,3398 | 3,0477 | 3,5206 |

Các ô codec trong bảng là weighted ratio, không phải archive byte. Silesia
Balanced thắng Zstd ở 1/12 file (`x-ray`) nhưng thua aggregate 150,235% tính
theo archive Zstd; nó thua XZ trên 12/12. enwik9 Balanced dùng 847.388.476 B,
Zstd default 312.548.986 B và XZ default 230.153.052 B. Pizza & Chili tương
ứng dùng 24.440.603/2.225.772/609.144 B. Git cho thấy repetition đơn giản
không đủ: MathZip Max cải thiện ratio combined lên 55,275 nhưng vẫn lớn hơn
Zstd/XZ nhiều lần.

Size và entropy không tự dự đoán winner. Balanced không thắng Zstd default ở
bất kỳ input 0--255 B nào; W/T/L theo bucket là 0/0/84 (empty), 0/0/198
(1--255 B), 49/0/138 (256 B--4 KiB), 41/0/82 (>4--64 KiB), 34/0/81
(>64 KiB--1 MiB), 1/0/16 (>1--100 MB), 0/0/1 (>100 MB). Chỉ entropy bin
[0,2) bit/B có weighted gain dương, 2,098%, nhưng ngay tại đó Balanced chỉ
thắng 18/225 file; bin [7,9; 8,0] có 28/117 win nhưng weighted gain là
-43,256%.
Cấu trúc/transform phù hợp quan trọng hơn entropy marginal.

### 7.2 Profile frontier, storage và phase metrics

Balanced giảm 13,7946% byte so Fast nhưng dùng 4,175× compression time;
W/T/L là 295/428/2. Max chỉ giảm thêm 0,4627% byte so Balanced, dùng 3,090×
time và có W/T/L 135/503/87. Trong 48/102 screened large-input row, Max chọn
lại exact Balanced frontier và tạo cùng archive hash; search rộng hơn vì vậy
không bảo đảm output nhỏ hơn trên mọi input. Xét riêng ba MathZip mode,
Fast/Balanced/Max
nằm trên per-input frontier ở 595/353/198 input, nhưng frontier này không xét
baseline mạnh hơn.

Mọi MathZip row Full thỏa invariant exact:

```text
container overhead + segment descriptors + model parameters
+ actual coded residual = compressed bytes
```

| Mode | Metadata B | Weighted % archive | Median % archive, all | Median % archive, non-synthetic | Coded residual % archive |
|---|---:|---:|---:|---:|---:|
| Fast | 3.248.883 | 0,2388% | 41,460% | 0,507% | 99,761% |
| Balanced | 3.591.122 | 0,3062% | 63,306% | 1,460% | 99,694% |
| Max | 1.036.356 | 0,0888% | 73,239% | 4,037% | 99,911% |

Weighted metadata bị chi phối bởi enwik8/enwik9, còn median bị chi phối bởi
synthetic nhỏ; phải báo cả hai. Balanced có 24.551 segment: 18.248 Raw, 3.088
Constant, 1.614 Recurrence, 775 Periodic, 447 Affine, 135 Sparse, 128 Copy, 93
Polynomial và 23 Piecewise Linear. Polynomial chỉ được chọn trên synthetic.
Recurrence được chọn 1.375 lần trên Silesia Balanced nhưng corpus vẫn thua
Zstd/XZ rõ rệt. `math_segments_winning_zstd` là 1.741/990/1.360 ở
Fast/Balanced/Max, song probe đó chỉ so model parameters + residual với một
Zstd frame trên transformed segment; nó không tính descriptor, transform hay
container nên không phải end-to-end archive win.

Entropy histogram input/residual weighted theo original byte lần lượt là
5,1682→5,2473 bit/B (Fast), 5,1682→6,4105 (Balanced) và 5,1682→6,3886 (Max):
trên Full tổng thể residual không có entropy marginal thấp hơn. Trên synthetic,
Balanced giảm 6,2386→3,2025 và Max giảm xuống 2,8036; ngược lại enwik9
Balanced tăng 5,1565→6,8876. Metric này là entropy của một byte histogram
residual gộp trong từng archive, bỏ qua run/order/segment context, nên không
phải lower bound trực tiếp của năm coder cấu trúc.

Phase metrics inline cho thấy bottleneck:

| Mode | Search remainder | Model fitting | Residual coding | Ba phase / compression wall |
|---|---:|---:|---:|---:|
| Fast | 14,762% | 2,104% | 82,164% | 99,031% |
| Balanced | 16,386% | 8,940% | 74,438% | 99,764% |
| Max | 13,147% | 20,384% | 66,390% | 99,920% |

`search_seconds` là phần internal total còn lại sau khi trừ fitting và residual,
không bao gồm hai phase đó. Trên enwik9, Balanced dùng 979,410 s:
208,743 s search, 92,710 s fit và 675,171 s residual; Max dùng 2.941,502 s:
464,630/203,079/2.271,132 s. Peak RSS enwik9 là 6.700,87/6.860,98/7.693,37
MiB ở Fast/Balanced/Max, so với 42,22 MiB của Zstd default và 95,03 MiB của XZ
default. Đây là row peak lấy mẫu 10 ms, không phải decoder-only RSS.

MathZip Full chỉ có 1 thread. Trên cùng grid, 4-thread Zstd tạo archive byte
giống 1-thread và tăng aggregate compression speed 1,822×/1,929×/2,664× ở
Fast/default/Max. XZ tăng 2,964×/2,894×/1,966× nhưng archive lớn hơn
0,956%/1,130%/0,755%; 7-Zip tăng 2,963×/2,546×/2,301× và lớn hơn
1,212%/0,713%/0,462%. Median per-file speedup gần một vì matrix có nhiều file
nhỏ; trên enwik9, chín tổ hợp Zstd/XZ/7-Zip × Fast/default/Max có speedup
2,735--4,133×.

### 7.3 Incompressible expansion

Full đo riêng 42 random và 42 encrypted input theo bảy size, mỗi size có sáu
noise tag. Mọi MathZip mode chọn 100% Raw fallback trên input không rỗng:

| Input size | Archive/delta | Expansion |
|---:|---:|---:|
| 0 B | archive 120 B | không định nghĩa |
| 1 B | +156 B | 15.600% |
| 31 B | +156 B | 503,226% |
| 256 B | +156 B | 60,938% |
| 4 KiB | +156 B | 3,8086% |
| 64 KiB | +156 B | 0,2380% |
| 1 MiB | +156 B | 0,01488% |

Trên toàn mỗi family random/encrypted, overhead là 6.336 B trên 6.710.976 B,
tức 0,09441%. Với 56 already-compressed input, Balanced dùng 1.648.240 B cho
1.647.842 B input, weighted expansion 0,02415%; một số file lớn vẫn được nén
nhẹ nhưng fixed overhead chi phối file nhỏ. Corrected V13/V17 bổ sung causal
evidence: tắt whole-file fallback chỉ làm random 64 KiB lớn thêm 36 B trên
matrix đó.

### 7.4 Focused và historical evidence

| Quick slice | MathZip Fast | Zstd Fast | XZ Fast | MathZip comp MB/s |
|---|---:|---:|---:|---:|
| Canterbury files (11) | 1,2573 | 4,0924 | 4,9290 | 1,549 |
| Calgary files (14) | 1,2832 | 2,8093 | 3,3115 | 1,356 |
| Silesia subset (4) | 1,1365 | 3,5911 | 4,6254 | 1,592 |
| Synthetic (22) | 1,9443 | 2,7357 | 2,6419 | 0,531 |

Trên toàn bộ 53 input Quick, MathZip nhỏ hơn raw ở 45 input và expand ở 8
input; tám trường hợp thuộc nhóm synthetic random/already-compressed/encrypted.
Ciphertext 4 KiB thành archive MathZip 4.252 B (ratio 0,9633; expand 3,8086%),
so với Zstd 4.110 B. MathZip nhỏ hơn Zstd Fast ở 6/53 và nhỏ hơn XZ Fast ở
6/53; cả hai tập thắng chỉ gồm synthetic. Không có evidence Quick cho lợi thế
trên corpus thực.

Focused Silesia phủ đủ 12 file, tổng 211.938.580 byte, với 108/108 row và 324
measured trial thành công:

| Codec | Archive B | Ratio | Comp MB/s | Decomp MB/s | Peak RSS MiB |
|---|---:|---:|---:|---:|---:|
| MathZip Fast | 187.537.684 | 1,130 | 1,567 | 37,157 | 328,750 |
| Zstd Fast | 73.448.492 | 2,886 | 157,447 | 431,280 | 15,320 |
| XZ Fast | 58.417.824 | 3,628 | 8,839 | 39,807 | 10,352 |

MathZip thua Zstd và XZ về kích thước trên cả 12/12 file. Nó nhỏ hơn raw trên
10/12 file; `sao` và `x-ray` đều chọn whole-file Raw fallback, nên mỗi archive
lớn hơn input đúng 156 B. Kết quả này thay thế khoảng trống coverage Silesia
12-file ở revision lịch sử và là independent confirmation cho slice Fast;
Full hiện cung cấp thêm Balanced/Max cùng grid rộng hơn.

Focused Bit-plane cô lập đúng một factor trên 589.979 B synthetic:

| Codec/representation | Archive B | Ratio | Comp MB/s | Decomp MB/s | Peak RSS MiB |
|---|---:|---:|---:|---:|---:|
| Packed Bit-plane v1 | 148.473 | 3,974 | 0,093 | 6,663 | 4,078 |
| Independent Bit-plane v2 | 152.803 | 3,861 | 0,800 | 6,797 | 3,637 |
| Zstd default | 81.901 | 7,204 | 8,014 | 8,202 | 1,334 |

Independent v2 lớn hơn packed 4.330 B (2,916%); theo file là 0 thắng, 2 hòa,
12 thua. Trên chín file 64 KiB, phần tăng là 3.043 B (2,062%); trên năm file
31 B là 1.287 B (143,159%) vì tám segment/plane descriptor cùng padding chi
phối. Đổi lại throughput nén cao hơn packed khoảng 8,60 lần vì mỗi optimizer
làm việc trên plane ngắn hơn. Zstd vẫn nhỏ hơn và nhanh hơn cả hai. Artifact
42/42 row, 126 measured trial và zero failure này chứng minh đường encode/decode
v2 hoạt động; nó không chứng minh compression gain.

Focused enwik8 giữ đúng 100.000.000 byte input:

| Codec | Archive B | Ratio | Comp MB/s | Decomp MB/s | Peak RSS MiB |
|---|---:|---:|---:|---:|---:|
| MathZip Fast | 99.095.482 | 1,0091 | 1,618 | 45,905 | 672,762 |
| Zstd Fast | 40.678.709 | 2,4583 | 141,185 | 505,828 | 13,484 |
| XZ Fast | 33.276.380 | 3,0051 | 6,403 | 36,810 | 10,348 |

Archive MathZip vì vậy lớn hơn Zstd 2,436 lần và XZ 2,978 lần. Zstd nén nhanh
hơn 87,26 lần và giải nén nhanh hơn 11,02 lần; XZ nén nhanh hơn 3,96 lần, còn
MathZip giải nén nhanh hơn XZ 1,247 lần. Các tỷ lệ này là so sánh trên đúng một
máy và một file, không phải confidence interval đa hệ thống.

Focused Git Copy ablation giữ nguyên toàn bộ mode/model còn lại, transform,
residual coder, segmentation và budget; factor duy nhất là `--no-copy-model`.
Trên 15 cặp, Copy-on tạo tổng 90.803 B so với 90.843 B của Copy-off, giảm 40 B
hay 0,0440%. Chỉ `combined/all_versions.bin` chọn một Copy segment và giảm
40.453 xuống 40.413 B; 14 input còn lại bằng nhau về archive size, gồm toàn bộ
11 file snapshot riêng. Compression wall time cộng theo median là 48,494 s khi
bật và 48,270 s khi tắt, khác biệt nhỏ hơn noise có thể diễn giải từ ba
repetition. Direct Zstd control dùng 1.844 B trên cùng 15 row, nhưng corpus có
cả file riêng lẫn concatenation nên con số này chỉ là control trong đúng
focused matrix, không phải aggregate tổng dụng.

Focused residual ablation giữ nguyên toàn bộ Balanced pipeline và chỉ thay năm
custom residual coder bằng Zstd residual ID 5. Trên 2.257.577 B input, custom
dùng 1.045.829 B (ratio 2,1586), hybrid dùng 322.067 B (ratio 7,0097), còn
direct Zstd dùng 328.293 B (ratio 6,8767). Zstd residual vì vậy giảm 723.762 B
hay 69,2046% so với custom; hybrid nhỏ hơn direct Zstd 6.226 B hay 1,8965%
trong matrix này. Delta không đồng đều: hybrid thắng custom trên 5 file, hòa
1 và thua 6; riêng synthetic aggregate chỉ giảm 357 B (0,3974%), còn
Canterbury giảm 723.405 B (75,6708%).

Chi phí là rất lớn: throughput tổng từ median per-file là 0,00929 MB/s cho
hybrid so với 23,716 MB/s cho direct Zstd, tức direct Zstd nhanh hơn khoảng
2.552,6 lần. Kết quả single-factor xác nhận residual coder chi phối size trên
workload này; 1,8965% endpoint gain không phải Pareto improvement và không
được khái quát ngoài 12 input đã đo.

Focused Recursive ablation giữ MathZip Fast cố định và chỉ thay Adaptive bằng
Recursive depth 4. Trên 714.617 B từ tám input synthetic 64 KiB cùng hai file
Canterbury, Adaptive dùng 266.151 B (ratio 2,6850), Recursive dùng 265.911 B
(2,6874), còn Zstd Fast dùng 165.733 B (4,3119). Recursive giảm 240 B hay
0,0902%; theo từng input nó thắng 7, hòa 1 và thua 2. Phần lớn delta đúng bằng
descriptor: sáu synthetic có cấu trúc chuyển từ hai segment xuống một; random
giữ nguyên một segment; `alice29.txt` giảm từ 5 xuống 3 segment và 74 B; `sum`
cùng `piecewise_mixed` tăng một segment và lần lượt mất 38 B.

Đổi lại, throughput tổng Recursive chỉ 0,373 MB/s so với Adaptive 0,708 MB/s,
tức chậm hơn khoảng 1,90 lần; Zstd Fast đạt 13,571 MB/s. Zstd nhỏ hơn Recursive
100.178 B (37,6735% tính trên archive Recursive). Vì matrix chỉ có mười input,
Fast mode và depth 4, kết quả này chứng minh implementation hoạt động và cô lập
trade-off metadata/search, không chứng minh Recursive tốt hơn trên Full.

Focused Segmentation ablation dùng cùng 714.617 B input và MathZip Fast để đo
đủ sáu Fixed size 256 B, 1/4/16/64/256 KiB, rồi so ChangePoint/Adaptive với
anchor 4 KiB. ChangePoint dùng 262.012 B (ratio 2,7274; 0,913 MB/s), Adaptive
dùng 264.305 B (2,7038; 0,548 MB/s), Fixed 4 KiB dùng 271.155 B (2,6355;
1,556 MB/s), còn Zstd Fast dùng 165.733 B (4,3119; 13,338 MB/s). ChangePoint
thắng Adaptive 8, hòa 1, thua 1; so best Fixed size riêng cho từng input, nó
thắng 3 và hòa 7. Đây là evidence policy/size trên mười input, không chứng minh
threshold hoặc feature weighting tối ưu cho Full.

Focused Max timeout regression replay đúng hai Canterbury input từng timeout
ở warm-up trong ablation lịch sử, với cùng limit 600 s. Trên current source,
`kennedy.xls` hoàn thành ở median 368,526 s và 430.446 B (ratio 2,392), còn
`ptt5` hoàn thành ở 158,502 s và 100.522 B (5,106). Aggregate MathZip Max là
530.968 B (ratio 2,906; 0,003 MB/s), so với Zstd Max 113.190 B (13,632;
1,306 MB/s). Phase probe MathZip cộng 3,672 s search, 143,288 s model fitting
và 380,287 s residual coding: 0,696%, 27,177% và 72,127% tổng phase time.
Artifact 4/4 row không failure chứng minh hai regression case hiện hoàn tất
dưới limit; nó không thay đổi artifact ablation lịch sử và không thay thế
frontier Full.

Corrected ablation chạy cùng 12 input/21 codec trên một clean revision và hoàn
thành 252/252 row:

| Variant/control | Archive B | Weighted ratio | Aggregate median comp time |
|---|---:|---:|---:|
| V13 custom residual | 1.044.616 | 2,1612 | 279,086 s |
| V14 Zstd residual | 324.348 | 6,9604 | 251,921 s |
| V17 no whole-file Raw fallback | 1.044.652 | 2,1611 | 279,504 s |
| V18 Fast | 1.397.088 | 1,6159 | 1,139 s |
| V19 Balanced | 1.044.616 | 2,1612 | 279,463 s |
| V20 Max | 776.839 | 2,9061 | 721,778 s |
| direct Zstd default | 328.293 | 6,8767 | 0,110 s |

V13↔V14 là single-factor residual-coder comparison: V14 giảm 720.268 B
(68,9505%) và time giảm 9,7336%. Tuy vậy V14 chỉ thắng/tie/thua 5/1/6 file;
nó thắng mạnh cả bốn Canterbury và `piecewise_mixed`, trong khi custom nhỏ hơn
trên sáu structured synthetic input. Hybrid chỉ nhỏ hơn direct Zstd 1,2017%
nhưng chậm hơn khoảng 2.285×. Vì vậy weighted result cho thấy residual coder
chi phối bytes lớn, không chứng minh majority per-file hay Pareto gain.

V13↔V17 chỉ thay whole-file fallback: 11/12 archive bằng byte, còn random
64 KiB làm V17 lớn hơn 36 B. V13 và V19 Balanced có archive hash cùng inspect
giống nhau trên 12/12 input. V19→V20 giảm 267.777 B (25,6340%) nhưng dùng
2,583× compression time; Max W/T/L là 3/1/8 và nếu bỏ `kennedy.xls` thì Max
lớn hơn Balanced 1.761 B. Fast→Balanced giảm 25,229% byte nhưng tốn 245,33×
time. Các profile thay nhiều search factor, nên đây là trade-off description,
không phải single-factor causality. `ablation_comparison.png` hiển thị median
per-file ratio; các kết luận ở đây dùng weighted raw byte totals và vì thế
không được suy từ chiều cao bar của plot.

Storage breakdown tuân đúng invariant
`container overhead + segment descriptors + model parameters + actual coded
residual = compressed bytes`. Với enwik8, các thành phần lần lượt là 120 +
219.744 + 214 + 98.875.404 = 99.095.482 byte. Có 6.104 segment: 6.062 Raw, 39
Recurrence và 3 Periodic. `raw_model_percent` là 99,311872%, còn
`raw_fallback_percent` là 90,235136%; hai metric không đồng nghĩa. Residual
entropy ước lượng 6,1553 bit/byte, nhưng actual coded residual vẫn chiếm
99,778% archive. Có 42 non-Raw segment thắng Raw probe và 0 segment thắng Zstd
level-3 probe.

### 7.5 Câu hỏi nghiên cứu

`Answered (phạm vi đo)` nghĩa là Full + focused artifact đủ để trả lời trên
matrix đã pin, không phải khẳng định universal hay causal khi không có
single-factor pair.

| # | Câu hỏi | Trạng thái | Kết luận từ evidence hiện có |
|---:|---|---|---|
| 1 | Model hơn entropy-only? | **Answered (phạm vi đo)** | Có trên controlled matrix nhưng không đủ cho general-purpose. V3 fixed+Constant → V4 fixed+all-model giảm 31,446% byte, W/T/L 10/2/0; V2 residual-only ratio 1,3053 so V4 1,9057. Full vẫn cho Balanced lớn hơn Zstd default 159,553% weighted. |
| 2 | Adaptive/Recursive hơn fixed bao nhiêu? | **Answered (phạm vi đo)** | Corrected V13 nhỏ hơn V16 fixed 9,670% (11 win/1 tie); cặp Constant V5 nhỏ hơn V3 0,454%. Focused ChangePoint nhỏ hơn Fixed 4 KiB 3,372% và Adaptive 0,868% nhưng chậm hơn Fixed 1,70×; Recursive chỉ nhỏ hơn Adaptive 0,0902% và chậm hơn 1,90×. |
| 3 | Family nào hữu ích? | **Answered (phạm vi đo)** | Cumulative V5→V6 Linear, V6→V7 Polynomial, V7→V8 Periodic, V8→V9 Recurrence giảm lần lượt 7,833%/4,623%/9,416%/5,081%. Full Balanced chọn 3.088 Constant, 1.614 Recurrence, 775 Periodic, 447 Affine nhưng Polynomial chỉ xuất hiện trên synthetic; selection không đồng nghĩa end-to-end baseline win. |
| 4 | Bit-plane tổng dụng hay chuyên biệt? | **Answered (phạm vi đo)** | Corrected V9→V10 thêm Bit-plane giảm 5,459% trên 12 input, chỉ 2 win/10 tie, gain nằm ở Canterbury. Full Balanced tự chọn Bit-plane ở 52/725 input (31 ngoài synthetic) nhưng chỉ 4/52 archive nhỏ hơn Zstd và cả bốn thuộc synthetic. Focused independent-v2 còn lớn hơn packed-v1 2,916% dù nhanh hơn 8,60×. |
| 5 | Metadata bao nhiêu? | **Answered (phạm vi đo)** | Weighted metadata Full là 0,2388%/0,3062%/0,0888% archive Fast/Balanced/Max; median per-file là 41,460%/63,306%/73,239% do file nhỏ. Trên non-synthetic median chỉ 0,507%/1,460%/4,037%. Mọi 2.175 MathZip row thỏa exact storage invariant. |
| 6 | Residual entropy giảm bao nhiêu? | **Answered (phạm vi đo)** | Full weighted input→residual là 5,1682→5,2473/6,4105/6,3886 bit/B: không giảm tổng thể. Synthetic giảm 6,2386→3,2025 Balanced và 2,8036 Max, còn enwik9 tăng 5,1565→6,8876. Histogram entropy bỏ qua run/order nên phải đọc cùng actual coded residual, vốn chiếm 99,69--99,91% Balanced/Max archive. |
| 7 | Polynomial/recurrence thắng khi nào? | **Answered (phạm vi đo)** | Polynomial được chọn ở 59 Balanced/91 Max row, đều synthetic polynomial/piecewise; zero-noise 1 MiB polynomial Max dùng Polynomial+Copy, còn Balanced thường chọn stride+Affine. Recurrence được chọn trên recurrence/piecewise và record-like `geo`/BMP/WAV/`x-ray`; 1.375 Recurrence segment trên Silesia Balanced vẫn không đủ thắng aggregate Zstd/XZ, và selection count không phải causal contribution. |
| 8 | Thắng Zstd ở đâu? | **Answered (phạm vi đo)** | Fast/Balanced/Max thắng matched Zstd ở 57/125/112 trên 725 input. Với Balanced, 121 win là synthetic; bốn win còn lại là `geo` (+2,862%), BMP (+97,473%), WAV (+88,842%) và `x-ray` (+15,528%), đều stride và chậm hơn 41,47--1.666,43×. |
| 9 | Thua Zstd/XZ ở đâu? | **Answered (phạm vi đo)** | Balanced thua Zstd/XZ ở 600/620 input. Nó thua aggregate trên Canterbury, Calgary, Silesia, enwik8/9, Pizza & Chili, mixed, Git và synthetic. enwik9 dùng 847,39 MB so 312,55/230,15 MB; Pizza dùng 24,44 MB so 2,23/0,61 MB. |
| 10 | Gain đáng search time? | **Answered (phạm vi đo)** | Không ở aggregate. Balanced/Zstd default time ratio là 174,988× trong Full; bốn non-synthetic size win chậm hơn 41,47--1.666,43×. Chỉ generated BMP giữ MathZip trên all-codec frontier; endpoint gain không bù search trên workload tổng. |
| 11 | Fast/Balanced/Max frontier? | **Answered (phạm vi đo)** | Balanced nhỏ hơn Fast 13,795% ở 4,175× time. Max nhỏ hơn Balanced chỉ 0,463% ở 3,090× time, W/T/L 135/503/87. Trong all-codec per-input frontier, MathZip Fast/Balanced/Max xuất hiện 37/62/35 lần, hầu hết synthetic; không mode nào ở global aggregate frontier. |
| 12 | Format-agnostic khả thi? | **Answered (phạm vi đo)** | Khả thi về correctness: 24.650 row/73.950 Full trial và 79.383 successful trial trong cả 13 result document được track đều bit-exact. Về compression, gain tập trung ở synthetic và bốn stride-like input; text, long-range repetition và general binaries thường cần dictionary/context/domain knowledge mạnh hơn catalog hiện tại. |
| 13 | Expansion incompressible? | **Answered (phạm vi đo)** | Random/encrypted dùng Raw fallback: +156 B, tương ứng 3,8086% ở 4 KiB, 0,2380% ở 64 KiB, 0,01488% ở 1 MiB; empty archive là 120 B. Mỗi family weighted expand 0,09441%; 56 already-compressed input Balanced expand 0,02415%. V13/V17 xác nhận fallback tránh thêm 36 B trên random 64 KiB. |
| 14 | Model search share? | **Answered (phạm vi đo)** | Fast/Balanced/Max search remainder chiếm 14,762%/16,386%/13,147%, fit 2,104%/8,940%/20,384%, residual 82,164%/74,438%/66,390% wall. enwik9 Max dùng 2.941,50 s, trong đó residual 2.271,13 s; residual evaluation là bottleneck lớn nhất. |
| 15 | Future component tốt nhất? | **Answered (phạm vi đo)** | Ưu tiên residual coder + reuse/early model selector và block/stream memory. Corrected Zstd residual giảm 68,9505% byte, trong khi Copy/Recursive chỉ 0,0440%/0,0902%; Full Max chỉ thêm 0,463% gain ở 3,090× time và peak 7,51 GiB. Thêm brute-force family trước khi giải quyết ba bottleneck này có xác suất lợi ích thấp. |

## 8. Ablation study

Corrected ablation `20260725T170925Z-dde3921f` chạy 12 input, tổng 2.257.577
byte, trên clean revision
`e5366fa7c5c1cd5801bb9cc86b19a09592403137`. Exact grid 12 × 21 hoàn thành
252/252 row và 756 measured trial, 0 failure/warning, trong 9.327,963 s. Inline
metrics, checkpoint/run identity, generated report và 15 PNG đều strict pass.

| Variant | Archive B | Weighted ratio | Comp MB/s |
|---|---:|---:|---:|
| V01 Raw-only | 2.259.449 | 0,9992 | 13,2306 |
| V02 residual-only | 1.729.564 | 1,3053 | 12,1174 |
| V03 fixed + Constant | 1.728.008 | 1,3065 | 10,1675 |
| V04 fixed + all models | 1.184.619 | 1,9057 | 2,0284 |
| V05 adaptive + Constant | 1.720.170 | 1,3124 | 0,9923 |
| V06 adaptive + linear | 1.585.427 | 1,4240 | 0,5120 |
| V07 add Polynomial | 1.512.135 | 1,4930 | 0,2998 |
| V08 add Periodic | 1.369.757 | 1,6482 | 0,1565 |
| V09 add Recurrence | 1.300.155 | 1,7364 | 0,1036 |
| V10 add Bit-plane | 1.229.185 | 1,8366 | 0,0365 |
| V11 add stride | 1.227.778 | 1,8388 | 0,0128 |
| V12 add Copy | 1.045.518 | 2,1593 | 0,0123 |
| V13 custom residual | 1.044.616 | 2,1612 | 0,0081 |
| V14 Zstd residual | 324.348 | 6,9604 | 0,0090 |
| V15 identity-only | 1.053.462 | 2,1430 | 0,0813 |
| V16 fixed partition | 1.156.440 | 1,9522 | 0,2206 |
| V17 no raw fallback | 1.044.652 | 2,1611 | 0,0081 |
| V18 Fast | 1.397.088 | 1,6159 | 1,9818 |
| V19 Balanced | 1.044.616 | 2,1612 | 0,0081 |
| V20 Max | 776.839 | 2,9061 | 0,0031 |
| direct Zstd default | 328.293 | 6,8767 | 20,4732 |

V13↔V14 là cặp causal hợp lệ trong corrected config. Zstd residual giảm
1.044.616 xuống 324.348 B, tức 720.268 B (68,9505%), và aggregate median
compression time giảm 279,086 xuống 251,921 s (9,7336%). Theo file V14
thắng/hòa/thua 5/1/6: nó thắng mạnh bốn Canterbury và `piecewise_mixed`, còn
custom coder nhỏ hơn trên sáu structured synthetic input. Hybrid nhỏ hơn
direct Zstd 3.945 B (1,2017%) nhưng chậm hơn khoảng 2.285×, nên không phải
Pareto improvement hay bằng chứng general-purpose.

V13↔V17 cũng là single-factor: V17 chỉ tắt whole-file Raw fallback, không bỏ
Raw predictor. Mười một archive bằng byte; random 64 KiB tăng 36 B khi tắt
fallback. Đây là effect nhỏ nhưng trực tiếp trên input incompressible đã pin.
Không dùng V12→V13 để gán contribution: V13 trở lại named Balanced base và
không phải bước chỉ bật/tắt một factor từ cumulative V12.

V13 và V19 Balanced có archive hash cùng inspect giống nhau trên 12/12 input.
V19→V20 giảm 267.777 B (25,6340%) nhưng compression time tăng 2,583×; Max
W/T/L là 3/1/8. Gần như toàn bộ weighted gain đến từ `kennedy.xls`; bỏ file đó,
Max lớn hơn Balanced 1.761 B. Fast→Balanced giảm 25,229% byte nhưng tốn
245,33× time. Vì Fast/Balanced/Max thay nhiều search factor, các số này mô tả
profile frontier trên matrix hẹp, không phải attribution riêng cho một
component. Plot ablation dùng median per-file ratio, còn bảng/kết luận này dùng
weighted raw byte totals; hai aggregation không được đánh tráo.

Artifact historical `20260724T032334Z-aca5bed4` không bị viết lại. Nó vẫn ghi
đủ 252 row, trong đó V20 Max timeout warm-up 600 s trên `kennedy.xls` và `ptt5`;
V13/V14/V17 của config cũ còn thay nhiều factor cùng lúc nên không dùng cho
causal comparison. Strict validation với `--allow-failures` vẫn pass evidence
khác và phát đúng hai warning. Focused Max regression hiện hành đóng hai case
timeout dưới cùng limit nhưng không được ghép vào legacy V20. Focused residual
run 36/36 vẫn là independent confirmation của residual effect; corrected
complete matrix nay là primary source cho V13/V14/V17 và profile comparison.
Full là nguồn breadth chính; các artifact này vẫn là nguồn causal/focused và
không được trộn revision để tạo cặp giả.

## 9. Failure analysis

Failure quan sát được:

- **Không có execution failure trong Full:** 24.650/24.650 row hoàn thành,
  nhưng thành công chức năng không phải thành công compression. Balanced thua
  Zstd/XZ về size ở 600/620 input; Max thua 613/624 matched-level input.
- **Dữ liệu tổng dụng và repetition:** Canterbury, Calgary, Silesia, enwik8/9,
  Pizza & Chili, mixed và Git đều có weighted ratio MathZip Balanced thấp hơn
  Zstd/XZ. Pizza dùng 24,44 MB thay vì 2,23/0,61 MB; enwik9 dùng 847,39 MB
  thay vì 312,55/230,15 MB. Copy không khai thác được repetition Git bằng
  dictionary codec: ratio MathZip Max combined 55,275 vẫn thấp xa
  Zstd/XZ default 287,695/224,095.
- **Win hẹp và đắt:** ngoài `synthetic`, Balanced chỉ thắng Zstd trên
  `geo`, generated BMP/WAV và `x-ray`; cả bốn chọn stride, nhưng nén chậm hơn
  41,47--1.666,43×. Chỉ BMP nằm trên all-codec Pareto frontier.
- **Max không monotonic:** Max nhỏ hơn Balanced chỉ 0,4627% weighted ở 3,090×
  time, hòa byte trên 503 input và lớn hơn trên 87. Trong 48/102 screened
  large-input case, Max phải chọn lại Balanced frontier để giữ exact archive
  tốt hơn.
- **Random/encrypted/already-compressed:** Raw fallback giới hạn random và
  encrypted 1 MiB ở +156 B (0,01488%), nhưng file 4 KiB vẫn expand 3,8086%,
  31 B expand 503,226% và empty archive tốn 120 B. Already-compressed matrix
  Balanced expand 0,02415% weighted.
- **Search explosion (artifact lịch sử):** hai Max warm-up Canterbury vượt
  timeout 600 s. Đây là functional benchmark failure, không phải missing-data
  row được phép bỏ. Focused current-source regression hoàn thành đúng hai case
  dưới limit; Full dùng 5.400 s/op và không timeout, nhưng enwik9 Max vẫn cần
  median 2.941,50 s.
- **Memory:** enwik9 peak RSS MathZip là 6,54--7,51 GiB, so với 42,22 MiB của
  Zstd default và 95,03 MiB của XZ default. enwik8 là 672,78--777,16 MiB.
  Buffering input/transforms/search state làm codec chưa phù hợp streaming.
- **Residual-coder dominance:** coded residual chiếm 99,69% Balanced và
  99,91% Max archive; phase residual chiếm 74,44%/66,39% compression wall.
  Corrected Zstd-residual pair giảm 68,9505% byte nhưng hybrid vẫn chậm hơn
  direct Zstd khoảng 2.285×. Lợi ích entropy coder không được gán nhầm cho
  model catalog.
- **Residual entropy mismatch:** trên Full, estimated residual entropy
  Balanced/Max cao hơn input 1,2422/1,2204 bit/B; chỉ synthetic giảm mạnh.
  Fit một function không mặc nhiên tạo residual dễ mã hóa trên arbitrary byte
  order.
- **Metadata có hai mặt:** weighted metadata chỉ 0,3062% Balanced archive vì
  enwik8/9 chi phối, nhưng median per-file là 63,306% và đạt 100% ở một số file
  nhỏ. Chỉ báo một aggregate sẽ che giấu failure mode còn lại.
- **Reference phụ thuộc workload:** Copy tạo size delta lớn nhất trong custom
  chain lịch sử (14,887%) nhưng focused Git pair chỉ giảm 0,0440%; claim
  “mathematical model” phải luôn báo corpus và result khi tắt Copy.
- **Recursive trade-off:** depth 4 chỉ giảm 0,0902% so Adaptive trên focused
  matrix nhưng chậm hơn 1,90 lần; hai input còn tăng đúng khoảng một descriptor.
- **Segmentation trade-off:** ChangePoint nhỏ hơn Fixed 4 KiB 3,372% và
  Adaptive 0,868% trên focused matrix, nhưng chậm hơn Fixed khoảng 1,70 lần;
  Zstd Fast vẫn nhỏ hơn 36,746% và nhanh hơn 14,60 lần.
- **Independent Bit-plane trade-off:** v2 nén nhanh hơn packed v1 khoảng 8,60
  lần trên focused matrix nhưng archive lớn hơn 2,916%; tám descriptor tối
  thiểu làm năm input 31 B chịu overhead 143,159%. Full chọn Bit-plane rộng
  hơn nhưng không có no-Bit-plane pair trên toàn 725 input, nên selection count
  không được diễn giải như causal gain.
- **Integrity:** mọi warm-up/repetition hoàn thành đều khôi phục đúng SHA-256;
  không quan sát corruption hoặc nondeterministic archive. Full có 73.950
  measured trial; bộ evidence chính 12 artifact có 77.979 trial thành công,
  và Quick lịch sử đưa tổng 13 result document được track lên 79.383.

Full đã đóng coverage size/noise cho generator random/encrypted, nhưng chưa cô
lập over-segmentation theo từng feature, modulo mismatch với typed multi-byte
values, false schema của Bit-plane hoặc từng residual coder trên toàn Full.
Decode-bomb resistance được test ở parser/limit suite, nhưng không thay cho
peak-memory benchmark trên hostile archive.

## 10. Limitations

- Không có codec lossless làm nhỏ mọi file.
- Model catalog hữu hạn không tìm shortest general description.
- Encoder search có thể chậm hơn nhiều so với decoder.
- Metadata có thể lớn hơn residual saving.
- Mathematical smoothness thường không tồn tại trong arbitrary byte order.
- Fixed-point harmonic chưa được chuẩn hóa; floating point không portable đủ.
- Codec không biết file semantics, endian, record width hoặc encryption state.
- V1 chưa cam kết streaming/random access.
- Integrity digest không phải chữ ký/MAC.
- Evidence chỉ đến từ một máy; chưa có variance đa máy hay confidence interval
  ngoài ba repetition trong run.
- Full có 644/725 input synthetic và chủ ý coi file riêng/stream `combined` là
  workload khác; tổng 725-row không phải estimate cho một phân bố file tự
  nhiên. Kết luận chính vì vậy luôn kèm per-corpus và paired count.
- Pizza & Chili chỉ là một repetitive subset; mixed corpus và Git snapshots
  được tự sinh, không đại diện mọi media, executable, repository history hoặc
  cross-object store. Encrypted matrix dùng deterministic generator của dự án,
  không phải survey nhiều cipher/mode triển khai thực.
- MathZip chỉ được đo ở một thread. Full có 4-thread data cho XZ/Zstd/7-Zip
  nhưng không có MathZip parallel implementation để so scaling trực tiếp.
- Focused Recursive chỉ có mười input, Fast mode và depth 4; nó không thay
  benchmark depth/search budget rộng.
- Focused Bit-plane chỉ có 14 input synthetic và ép transform/no-fallback để cô
  lập representation. Full đo lựa chọn tự động nhưng không có paired
  no-Bit-plane variant trên cùng 725 input.
- V20 Max lịch sử không hoàn thành cùng 12 input; focused current-source
  regression đóng hai timeout case nhưng không tạo Max Canterbury aggregate
  cùng revision/grid.
- Full dùng `screened_large_v1` ở 102 Balanced/Max input lớn: vẫn Adaptive,
  exact-compare mọi finalist/Raw, giữ Identity và padded Bit-plane, và Max đánh
  giá thêm Balanced frontier. Proxy pruning không phải lower bound nên kết quả
  không được gọi là global exhaustive-MDL; artifact chỉ chứng minh optimum
  trong candidate frontier thực sự đã đánh giá.
- V14 trong artifact lịch sử thay cả model set và residual coder nên không
  được dùng cho attribution. Corrected V13↔V14 và focused residual run là hai
  single-factor comparison hợp lệ, nhưng đều vẫn giới hạn ở 12 input.
- `estimated_residual_entropy` là entropy byte histogram gộp, không mô hình hóa
  order/run/context. `search_seconds` là internal remainder sau khi trừ model
  fitting và residual coding, không phải tổng của cả ba phase.
- RSS lấy mẫu mỗi 10 ms; peak ngắn hơn sampling interval có thể bị bỏ lỡ.
- Fresh output path ngăn cached archive reuse nhưng benchmark không flush OS
  page cache giữa repetition.
- CPU governor và container image digest không khả dụng trong environment
  capture.

## 11. Future work

Full thay đổi thứ tự ưu tiên: residual evaluation đang chiếm 66--82% wall,
Max chỉ thêm 0,463% weighted size gain so Balanced ở 3,090× time, và enwik9
peak 6,54--7,51 GiB. Vì vậy ba việc có xác suất cải thiện cao nhất là:

1. thêm arithmetic/rANS hoặc block Zstd residual, cache/reuse encoded candidate
   thay vì mã hóa lại trong search;
2. learned/analytic selector từ chối sớm input/segment mà Zstd/raw probe sẽ
   thắng, với budget và fallback deterministic;
3. block/stream search để chặn peak memory trước khi mở rộng model catalog.

Các hướng sau vẫn là roadmap nhưng cần ablation riêng:

- Rice/Golomb và residual coder chuyên cho sparse/run;
- LFSR bit-plane với normative tap/seed layout;
- fixed-point harmonic bằng bảng normative;
- e-graph/symbolic regression có grammar và timeout;
- shared/cross-object model và content-defined dedup;
- streaming decoder và optional index/random access;
- domain-aware/record-stride transforms được báo như profile riêng;
- GPU/parallel model fitting, không thay decoder semantics;
- tiny neural INR chỉ như extension versioned, self-contained và vẫn cần exact
  residual.

## 12. Kết luận hiện tại

Mười hai artifact trong bộ evidence chính cho thấy MathZip là codec lossless
deterministic có container, decode limits, inspect metrics và benchmark
protocol hoạt động end-to-end. Full exact grid hoàn thành 24.650/24.650 row,
73.950 measured trial, 0 failure; bộ này có 77.979 successful trial, còn Quick
lịch sử đưa tổng cả 13 result document được track lên 79.383 trial khôi phục
đúng SHA-256. Source, config, input grid, host identity và binary ổn định suốt
Full. Historical ablation vẫn giữ nguyên hai recorded Max timeout thay vì viết
lại quá khứ.

Kết quả compression chủ yếu là âm. Trên 725 workload row, MathZip
Fast/Balanced/Max đạt ratio 1,0787/1,2513/1,2572, so với Zstd default 3,2479
và XZ default 4,3610. Balanced thua matched Zstd/XZ ở 600/620 input. enwik9
Balanced dùng 847,39 MB và peak 6,70 GiB, trong khi Zstd/XZ dùng
312,55/230,15 MB với 42,22/95,03 MiB. Pizza & Chili, Silesia, Canterbury,
Calgary và Git đều cho aggregate âm.

Niche quan sát được là structured synthetic và một số record/stride-like
stream. Ngoài synthetic, Balanced thắng Zstd trên `geo`, generated BMP/WAV và
`x-ray`; tất cả dùng stride, nhưng nén chậm hơn 41,47--1.666,43× và chỉ BMP
nằm trên all-codec Pareto frontier. Random/encrypted 1 MiB chỉ expand 156 B
nhờ Raw fallback, nhưng fixed overhead vẫn lớn trên file nhỏ. Đây là evidence
cho khả năng phát hiện một số cấu trúc, không phải universal superiority.

Ablation giải thích vì sao mở rộng brute-force chưa hợp lý. Zstd residual giảm
68,9505% byte so custom trên corrected pair, trong khi Copy/Recursive chỉ giảm
0,0440%/0,0902% trên focused pairs. Full Max chỉ nhỏ hơn Balanced 0,4627% ở
3,090× time và còn lớn hơn trên 87 input. Residual coding chiếm phần lớn wall
và gần như toàn bộ archive; peak RSS tăng gần 10× khi input tăng từ enwik8
100 MB lên enwik9 1 GB. Residual coder/reuse, early model selection và
block/stream memory vì vậy là hướng tiếp theo có evidence mạnh nhất.

> MathZip có lợi trên một số structured synthetic và record/stride-like
> workload, nhưng không cạnh tranh tổng dụng với Zstd/XZ; chi phí search và
> memory hiện lớn hơn giá trị compression thu được.

Full đã hoàn tất coverage enwik9, Pizza & Chili subset, mixed, Git,
multi-thread baseline và encrypted size/noise matrix trên máy này. Bước xác
nhận tiếp theo là đa máy, corpus tự nhiên rộng hơn, MathZip parallel/streaming
và Full single-factor variants. Mọi kết quả âm cùng hai timeout lịch sử phải
tiếp tục được giữ trong artifact thay vì bị loại khỏi báo cáo.

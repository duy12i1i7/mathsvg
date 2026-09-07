# Phương pháp benchmark MathZip

Trạng thái: protocol bắt buộc cho kết quả có thể công bố. Các số đo được nêu
trong tài liệu này chỉ là snapshot của artifact bất biến đã định danh; raw
JSON/CSV, archive thật và xác minh round-trip mới là evidence gốc.

## 1. Câu hỏi và nguyên tắc

Benchmark phải phân biệt ba nguồn lợi:

1. prediction/model toán học;
2. residual entropy coder;
3. dictionary/reference hoặc Zstd hybrid.

Không được suy lợi ích của (1) từ một pipeline mà phần lớn kích thước giảm do
(2) hoặc (3). Mọi so sánh chính phải:

- dùng cùng byte input;
- chạy trên cùng máy và cùng chính sách thread;
- ghi exact executable version và command;
- tính kích thước file output thật, gồm toàn metadata/footer;
- giải nén mỗi output và so SHA-256 với input;
- giữ cả kết quả MathZip thua, timeout hoặc lỗi.

## 2. Profile corpus

### 2.1 Quick

Quick là smoke/per-commit benchmark, không đủ để tuyên bố hiệu năng tổng quát:

- một lát synthetic 4 KiB được pin bằng glob trong `configs/quick.yaml`; đây
  không phải toàn bộ family/size matrix;
- Canterbury, từng file và stream gộp có manifest;
- Calgary, từng file và stream gộp;
- subset Silesia cố định trong manifest;
- enwik8 được mô tả trong input config nhưng mặc định `enabled: false`; run
  focused dùng `configs/enwik8.yaml`.

### 2.2 Full

Full gồm:

- toàn synthetic matrix;
- Canterbury, Calgary và mọi file Silesia;
- enwik8 và enwik9 theo exact grid đã pin;
- subset Pizza & Chili Repetitive Corpus cố định;
- mixed file corpus có nguồn/license;
- Git snapshot/version corpus;
- companion ablation matrix chạy riêng bằng `configs/ablation.yaml`; raw
  artifact Full và ablation không bị trộn thành một result document.

Config Full hiện pin enwik9 là input bắt buộc. Nếu resource budget không đủ,
không được xóa input rồi gọi grid rút gọn là Full: artifact phải được để ở
trạng thái chưa chạy/chưa hoàn tất. Một protocol tương lai muốn cho phép
resource skip phải version config/schema và giữ một row có lý do explicit.
Discovery đã verify hiện tại là 725 input × 34 codec/thread key = 24.650 row.
Với một warm-up, ba repetition và metrics `inline`, ba MathZip mode cần đúng
8.700 compression/search và 8.700 decode; 6.525 inspect chỉ chạy trên measured
repetition. Các baseline còn lại cần 174.000 operation và Raw cần 5.800 copy.
Thiếu bất kỳ executable gzip/bzip2/XZ/Zstd/LZ4/Brotli/7-Zip nào làm script Full
fail-fast trước khi tạo run.

Run công bố `20260726T044843Z-213f07c3` đã thực thi nguyên grid này:
24.650/24.650 row `ok`, 73.950 measured trial, 0 failure, 0 resume và
129.095,967 giây elapsed. Nó dùng một warm-up + ba repetition như config pin,
inline metrics, archive thật và SHA-256 sau mỗi round trip. Exact-grid,
checkpoint/run identity, 15 PNG Full cùng raw JSON/CSV strict-verify với
0 warning. Source revision/tree/binary tương ứng là
`e60f423b5f85d1716eb2356f2460f97ba4227885`,
`55b6a32cc5abedcdcba8989065be83cbaa8600bede102e73c39306bb58153e63`
và `366ca45932751ecb10d52f4db3f4806b94acd44e3ec9ccbf45e33b45bd82e228`.
Companion corrected ablation được giữ ở result document riêng và cung cấp
`ablation_comparison.png`; không ghép row ablation vào Full.

Trước khi mở Full, diagnostic ngày 2026-07-26 trên cùng host và revision sạch
`520ae3bcbde3cc974c0805b93882e98a4495a2f0` chạy một exact
compress/decompress trên enwik9 1.000.000.000 B cho các cell có rủi ro cao
nhất. Đây là preflight, không phải row được ghép vào artifact Full. Cả tám
restored file đều có SHA-256
`159b85351e5f76e60cbe32e04c677847a9ecba3adc79addab6f4c6c7aa3744bc`:

| Cell | Comp wall | Archive B | Decode wall | Peak comp RSS KiB |
|---|---:|---:|---:|---:|
| MathZip Max t1 | 2.982,30 s | 847.072.259 | 34,80 s | 7.876.036 |
| Brotli q11 t1 | 3.489,38 s | 223.345.663 | 6,46 s | 212.044 |
| XZ `-9e` t1 | 1.484,25 s | 211.776.220 | 18,95 s | 691.252 |
| XZ `-9e` t4 | 511,57 s | 214.154.620 | 7,14 s | 3.753.908 |
| 7-Zip LZMA2 `-mx=9` t1 | 1.210,42 s | 213.323.141 | 16,44 s | 693.908 |
| 7-Zip LZMA2 `-mx=9` t4 | 458,96 s | 214.790.813 | 6,98 s | 1.887.588 |
| Zstd `-19` t1 | 1.028,39 s | 235.515.928 | 2,79 s | 232.552 |
| Zstd `-19` t4 | 289,93 s | 235.515.928 | 2,77 s | 616.876 |

Brotli q11 chỉ còn 110,62 giây so với timeout cũ 3.600 giây. Vì một Full row
cần warm-up + ba repetition độc lập, config pin timeout mỗi operation ở 5.400
giây. Thay đổi này không giảm grid, codec hay repetition; nó ngăn jitter nhỏ
biến một run nhiều ngày thành `complete_with_failures`. Mọi timeout thực tế
vẫn phải được ghi thành failure, không được bỏ row.

### 2.3 Focused Silesia

`configs/silesia.yaml` pin chính xác cả 12 member trong manifest Silesia và
chạy MathZip Fast, Raw, gzip, bzip2, XZ, Zstd, LZ4, Brotli và 7-Zip Fast với
một thread. Profile này dùng cùng protocol 1 warm-up + 3 repetition, archive
thật, metrics probe, inspect và restored SHA-256 như Quick. Nó là evidence
per-file bắt buộc của Silesia, nhưng không thay thế Full vì không chứa enwik9,
Pizza & Chili, mixed/Git corpus, encrypted matrix hay multi-thread grid.

Run công bố `20260724T165449Z-4ef6e508` hoàn thành 108/108 row, không có
failure. Mọi tên file trong config được test khớp tập `archive.members` của
`datasets/manifests/silesia.json`; thêm/bớt member trong manifest mà không cập
nhật profile sẽ làm test cấu hình thất bại.

Nguồn tham chiếu:

- [Canterbury và Calgary Corpus](https://corpus.canterbury.ac.nz/descriptions/)
- [Silesia Corpus](https://sun.aei.polsl.pl/~sdeor/index.php?page=silesia)
- [enwik8/enwik9 benchmark](https://mattmahoney.net/dc/text.html)
- [Pizza & Chili Repetitive Corpus](https://pizzachili.dcc.uchile.cl/repcorpus.html)

Script tải MUST dùng URL và checksum được pin trong manifest, tải vào file tạm,
verify digest trước khi rename, và không commit corpus lớn vào Git. Nếu upstream
thay byte hoặc không còn truy cập được, script phải fail rõ ràng; không tự thay
mirror không có checksum tương đương.

## 3. Synthetic corpus

Generator dùng PRNG deterministic được chỉ rõ (algorithm + version), seed ghi
trong manifest và metadata result. Không dùng implementation-dependent
`hash()` hoặc `thread_rng()`.

Matrix generator v2 đã triển khai:

| Family | Tham số |
|---|---|
| Constant | `00`, `ff` |
| Linear mod 256 | `(a,b)=(17,29)` |
| Polynomial | degree 2/3/4 với coefficient cố định ghi trong manifest |
| Periodic | pattern `[1,2,3,4,9,16,25]`, period 7 |
| Multi-periodic | period 5 và 11, phép cộng modulo 256 |
| Recurrence | order 2, hệ số `[1,1]` (Fibonacci modulo 256) |
| LFSR | hai tap/state được ghi cho width 8 và 16 |
| Piecewise mixed | constant/linear/periodic/recurrence/random |
| Noisy model | density 0, 0.1, 1, 5, 10, 25% |
| Random | SHA-256 counter stream deterministic từ seed cố định |
| Encrypted | structured telemetry plaintext, ChaCha20-IETF RFC 8439; deterministic key/nonce recorded in manifest |
| Already compressed | gzip/zstd/xz/zip + media fixture hợp pháp |

Mỗi family dùng nhiều size, tối thiểu `0, 1, 31, 256, 4096, 65536` byte; Full
thêm đúng size 1 MiB. Noise positions và values đều sinh từ seed.
Expected model selection là một diagnostic, không phải điều kiện lossless:
codec có thể chọn Raw nếu metadata làm candidate model lớn hơn.

Family `encrypted` không phải random stream đổi nhãn. Generator tạo plaintext
gồm các record `MZTL` little-endian có index, timestamp, sensor, reading, flags
và vùng zero, sau đó mã hóa byte thật bằng ChaCha20-IETF. Key 256-bit và nonce
96-bit được dẫn xuất bằng SHA-256 counter stream từ seed; nonce còn
domain-separate theo kích thước để không tái sử dụng cùng key/nonce giữa các
size. Manifest lưu key/nonce synthetic, counter ban đầu, schema plaintext và
phép dẫn xuất. Implementation thuần Python được khóa bằng hai test vector RFC
8439 cùng một golden vector seed chính thức. Sáu noise-density entry của cùng
size là các perturbation deterministic áp dụng **sau** mã hóa trên một base
ciphertext và vì vậy chủ ý dùng chung key/nonce; chúng không được diễn giải như
sáu phiên mã hóa độc lập.

Generator ghi marker khi đang tái sinh, atomic-replace từng payload cùng hai
manifest, và verifier từ chối marker dang dở, file không khai báo, hoặc CSV/JSON
không nhất quán. Availability, version và executable SHA-256 của sample Zstd
tùy chọn là một phần cache identity, nên thay đổi toolchain làm corpus stale.

## 4. Baseline và mức cấu hình

Mọi baseline có mặt trên máy phải chạy. Nếu thiếu binary, row vẫn được ghi với
`status=unavailable`. Version lấy từ stdout/stderr của chính executable và lưu
cùng raw result.

| Codec | Fast | Default | Max |
|---|---|---|---|
| raw | copy | copy | copy |
| gzip | `-1` | `-6` | `-9` |
| bzip2 | `-1` | `-6` | `-9` |
| xz/LZMA2 | `-1` | `-6` | `-9e` |
| zstd | `-1` hoặc `-3` | `-3` | `-19` |
| lz4 | default | default | high-compression nếu ghi rõ |
| brotli | quality thấp pin trong config | quality mặc định pin | `-q 11` |
| 7-Zip LZMA2 | tùy chọn | normal | Ultra, nếu tự động hóa được |

Các option thực tế MUST nằm trong config/result, không chỉ nhãn Fast/Default/
Max. So sánh headline phải cùng class tài nguyên; ví dụ MathZip max và Zstd
fast được phép hiện trên Pareto plot nhưng không được mô tả là so sánh cùng
setting.

MathZip chạy ít nhất:

- `fast`, `balanced`, `max`;
- năm custom residual coder;
- Zstd residual ID 5 trong run explicit; coder này tắt trong ba profile thường;
- raw fallback bật;
- copy/reference bật và tắt. Phép đo paired trên đúng Git snapshots dùng
  `configs/git-copy-ablation.yaml`: hai MathZip codec có cùng mode, model còn
  lại, residual coder, transform và segmentation; factor duy nhất là Copy.
  Run công bố `20260724T140810Z-c825d7e1` hoàn thành 45/45 row và cho delta
  40/90.843 byte (0,0440%) trên 15 archive Copy-off.

Standard Balanced/Max với input ≥1 MiB dùng policy search
`screened_large_v1`. Policy vẫn giữ Adaptive segmentation và exact serialized
MDL khi so các finalist, nhưng prune gần đúng trước exact encoding: Balanced
dùng anchor 64 KiB/top 3 transform/top 2 model-mode; Max dùng anchor 256
KiB/top 5/top 3 đồng thời đánh giá toàn Balanced frontier. Identity và padded
Bit-plane luôn có mặt; Raw whole-file luôn được so exact. Vì proxy không phải
lower bound, kết quả này không được mô tả là global optimum trên toàn catalog.
Custom/ablation options và input nhỏ hơn threshold giữ đường exhaustive cũ.
`--exhaustive-search` là opt-out explicit cho diagnostic. Mọi raw trial
instrumented ghi các frontier đã đánh giá và frontier thắng để có thể tách hai
lớp search.

### 4.1 Focused enwik8

`configs/enwik8.yaml` là suite riêng cho payload enwik8 100 MB: một warm-up,
ba repetition, timeout 3.600 giây, một thread, MathZip Fast và các baseline
Raw/gzip/bzip2/XZ/Zstd/LZ4/Brotli/7-Zip ở class Fast. Chạy bằng
`scripts/benchmark_enwik8.sh`; suite tải/verify enwik8 trước khi đo và giữ
artifact trong `benchmarks/results/enwik8`.

Đây là workload tập trung, không thay Full suite. Thiếu executable baseline
vẫn phải tạo row unavailable và license/provenance của enwik8 phải được ghi
trong result.

## 5. Ablation

Các variant 1--12 tạo một chuỗi bổ sung component có kiểm soát. Variant 13/14
là cặp residual single-factor từ cùng Balanced pipeline; 15--17 mỗi variant
thay một factor từ Balanced; 18--20 là so sánh profile. Tất cả giữ seed,
corpus, thread và các budget còn lại cố định:

1. Raw only.
2. Residual coder only.
3. Fixed segmentation + Constant.
4. Fixed segmentation + mọi basic model.
5. Adaptive + Constant.
6. Adaptive + Constant/Affine.
7. Thêm Polynomial.
8. Thêm Periodic.
9. Thêm Recurrence.
10. Thêm Bit-plane.
11. Thêm Stride transpose.
12. Thêm Copy/reference.
13. Custom residual.
14. Zstd residual.
15. Không transform.
16. Không adaptive partition.
17. Không raw fallback, chỉ để định lượng expansion.
18. Fast.
19. Balanced.
20. Max.

Config phải lưu explicit override transform/model/residual ID, segmentation
mode và search budget; phần không override được pin bởi named base profile cùng
source revision. Nhãn như `all_models` không tái lập được nếu model list thay
đổi giữa các version. Variant 14 dùng cùng Balanced pipeline với variant 13 và
chỉ thay danh sách residual coder thành Zstd level 3; direct `zstd-default` là
control riêng trong cùng matrix, không được gộp hai row. Variant 17 chỉ tắt
whole-file Raw fallback; Raw predictor theo segment vẫn giữ nguyên. Artifact
ablation công bố ngày 2026-07-24 dùng config cũ: V12→V13 reset nhiều override,
V14 thay thêm model set/tắt Copy, và V17 đồng thời bỏ Raw predictor. Báo cáo
giữ nguyên bytes/nhãn lịch sử nhưng không dùng các cặp đó để suy luận nhân quả;
hai Max warm-up timeout 600 giây cũng phải được giữ như failure evidence.

Run corrected `20260725T170925Z-dde3921f` là lần chạy config single-factor hiện
tại: 12 input × 21 codec = 252/252 row `ok`, 0 failure/warning, một warm-up và
ba measured repetition, metrics `inline`, elapsed 9.327,963 giây. Run dùng
revision sạch `e5366fa7c5c1cd5801bb9cc86b19a09592403137`, có
`expected-grid.json`, checkpoint/run identity, report và 15 PNG; strict result
cùng plot verification đều pass. Artifact corrected là nguồn cho attribution
V13/V14/V17, nhưng không ghi đè artifact legacy
`20260724T032334Z-aca5bed4` hay biến hai timeout cũ thành success hồi tố.

Trên corrected artifact, V13 custom và V14 Zstd residual chỉ khác residual
coder. V14 dùng 324.348 B thay vì 1.044.616 B, giảm 720.268 B (68,9505%);
theo file V14 thắng 5, hòa 1, thua 6. Vì bốn Canterbury file lớn làm weighted
aggregate nghiêng mạnh về V14, phải báo cả paired count và synthetic
counterexamples, không chỉ aggregate. V13/V17 chỉ khác whole-file Raw fallback:
11 cặp bằng byte, còn random 64 KiB làm V17 lớn hơn 36 B; đây là direct evidence
rằng fallback chặn đúng expansion nhỏ trên input đó.

V19 Balanced bằng V13 trên cả 12 file. V20 Max dùng 776.839 B thay vì
1.044.616 B (giảm 25,6340% theo tổng byte), nhưng aggregate median compression
time cao hơn khoảng 2,58 lần; Max chỉ thắng size 3 file, hòa 1 và thua 8.
Balanced/Max thay nhiều search factor cùng lúc, nên đây là mô tả trade-off
profile chứ không phải single-factor causal comparison. Matrix ablation vẫn là
focused 12-input evidence và không được gọi là Full.

`configs/residual-ablation.yaml` tách riêng cặp V13/V14 đã sửa trên đúng 12
input lịch sử. Hai MathZip entry cùng `mathzip-balanced`, cùng mọi search
option, và chỉ khác danh sách residual coder; `zstd-default` là control độc
lập. Chạy bằng `scripts/benchmark_residual_ablation.sh`. Run công bố
`20260724T143401Z-3bb520a6` hoàn thành 36/36 row: Zstd residual giảm archive
MathZip 723.762/1.045.829 byte (69,2046%) và hybrid nhỏ hơn direct Zstd
6.226/328.293 byte (1,8965%) trên matrix này.

`configs/recursive-ablation.yaml` là cặp segmentation single-factor trên tám
input synthetic 64 KiB và hai file Canterbury. Hai MathZip entry cùng
`mathzip-fast`; một entry dùng Adaptive, entry kia dùng Recursive với depth 4.
Zstd Fast là control độc lập. Chạy bằng
`scripts/benchmark_recursive_ablation.sh`. Run công bố
`20260724T161541Z-70dfeff6` hoàn thành 30/30 row: Recursive giảm
240/266.151 byte (0,0902%) so Adaptive nhưng throughput tổng giảm từ 0,708
xuống 0,373 MB/s. Không dùng focused matrix này thay cho Full.

`configs/segmentation-ablation.yaml` dùng cùng mười input và cùng
`mathzip-fast` để chạy đủ sáu fixed size bắt buộc: 256 B, 1/4/16/64/256 KiB.
Hai entry còn lại đặt cùng anchor 4 KiB và chỉ đổi ChangePoint/Adaptive; Zstd
Fast là control độc lập. Chạy bằng
`scripts/benchmark_segmentation_ablation.sh`. Profile này đo ảnh hưởng của
boundary policy và kích thước fixed; không được dùng thay cho Full corpus.
Run công bố `20260725T051723Z-a5f0fdfc` hoàn thành 90/90 row, 270 measured
trial và 0 failure. Trên 714.617 B input, ChangePoint 4 KiB dùng 262.012 B,
Adaptive 4 KiB dùng 264.305 B và Fixed 4 KiB dùng 271.155 B; ChangePoint thắng
Adaptive 8, hòa 1, thua 1.

`configs/max-timeout-regression.yaml` replay đúng `kennedy.xls` và `ptt5`, là
hai row Max đã timeout ở artifact ablation lịch sử. Profile dùng một warm-up,
ba repetition, timeout 600 giây và Zstd Max control.
`collect_mathzip_inspection=true` giữ inspection và encoder phase metrics cho
mọi measured MathZip trial; correctness được kiểm bằng restored SHA-256,
archive hash và strict evidence validation. Chạy bằng
`scripts/benchmark_max_timeout_regression.sh`. Run công bố
`20260725T055807Z-2e6ce6d4` ở revision sạch
`e6ce84274a45b5a6e4738844921f9b374439c643` hoàn thành 4/4 row, 12 measured
trial và 0 failure. Trên 1.542.960 B input, MathZip Max dùng 530.968 B (ratio
2,906; 0,003 MB/s), còn Zstd Max dùng 113.190 B (13,632; 1,306 MB/s).
Median MathZip là 368,526 s/430.446 B cho `kennedy.xls` và
158,502 s/100.522 B cho `ptt5`, đều dưới 600 s. Phase probe MathZip cộng
3,672 s search, 143,288 s model fitting và 380,287 s residual coding, tương
ứng 0,696%, 27,177% và 72,127% tổng phase time. Đây là focused current-source
regression; hai timeout trong artifact ablation lịch sử vẫn được giữ nguyên và
profile này không thay thế Full.

`configs/bit-plane-ablation.yaml` cô lập representation Bit-plane trên chín
input synthetic 64 KiB và năm input 31 B. Hai MathZip entry giữ cùng Balanced
model/residual/Adaptive options, ép `--no-raw-fallback`, và chỉ đổi
`bit-plane-packed` v1 sang `bit-plane-independent` v2; Zstd default là control
độc lập. Input 31 B bắt buộc để đo padding/metadata khi kích thước không chia
hết cho 8. Chạy bằng `scripts/benchmark_bit_plane_ablation.sh`. Run công bố
`20260724T174812Z-583dbb61` hoàn thành 42/42 row: independent lớn hơn packed
4.330/148.473 B (2,916%; 0 thắng/2 hòa/12 thua) nhưng throughput nén cao hơn
khoảng 8,60 lần. Đây là focused representation evidence, không thay Full hay
chứng minh lợi ích trên corpus tổng dụng.

## 6. Môi trường

Mỗi run suite ghi:

- UTC timestamp và suite/run UUID;
- Git commit hoặc, nếu không có Git, SHA-256 manifest của source tree;
- dirty-tree flag;
- hostname đã ẩn danh nếu cần;
- CPU model, physical/logical core, instruction set;
- RAM/swap;
- OS, kernel, container image digest;
- Rust/Cargo/Python/compiler version;
- version và SHA-256 executable của từng codec;
- build revision/dirty state nhúng trong MathZip binary và đối chiếu source run;
- CPU governor, power mode và affinity nếu đọc được;
- thread count và environment variables ảnh hưởng codec;
- filesystem, storage medium và free space;
- timer/RSS collection method.

Build và download nằm ngoài timed region. Binary release phải được build một
lần trước suite. Không thay binary giữa các repetition.

## 7. Quy trình đo một sample

Với mỗi `(dataset file, codec, configuration)`:

1. Verify input size/SHA-256 theo manifest.
2. Tạo working directory riêng, không reuse output cũ.
3. Chạy một warm-up không ghi vào statistics.
4. Chạy ít nhất ba measured repetitions. Full publication hiện tại pin đúng
   ba vì ngân sách exact-grid; một revision tương lai MAY tăng lên năm hoặc hơn
   nhưng phải đổi config identity và không được trộn với run hiện tại.
5. Mỗi repetition tạo archive mới và file restored mới.
6. Đo compression wall time, CPU time, peak RSS.
7. Ghi compressed size bằng `stat` sau khi process thành công.
8. Đo decompression tương tự.
9. Tính SHA-256 restored và so input.
10. Xóa/move output sau khi metadata cần thiết đã được thu, không lấy cached
    result từ repetition trước.

Warm-up phải được ghi rõ. OS page cache thường không thể drop an toàn trong
container không đặc quyền; policy mặc định là **warm-cache, consistent for all
codecs**. Nếu đo cold-cache, dùng một suite riêng, cơ chế hợp lệ và báo rõ;
không drop cache cho một codec nhưng không cho codec khác.

Timer dùng monotonic wall clock. Subprocess setup có thể bao gồm vì mọi CLI đều
chịu cùng protocol; với file rất nhỏ, báo riêng overhead-dominated và không
diễn giải throughput như steady state.

MathZip production mặc định `fsync` output atomic. Benchmark wrapper truyền
`--no-sync`, nên MathZip và baseline đều kết thúc sau khi đóng output vào page
cache, không tính durable flush riêng cho chỉ một codec.

Phase timing MathZip có hai protocol được ghi explicit trong methodology và
run identity:

- `probe` (mặc định tương thích artifact cũ): chạy một compression instrumented
  riêng sau phép đo wall-time. Probe không được cộng vào speed comparison, phải
  sinh archive SHA-256 giống archive measured, và command/sidecar vẫn nằm trong
  `trials[]` để audit.
- `inline`: chính compression được timing nhận đúng một `--metrics-output` và
  đồng thời sinh archive cùng sidecar. `compression_metrics_command` phải bằng
  `compression_command`; serialization/write sidecar nằm trong compression
  wall time. Không có lần nén probe thứ hai.

Full publication, corrected ablation và residual-ablation hiện chọn `inline`.
Strict validator yêu
cầu cặp field `mathzip_metrics_mode`/
`mathzip_metrics_in_compression_timing` nhất quán, kiểm đúng quan hệ command,
và vẫn đọc các artifact legacy chưa có hai field này. Sidecar mới còn ghi
provenance của search portfolio (requested mode, effective
segmentation/anchor, finalist limits và Balanced frontier của Max).

Timeout là per phase và nằm trong config. Khi timeout:

- terminate process theo grace period hữu hạn;
- ghi `timeout_compress` hoặc `timeout_decompress`;
- giữ elapsed time tới timeout;
- không điền compressed size nếu archive chưa hoàn tất;
- không im lặng loại sample.

## 8. Single-thread và multi-thread

Suite bắt buộc có single-thread khi codec hỗ trợ cấu hình thread. Dùng affinity
pin một logical CPU nếu có thể và ghi lại việc pin thành công/thất bại.

Multi-thread suite dùng cùng số thread \(t\) cho codec hỗ trợ, với
\(t\in\{2,4,\text{physical cores}\}\) tùy config. Codec không hỗ trợ thread
được ghi `threads=1`, không giả vờ dùng \(t\). Không gộp single- và multi-thread
vào cùng một trung bình.

## 9. Metrics

Với original bytes \(O\), compressed bytes \(C\), wall seconds \(t\):

\[
\text{ratio}=O/C,
\qquad
\text{savings}=1-C/O,
\qquad
\text{bits per byte}=8C/O,
\]

\[
\text{throughput MB/s}=\frac{O}{10^6t}.
\]

MB/s dùng MB thập phân. Peak RSS báo byte và MiB \((2^{20})\). Với file rỗng,
ratio/savings/bits-per-byte là `null`, không chia 0; vẫn báo compressed bytes và
round-trip.

Expansion:

\[
\text{expansion}=C/O-1
\]

cho \(O>0\).

MathZip-specific:

- selected transform chain;
- segment count, mean/median segment length;
- model/residual/mode distribution theo count và decoded bytes;
- `container_overhead_bytes` = header + footer + transform descriptors;
- `partition_metadata_bytes` = 36-byte descriptors của mọi segment;
- model parameter bytes và actual residual payload bytes;
- raw-model decoded bytes/total bytes và Raw-model-plus-Raw-residual fallback
  decoded bytes/total bytes, báo thành hai metric khác nhau;
- Shannon entropy \(H=-\sum_b p_b\log_2p_b\) của toàn residual đã decode,
  đơn vị bit/residual-byte, cùng actual coded residual bytes;
- search, fitting và residual coding time;
- số non-Raw segment mà `model params + residual payload` nhỏ hơn Raw residual
  body cùng length;
- số non-Raw segment mà cùng body đó nhỏ hơn Zstd level-3 frame của transformed
  bytes thuộc segment;
- determinism/checksum failures.

Descriptor chung không tham gia hai “math thắng” probe. Storage breakdown MUST
thỏa:

```text
compressed_bytes =
    container_overhead_bytes
  + partition_metadata_bytes
  + model_parameter_bytes
  + actual_residual_coded_bytes
```

`metadata_bytes` nếu có bằng hai thành phần metadata đầu. Nếu implementation
chưa instrument field nào, ghi `null` + `metric_unavailable`, không ước lượng.

## 10. Aggregate

Cho một corpus có files \(i\):

\[
O_\Sigma=\sum_i O_i,\quad C_\Sigma=\sum_i C_i,
\quad \text{weighted ratio}=O_\Sigma/C_\Sigma.
\]

Đây là aggregate chính. Ngoài ra báo:

- arithmetic mean per-file savings;
- geometric mean của \(C_i/O_i\) cho file không rỗng, rồi có thể đảo thành
  ratio;
- median per-file bits/byte;
- tổng wall time và throughput từ tổng bytes/tổng time;
- số success/failure/timeout/unavailable.

Không dùng trung bình trực tiếp của ratio làm headline vì file nhỏ có thể có
trọng số quá lớn. Stream gộp là workload khác với tổng từng archive: báo riêng,
không thay weighted aggregate.

Median repetition là statistic chính cho time và RSS. Compressed size của
encoder deterministic phải giống nhau ở mọi repetition; nếu khác, sample có
`determinism_failure=true`, lưu mọi size/hash và không chọn size đẹp nhất.

## 11. Result schema

Normative machine schema của implementation nằm tại
[`python/benchmark_result.schema.json`](../python/benchmark_result.schema.json).
Raw JSON là một suite object chứa `results`; CSV là projection phẳng của mỗi
aggregate row, còn JSON giữ `trials[]` để không mất từng repetition.

Các nhóm field chính:

```text
schema_version, run_id, profile, status
started_at_utc, completed_at_utc, elapsed_seconds
source.{source_revision,source_dirty,source_tree_sha256,source_tree_manifest}
system.{os,kernel,architecture,cpu_model,ram_bytes,swap_bytes,storage,...}
methodology, config.{path,sha256,snapshot}, codec_definitions
expected_grid.{inputs,codecs,expected_result_count,sha256}
resume.{resume_count,active_elapsed_seconds,execution_intervals}

corpus, input_path, input_name, input_sha256
input_license, input_provenance, input_manifest,
input_manifest_sha256, input_manifest_verified
input_entropy_bits_per_byte, original_bytes
codec, codec_family, codec_level, variant, threads
status, error, successful_repeats, requested_repeats
compressed_bytes, archive_sha256, restored_sha256
roundtrip_verified, deterministic_archive
compression_seconds, decompression_seconds
compression_cpu_seconds, decompression_cpu_seconds, peak_rss_bytes
ratio, savings, bits_per_byte, expansion_percent
transform, segment_count, model_distribution, residual_distribution
model_parameter_bytes, partition_metadata_bytes, residual_bytes
container_overhead_bytes, raw_model_percent, raw_fallback_percent
estimated_residual_entropy, actual_residual_coded_bytes
math_segments_winning_raw, math_segments_winning_zstd
search_seconds, model_fitting_seconds, residual_coding_seconds
trials[].{index,status,commands,times,RSS,hashes,stderr}
```

Command trong `trials[]` phải là argv array; CSV có projection quoted để đọc
thuận tiện. Path công bố được rút gọn thành repository-relative hoặc
`<external>/<tmp>/<work>`; không lưu secret hay absolute home path.

Top-level status là `complete` hoặc `complete_with_failures`. Aggregate row
dùng `ok`, `missing_input`, `unavailable_codec`, `warmup_failed`,
`partial_failure`, `determinism_failure`, hoặc status lỗi được truyền từ trial.
Trial status hiện gồm:

```text
ok
error
compression_timeout
compression_failed
compression_metrics_failed
decompression_timeout
decompression_failed
checksum_mismatch
inspection_failed
```

Metric không thu được là `null`; lý do environment-level nằm trong
`system.metric_unavailable`, không giả thành số 0. Harness v0.1 ghi
`missing_input` khi input bắt buộc không được chuẩn bị; nó chưa có
`skipped_resource_limit`, nên một grid như vậy không được trình bày là Full
hoàn tất.

CLI production dùng giới hạn mặc định thận trọng: compress nhận tối đa 512 MiB
input; decompress/inspect/verify nhận tối đa 256 MiB archive và 128 MiB output.
Harness benchmark truyền explicit 8 GiB input/output, 16 GiB archive và
8.388.608 segment để chạy corpus lớn đã kiểm checksum. Các override này là
quyết định trust/resource của protocol benchmark, phải hiện trong argv của
`trials[]`; chúng không thay default an toàn của CLI.

CSV/JSON validator phải kiểm tra:

- field bắt buộc và type;
- metrics không âm;
- mọi field CSV phải khớp projection canonical của JSON;
- mọi input phải khớp size/SHA-256 trong manifest;
- build-info MathZip phải khớp clean source revision;
- output hash chỉ có khi output tồn tại;
- `status=ok` kéo theo `roundtrip_verified=true`;
- breakdown MathZip cộng đúng archive size khi tất cả field có mặt;
- không có duplicate sample key;
- với artifact mới, tích Descartes chính xác của
  `expected_grid.inputs × expected_grid.codecs` phải bằng tập key
  `(corpus,input_path,codec,threads)` của results: thiếu, dư hay trùng đều là
  validation error. Artifact v1 cũ không có `expected_grid` vẫn được validate
  theo các invariant trước đây.

### 11.1. Checkpoint và resume cho run dài

Mỗi aggregate row chỉ được checkpoint sau khi toàn bộ warm-up và measured
repetition của row đó đã xong. Runner ghi nguyên tử một envelope riêng trong
`checkpoint-rows/`, rồi cập nhật nguyên tử `checkpoint.json`; các thao tác này
nằm ngoài timed region. Vì row đang chạy chưa có envelope, nếu process bị dừng
thì resume chạy lại nguyên warm-up và mọi repetition của row đó.
Nếu crash xảy ra đúng giữa hai rename, durable row envelope là nguồn sự thật:
progress count cũ hơn được nâng lên khi resume; count tuyên bố lớn hơn số
envelope durable bị từ chối thay vì suy đoán.

Một run mới còn lưu hai snapshot bất biến:

- `expected-grid.json`: input evidence, codec/thread keys, expected count và
  content SHA-256;
- `run-identity.json`: config SHA/snapshot, clean source revision/tree hash,
  executable hash và MathZip build-info, input-grid hash, host identity
  (hostname được băm), CPU/core/RAM/OS, cùng warm-up/repeat/timeout/thread
  policy.

`--resume RUN_DIRECTORY` hoặc `--resume RUN_DIRECTORY/checkpoint.json` fail
closed nếu bất kỳ identity nào khác, nếu checkpoint hỏng, hoặc nếu source cũ/
hiện tại không phải clean revision (hay exact tree identity trên môi trường
không có Git HEAD). `run_id` và `started_at_utc` ban đầu được giữ nguyên;
`execution_intervals`, `resume_count`, wall elapsed và active elapsed được báo
riêng để thời gian máy dừng không bị giả thành thời gian benchmark hoạt động.
Active elapsed chỉ cộng phần invocation đã checkpoint; thời gian của row bị kill
trước checkpoint là lower bound và row được chạy lại. Runner giữ exclusive lock
theo canonical output root trong suốt invocation để từ chối concurrent
resume/publication.

`results.json`, `results.csv` và `latest.*` chỉ được ghi/cập nhật sau khi tập
row durable khớp chính xác toàn bộ expected grid. Failure/timeout row vẫn là
một row hoàn tất và không bị giấu; “grid complete” không đồng nghĩa
“zero-failure”.

## 12. Báo cáo và biểu đồ

Plot được sinh chỉ từ validated raw results:

- ratio/bits-per-byte theo file và corpus;
- compression/decompression throughput;
- ratio-speed và ratio-memory Pareto frontier;
- metadata/model/residual breakdown;
- model distribution và segment histogram;
- residual entropy so actual size;
- ablation;
- noise density so ratio;
- input entropy so MathZip gain;
- gain/loss so Zstd và XZ.

Mọi plot có unit, profile, thread count, error indication và số sample. Trục
log phải được ghi rõ. Failed row không được biến thành 0 hoặc bỏ khỏi
denominator; bảng kèm phải có failure count.

Full công bố 15 PNG vì không chứa variant ablation trong result document.
Danh mục biểu đồ bắt buộc được hoàn tất bằng
`benchmarks/plots/ablation/ablation_comparison.png` từ corrected ablation;
hai manifest cùng bind hash về raw result tương ứng.

Riêng `ablation_comparison.png` dùng median ratio của các row thành công để
hiển thị variant điển hình. Nó không thay weighted corpus/whole-matrix ratio.
Mọi kết luận byte-weighted (đặc biệt V13/V14 và Balanced/Max, nơi file
`kennedy.xls` chi phối tổng byte) phải tính lại từ `original_bytes` và
`compressed_bytes` trong artifact, đồng thời báo paired win/tie/loss.

## 13. Điều kiện để phát biểu kết luận

Một claim như “MathZip thắng Zstd trên workload W” chỉ hợp lệ nếu:

- W và selection rule được khai báo trước, không chọn sau khi xem kết quả;
- output thật và round-trip SHA-256 đều hợp lệ;
- cùng machine/thread class và cấu hình được nêu;
- cả size và speed/memory trade-off được trình bày;
- có per-file data, weighted aggregate và failures;
- raw data + config + source revision có thể tái chạy.

Synthetic results chỉ chứng minh codec nhận ra family đã thiết kế; không chứng
minh khả năng nén file tổng dụng. Nếu chưa có run hợp lệ, research report phải
ghi “chưa có bằng chứng”, không điền số dự kiến.

# Trạng thái triển khai MathZip

Tài liệu này tách ba việc thường bị nhập nhằng: source đã có, kiểm tra
correctness đã chạy trên đúng snapshot, và benchmark comparative đã có
artifact. Một enum, config hoặc test chưa chạy không tự trở thành evidence.

## 1. Quy ước

| Ký hiệu | Nghĩa |
|---|---|
| ✅ | Đã có source và evidence nêu rõ đã chạy |
| 🟡 | Source/test đã có nhưng strict revalidation hoặc end-to-end artifact còn chờ |
| 📋 | Đã có đặc tả/harness/config, chưa có kết quả thực nghiệm |
| ⛔ | Chưa triển khai hoặc chủ ý deferred |
| ❌ | Đã chạy và thất bại |

## 2. Audit hiện tại và snapshot benchmark đã công bố

| Field | Giá trị |
|---|---|
| Ngày audit tài liệu | 2026-07-28 |
| Implementation revision đã clean-validate + dùng cho Quick hiện tại | `6dbc3860910e8d310cfa8bbeadbf97f0529beff1` |
| Source-tree SHA-256 của Quick hiện tại | `91797df21983656b4d8020958e3619a646f46dfe86a4560d854e22483838c196` |
| Release MathZip SHA-256 của Quick hiện tại | `6568f05094d05eac86a12ee608bc10c34c268080a25636bbaa8b9b2f59f63549` |
| Focused Silesia revision/tree/binary | `0e3bd26e9883b7b6d6ed51def05b1822efbda6bf` / `dd883471e0a661461fd56cf892f01dfbc90a8ec2e9d192d87f07b8e0b5e4cfad` / `485c9cdd7533e34d1a18592bd6652d4ba590b62e6188e0a1a63558614152d154` |
| Focused Bit-plane revision/tree/binary | `1703b73f3ef51753ed1f92ea5e2ce28da9bcdf22` / `612a5cf7d57a2607bfc8c297bf9bf08214ac25e065ea4d9a08ee881ddac733ac` / `237c4515d958afcc373078badbec902e7736a3582891b734fd4ba555a326094f` |
| Focused Segmentation revision/tree/binary | `3c864d915b0d2452da712cd691796a4bdf987876` / `9c9501edf2a575addc6f45ae5b5fce31352031a79d412d6993674c994d83991d` / `1b5e433a5321b00ecd7969bf1259db52637b8fee8abddb6e379123fe86bd37f2` |
| Git Copy ablation revision/tree/binary | `163fc78cbadc0fdb5888781080baade7b576811d` / `6397e3d01b12d567f54c378f036e785ee107916f8d9ab873b13f41a1031e0fd2` / `c4cc0e9b8a8469f70e5c9d594b127f5bf2eb6ad6a402388bb4595772197c2437` |
| Residual ablation revision/tree/binary | `a130e30389cad1efe6db3ffad7764022a446531e` / `6e0ac49416c7a7d9db4d492df6fa3a125c8ea1556fd7f053580499a9cc85e7dd` / `a2818ffa822338c2ffa00159b15739a0618417aa8a74a417e3656758181d75d6` |
| Recursive ablation revision/tree/binary | `aac547e57647b0135c555c0b59a5b0c01db476ea` / `0164eab3f424d99e9976ecfe9b1f5ceeff2c8fea88b62624c7b4adb3ad8bf33f` / `8a40009eafaac4ae63a396de1751d4a4f8519130b66aba49f1a7fb1e427c5559` |
| Corrected ablation revision/tree/binary | `e5366fa7c5c1cd5801bb9cc86b19a09592403137` / `e3813b1523ee29c09f8b60b9d7a06844219e21c20114e3a3ce5762ae75b5323a` / `728494e5c0d1ba0aad42a039b056d26372cd60b9a744c0a4ad1744552084cd30` |
| Full revision/tree/binary | `e60f423b5f85d1716eb2356f2460f97ba4227885` / `55b6a32cc5abedcdcba8989065be83cbaa8600bede102e73c39306bb58153e63` / `366ca45932751ecb10d52f4db3f4806b94acd44e3ec9ccbf45e33b45bd82e228` |
| Legacy enwik8/ablation codec revision | `235d190a20e64b6ddc5ce002379809ebbe8fe0cd` (clean) |
| Toolchain audit gần nhất | rustc/cargo 1.97.1; Python 3.12.3 |
| Rust test inventory | 61 unit/binary + 18 integration = 79; pass |
| Python test inventory | 55/55 pass, gồm release-binary smoke trên clean revision |
| Static/build verification | fmt, check, Clippy `-D warnings`, fuzz build, compile/shell/schema/manifest/link checks pass |
| Benchmark comparative | Full 24.650/24.650, Quick 477/477, Silesia 108/108, enwik8 9/9, Git Copy 45/45, residual 36/36, Recursive 30/30, Bit-plane 42/42, Segmentation 90/90 và corrected ablation 252/252 strict pass; historical ablation giữ 2 failure |

Mỗi benchmark bên dưới định danh chính xác revision riêng; không được dùng thay
đổi tài liệu/verifier để diễn giải như benchmark lại codec. Quick hiện tại,
Silesia, Git Copy, residual, Recursive, Bit-plane, Segmentation, corrected
ablation và Full có exact-grid/checkpoint identity; enwik8 và historical
ablation là legacy-v1 artifact trước schema đó. Full là artifact độc lập, không
được dùng để hồi tố hay viết lại các focused run.

## 3. Core, CLI và giới hạn tài nguyên

| Thành phần | Trạng thái | Source hiện có |
|---|---|---|
| `compress`/`decompress`/`inspect`/`verify` | ✅ | API, CLI và round-trip tests |
| stdin/stdout và overwrite safety | ✅ | file/pipe path và `--force` |
| arbitrary bytes + whole-file Raw fallback | ✅ | fallback được so bằng archive size thật |
| checksum | ✅ | header/segment CRC-32, archive/original SHA-256 |
| bounded untrusted decode | ✅ | checked sizes/counts, canonical parsing và aggregate work cap |
| benchmark CLI bridge | ✅ | Python harness có E2E test |

Giới hạn mặc định hiện tại:

| Surface | Mặc định |
|---|---:|
| CLI compress input | 512 MiB |
| archive cho decompress/inspect/verify | 256 MiB |
| restored output | 128 MiB |
| segment | 65,536 |
| transform descriptors | 16 |
| model parameters | 64 MiB |
| period / recurrence order / model entries | 1,048,576 / 16 / 65,536 |

Decoder còn tính
`transformed_size * sum(transform_weight)` bằng checked arithmetic và giới hạn
ở tám lần output limit tương ứng (v2 dùng transformed limit đã round-up);
Identity/Delta/XOR/Stride có weight 1, cả packed và padded BitPlane có weight
8. Model work có cap riêng bằng `8 * transformed_size`, Polynomial tính theo
coefficient và Recurrence theo order. Encoder giới hạn Recurrence order 8;
decoder có limit 16.
Benchmark harness chỉ tăng limit một cách explicit cho corpus đã verify:
input/output 8 GiB, archive 16 GiB và segment 8,388,608. Các override phải nằm
trong argv của raw result, không thay default CLI.

## 4. Format v1 và profile v2 hẹp

Normative layout: [format-spec.md](format-spec.md).

| Hạng mục | Trạng thái source |
|---|---|
| `MZIP` v1, fixed 80-byte header | ✅ |
| `MZIP` v2, cùng header/framing, sole padded BitPlane ID 5 | ✅ source + clean tests + focused artifact |
| Transform descriptors length-delimited | ✅ |
| 36-byte segment prefix + explicit parameter/residual lengths | ✅ |
| `MZFT` + archive SHA-256 footer | ✅ |
| Little-endian fixed integers + canonical bounded uLEB128 | ✅ |
| Exact payload consumption; unknown version/flag/ID rejected | ✅ |
| Optional random-access index | ⛔ |
| Streaming format | ⛔ |
| Authentication/signature/MAC | ⛔, ngoài scope v1 |

Mọi transform v1 giữ nguyên byte length; ID 3 packed BitPlane và golden v1
không đổi. V2 hiện chỉ nhận đúng một ID 5, với transformed size
`8 * ceil(original_size / 8)` và canonical high padding bits bằng 0.
Parser/decoder nhận bounded chain ở v1; encoder đánh giá đúng một transform
descriptor cho mỗi whole-archive candidate. Whole-file Raw fallback vẫn là
Identity v1.

## 5. Transform và prediction model

| Registry | Thành phần có source |
|---|---|
| Transform IDs 0..4 | Identity, Delta modulo 256, previous-byte XOR, packed BitPlane, Stride 2/3/4/8/16/32/64 |
| Transform ID 5 (v2) | Padded BitPlane: tám plane byte-aligned LSB-first, zero high padding, tối ưu/phân đoạn từng plane riêng |
| Model IDs 0..9 | Raw, Constant, Affine, Polynomial, Periodic, Recurrence, PiecewiseLinear, Run, Sparse, Copy |

IDs 0..5 và model IDs đã qua unit/integration/property test ở clean snapshot.
ID 5 có cases cho layout, padding, odd size, version gate, plane-boundary
partition và round-trip, cùng focused comparative artifact 42/42 row.
Polynomial dùng Newton forward difference degree 2..4;
Recurrence dùng actual/restored-prior feedback; Copy chỉ dùng backward
reference không chồng lấn.

Copy search encoder đã được thay từ quét byte-by-byte thành index deterministic
`BTreeMap<u32, Vec<boundary_start>>`, khóa bởi bốn byte đầu little-endian. Nó
chỉ xét source tại candidate boundary, trong cửa sổ 1 MiB, theo thứ tự gần nhất
và tối đa 16 candidate hợp lệ. Semantics decode của Copy không đổi.

LFSR bit model, fixed-point harmonic và neural INR không có model ID v1/v2.

## 6. Residual

| ID | Coder | Trạng thái source |
|---:|---|---|
| 0 | Raw | ✅ |
| 1 | RLE | ✅ |
| 2 | ZeroRun | ✅ |
| 3 | Sparse non-zero | ✅ |
| 4 | BitPack LSB-first | ✅ |
| 5 | Đúng một Zstd frame độc lập | ✅ |

AddModulo và XOR là hai residual mode. Zstd residual dùng encoder level 3,
decoder bounded theo exact segment length, và **tắt mặc định** trong Fast,
Balanced và Max. Nó chỉ được bật explicit, gồm variant
`ablation-14-zstd-residual`. Direct `zstd-default` vẫn là control riêng.
Huffman, arithmetic/rANS, Rice/Golomb và ZigZag variable-byte còn deferred.

## 7. Segmentation và optimizer

| Hạng mục | Trạng thái source |
|---|---|
| Fixed boundaries | ✅ |
| Change-point: entropy, histogram, mean/variance, autocorrelation, periodicity, compression probe, bit density, prediction residual | ✅ source + deterministic unit tests |
| Adaptive = fixed anchors + change points | ✅ |
| Actual serialized-size edge cost | ✅ |
| Bounded-lookback dynamic programming | ✅ |
| Recursive leaf-count DP, depth ≤16, strict bounds | ✅ source/tests + focused 30-row comparative artifact |
| Whole-file Raw comparison | ✅ |
| Recursive feasibility/Pareto pruning + 262K-boundary/2M-state caps | ✅ |
| Shared AddModulo/XOR prediction + bounded exact top-k period probe | ✅ source/tests |
| ≥1 MiB standard Balanced/Max screened portfolio | ✅ Adaptive retained; B/M anchors 64/256 KiB, transform 3/5, model-mode 2/3, exact Raw, Max includes Balanced frontier; proxy provenance in sidecar |
| Beam search / encoded-cost lower-bound / early stopping | ⛔ |
| Decode-time/memory weighted objective \(J\) | ⛔ |

Fixed mode nối đúng các anchor liên tiếp và không áp minimum/maximum segment
size của profile; do đó fixed block nhỏ hơn profile minimum và block cuối ngắn
không còn bị loại. ChangePoint/Adaptive dùng tám nhóm feature Q8/integer trên
hai cửa sổ bị chặn ở 4 KiB, threshold cố định và bước quét bị chặn; chúng không
đặt candidate ở mọi byte. Recursive áp bounds nghiêm ngặt, trừ whole input ngắn
hơn minimum. Recursive topology không được serialize: mọi leaf partition tối
đa \(2^d\) được biểu diễn bằng flat segment list và có thể gán một cây cân bằng
depth tối đa \(d\). Với ID 5, mỗi plane sở hữu một lần
segmentation/optimizer/model search riêng và segment do encoder phát ra không
vượt logical plane boundary. Whole-file Raw fallback toàn cục vẫn miễn bounds;
vượt hard cap làm searched transform nhường cho fallback thay vì OOM.

## 8. Inspect và metric

`inspect` hiện reconstruct đầy đủ, kiểm checksum và có source cho:

- transform/segment counts, mean/median length và distributions;
- `raw_model_percentage`: mọi decoded byte dùng Raw predictor;
- `raw_fallback_percentage`: chỉ Raw predictor + Raw residual;
- `container_overhead_bytes`: header + footer + transform descriptors;
- `partition_metadata_bytes`: segment descriptors;
- model parameter và actual residual coded bytes;
- Shannon entropy của decoded residual stream, bit/residual-byte;
- số non-Raw segment có model-body nhỏ hơn Raw body hoặc Zstd level-3 probe.

Invariant storage:

```text
compressed_size =
    container_overhead_bytes
  + partition_metadata_bytes
  + model_parameter_bytes
  + actual_residual_coded_bytes
```

Encoder trả phase timing không chồng lấn cho search/optimizer, model fitting và
residual coding bằng sidecar JSON ngoài archive. Protocol `probe` legacy chạy
compression instrumented riêng ngoài wall time và bắt archive SHA-256 khớp.
Protocol `inline` của Full/ablation/residual dùng chính timed compression, nên
sidecar work được tính vào speed và không có lần nén thứ hai. Timing không được
serialize nên không làm mất determinism; strict validator kiểm protocol,
command provenance, đủ ba phase mỗi repetition và median aggregate. Sidecar
mới còn ghi effective screened/exhaustive search policy.

## 9. Tests, fuzzing và robustness

| Evidence | Trạng thái |
|---|---|
| Rust 61 unit/binary + 18 integration = 79 tests | ✅ pass |
| Python 55 tests | ✅ pass; environment-dependent skips được báo explicit |
| Property/corruption/limits cases | ✅ pass |
| Golden v1/v2 canonical archive | ✅ 160-byte v1 và 416-byte v2 fixture hash đã pin và pass |
| Three cargo-fuzz targets | ✅ recorded campaigns, không crash/timeout |
| Cross-architecture golden vectors | ⛔ |
| Sanitizer/Miri audit | ⛔ |

Recorded fuzz campaign, không crash/timeout:

| Target | Executions | Edge coverage | Feature coverage |
|---|---:|---:|---:|
| decode | 1,305,168 | 441 | 519 |
| inspect | 1,288,144 | 534 | 612 |
| roundtrip | 4,529 | 1,702 | 4,277 |

Chi tiết toolchain, command và giới hạn của evidence này:
[fuzzing.md](fuzzing.md). Đây là bounded campaign evidence, không chứng minh
parser không có bug.

Golden v1 dùng input `00010203`, Fast/Fixed-4/Identity/Raw+Raw/no-fallback và
pin archive SHA-256
`ef495ec6d95d329304d8481376d4ca2a263c9470107dd0f3c8d8392cf01f2c88`;
golden v2 dùng cùng input với padded BitPlane ID 5 và pin SHA-256
`8fd085925a7747c6668afc1ab359e26c2b1a8cf563eae59a4ff737e495d40312`.
Chi tiết nằm trong
[format-spec.md](format-spec.md#14-test-vectors-cần-duy-trì).

## 10. Dataset và benchmark framework

| Thành phần | Trạng thái |
|---|---|
| Full synthetic matrix | ✅ 644 files đã generate và verify local, gồm 42 encrypted entry |
| Encrypted synthetic v2 | ✅ plaintext telemetry có cấu trúc được mã hóa ChaCha20 RFC 8439; 2 RFC vector + decrypt/determinism test pass; Quick và Full artifact đã đo |
| Mixed + Git-version auxiliary corpora | ✅ đã generate và verify local |
| Canterbury, Calgary, Silesia | ✅ downloaded/extracted/verified local |
| enwik8, enwik9 | ✅ downloaded/extracted/verified local |
| Pizza & Chili `world_leaders` | ✅ downloaded/extracted/verified local |
| Safe checksummed downloader | ✅ tests và local manifest verification pass |
| Baseline wrappers | ✅ gzip/bzip2/XZ/Zstd/LZ4/Brotli/7z và Raw đã chạy trong Quick |
| Published Quick artifact/config snapshot | ✅ 53 input × 9 codec = 477/477 rows, strict exact-grid pass; gồm một encrypted input |
| Historical Quick snapshot | ✅ immutable 52 × 9 = 468/468 legacy-v1 artifact, có trước encrypted v2 |
| Full config/artifact | ✅ `20260726T044843Z-213f07c3`: 725 input (1.468 GB) × 34 codec/thread = 24.650/24.650 row `ok`; 73.950 measured trial, inline metrics, 0 failure/resume, 129.095,967 s, strict exact-grid + 15 Full plot pass |
| Focused toàn bộ Silesia | ✅ 12 input × 9 codec = 108/108 row, strict exact-grid pass; 324 measured trial, 0 failure |
| focused `configs/enwik8.yaml` + script | ✅ 9/9 rows, strict pass, 0 warning |
| Corrected 20-variant ablation + Zstd control | ✅ `20260725T170925Z-dde3921f`: 12 × 21 = 252/252 row `ok`, 756 measured trial, inline metrics, strict exact-grid/plot pass, 0 warning; V13/V14/V17 là single-factor |
| Historical 20-variant ablation | ✅ immutable `20260724T032334Z-aca5bed4`: 252 row được ghi, 250 thành công, 2 Max warm-up timeout; V13/V14/V17 có causal confound và không dùng để attribution |
| Paired custom/Zstd residual trên ablation corpus | ✅ 12 input × 3 codec = 36/36 row, strict exact-grid pass; Zstd residual giảm 69,2046% so custom và hybrid nhỏ hơn direct Zstd 1,8965% nhưng chậm hơn khoảng 2.552,6 lần |
| Paired Copy on/off trên Git snapshots | ✅ 15 input × 3 codec = 45/45 row, strict exact-grid pass; Copy giảm 40/90.843 B (0,0440%) trên đúng 1 input |
| Paired Adaptive/Recursive depth 4 | ✅ 10 input × 3 codec = 30/30 row, strict exact-grid pass; Recursive giảm 240/266.151 B (0,0902%) nhưng chậm hơn 1,90× |
| Focused packed-v1 / independent-v2 Bit-plane | ✅ 14 input × 3 codec = 42/42 row; 126 measured trial, strict pass, 0 warning |
| Fixed-size sweep + ChangePoint/Adaptive | ✅ 10 input × 9 codec = 90/90 row; đủ 256 B, 1/4/16/64/256 KiB; 270 measured trial, strict pass |
| Max timeout regression | ✅ 2 input × 2 codec = 4/4 row; 12 measured trial, strict exact-grid pass; current source hoàn thành cả hai MathZip Max row dưới 600 s |
| JSON/CSV validator, plots và report generator | ✅ tests pass; Full report + 15 PNG và corrected ablation report + 15 PNG có hash manifest, strict pass; hai bundle phủ cả `ablation_comparison` và gain-vs-XZ |
| Comparative Full/Quick/Silesia/enwik8/corrected+historical ablation/Git Copy/residual/Recursive/Bit-plane/Segmentation/Max-regression results | ✅ artifact công bố |

Quick dùng Canterbury, Calgary, bốn file Silesia và synthetic. Focused Silesia
đo đủ 12 file với chín codec Fast: MathZip nén 211.938.580 B xuống
187.537.684 B (ratio 1,130; 1,567 MB/s; peak RSS lớn nhất 328,750 MiB), trong
khi Zstd dùng 73.448.492 B (2,886) và XZ dùng 58.417.824 B (3,628); MathZip
thua cả hai trên 12/12 file. Focused enwik8 dùng payload 100 MB. Ablation dùng
4 file Canterbury và 8 file synthetic. Git Copy ablation đo 15 file
individual/combined từ ba snapshot tự sinh. Full đã đo enwik8, enwik9,
Pizza & Chili `world_leaders`, mixed generated, 15 Git
snapshot/combined input, toàn bộ Silesia và synthetic matrix trong cùng exact
grid.

Corrected ablation chạy đủ 252/252 row trong 9.327,963 s với một warm-up, ba
repetition và inline metrics; strict exact-grid/checkpoint cùng 15 PNG đều pass,
0 warning. V13→V14 giảm 1.044.616 xuống 324.348 B (68,9505%) và aggregate
median compression time giảm 279,086 xuống 251,921 s (9,7336%), nhưng hybrid
chỉ nhỏ hơn direct Zstd 1,2017% và chậm hơn khoảng 2.285×. Theo file V14 thắng
5, hòa 1, thua 6, nên weighted gain không đồng nghĩa majority win. V17 chỉ tắt
whole-file fallback: 11 cặp hòa và random lớn hơn V13 36 B.

V13 và V19 Balanced có archive/hash/inspect giống nhau trên 12/12 input. Max
giảm 25,6340% tổng byte so Balanced nhưng dùng 2,583× aggregate median
compression time; W/T/L kích thước là 3/1/8 và nếu bỏ `kennedy.xls` thì Max
lớn hơn Balanced 1.761 B. Đây là trade-off profile chịu ảnh hưởng gần như toàn
bộ bởi một file lớn, không phải single-factor hay bằng chứng Full.

Focused Bit-plane dùng chín input 64 KiB và năm input 31 B. Packed v1 dùng
148.473 B (ratio 3,974; 0,093 MB/s), independent v2 dùng 152.803 B (3,861;
0,800 MB/s), còn Zstd dùng 81.901 B (7,204; 8,014 MB/s). Independent v2 lớn
hơn packed 4.330 B (2,916%), với 0 thắng/2 hòa/12 thua về kích thước, dù
throughput nén cao hơn khoảng 8,60 lần. Đây là evidence cho correctness và
trade-off của implementation trên matrix hẹp, không phải claim compression
gain.

Focused Segmentation dùng cùng 714.617 B từ tám input synthetic 64 KiB và hai
file Canterbury. ChangePoint 4 KiB dùng 262.012 B (ratio 2,727; 0,913 MB/s),
Adaptive 4 KiB dùng 264.305 B (2,704; 0,548 MB/s), Fixed 4 KiB dùng 271.155 B
(2,635; 1,556 MB/s), còn Zstd Fast dùng 165.733 B (4,312). ChangePoint thắng
Adaptive 8, hòa 1, thua 1 và không thua best Fixed size trên bất kỳ input nào
trong matrix hẹp này.

Focused Max regression dùng đúng hai file Canterbury từng timeout trong
ablation lịch sử. Current source dùng 430.446 B cho `kennedy.xls` với median
368,526 s và 100.522 B cho `ptt5` với median 158,502 s; cả hai thấp hơn limit
600 s. Aggregate MathZip là 530.968 B (ratio 2,906; 0,003 MB/s), còn Zstd Max
là 113.190 B (13,632; 1,306 MB/s). Artifact cũ vẫn giữ hai timeout; regression
mới không tạo Full frontier hay thay đổi history.

## 11. Đối chiếu tiêu chí dự án

| Tiêu chí | Trạng thái trung thực |
|---|---|
| File nhị phân arbitrary + Raw fallback | ✅ |
| Adaptive segmentation | ✅ bounded-DP Adaptive và bounded-depth Recursive đều có source + comparative evidence |
| Ít nhất năm model toán học | ✅ 10 model ID |
| Bit-plane | ✅ packed ID 3/v1 và padded ID 5/v2 có source, clean tests và focused evidence; mỗi plane được phân đoạn/model riêng; LFSR bit predictor vẫn deferred advanced model |
| Residual coding | ✅ năm custom coder + Zstd hybrid explicit |
| Actual encoded-size MDL trên candidate/finalist được giữ | ✅; large portfolio prune bằng proxy gần đúng, không claim global optimum |
| Container format rõ và bounded decode | ✅ |
| Mọi measured successful benchmark trial round-trip SHA-256 | ✅ 79.383/79.383 trial thành công trên toàn bộ 13 result document được track: 77.979 trong bộ Full + 11 artifact current/focused và 1.404 trong Quick lịch sử |
| Canterbury/Calgary/toàn bộ Silesia/enwik8/enwik9/Pizza/Mixed/Git results | ✅ có Full artifact comparative; Silesia đủ 12/12 file |
| Baseline comparative | ✅ Full cùng các focused Quick/Silesia/enwik8 |
| Ablation | ✅ corrected đủ 20 variant + control, 252/252 success; historical 2/252 Max timeout và causal confound vẫn được báo |
| CSV/JSON publication artifact | ✅ Full, Quick, Silesia, enwik8, corrected+historical ablation, Git Copy, residual, Recursive, Bit-plane, Segmentation, Max regression |
| Full benchmark artifact | ✅ raw JSON/CSV + 24.650 checkpoint row + report + 15 PNG đã commit và strict-verify |
| Báo cáo không vượt evidence | ✅ kết quả âm và timeout được giữ |

## 12. Deferred và bước còn lại

- Format v1/v2 còn experimental; v1 ID 3 và golden fixture được giữ tương thích
  trong thay đổi v2 này nhưng chưa có cam kết archival dài hạn.
- Streaming/random access, transform composition search, overlapping Copy,
  harmonic/LFSR-bit, modular-function model và arithmetic/rANS còn deferred.
- Recursive leaf-count DP, depth cap và feasibility/Pareto pruning đã có;
  beam search, encoded-cost lower bound, early stopping và objective \(J\) có
  trọng số decode/RSS còn deferred.
- Encoder/inspect buffer dữ liệu. enwik8 đo peak RSS MathZip 672.762 MiB.
- Historical Max search không hoàn tất trong 600 giây trên `kennedy.xls` và
  `ptt5`; focused regression của current source đã hoàn tất cả hai dưới cùng
  limit, nhưng throughput 0,003 MB/s. Full revision dùng screened portfolio
  cho input ≥1 MiB và hoàn tất cả enwik8/enwik9; đây là bounded approximate
  pruning, không phải exhaustive proof hay Pareto improvement.
- Full đã đóng coverage enwik9, Pizza & Chili, mixed corpus, Git snapshots
  trên Full codec/thread grid và encrypted size/noise matrix. Trên 1.467.705.562 B,
  MathZip Fast/Balanced/Max t1 đạt ratio 1,0787/1,2513/1,2572;
  Zstd-default t1 3,2479 và XZ-default t1 4,3610. Balanced thua Zstd/XZ theo
  kích thước trên 600/725 và 620/725 file.
- Full xác nhận screened portfolio giữ workload hữu hạn nhưng trade-off yếu:
  Balanced→Max chỉ giảm 0,4627% weighted bytes, dùng 3,090× compression time
  và có W/T/L 135/503/87. Đây là evidence âm, không phải lý do mở lại grid.
- Dockerfile có source nhưng Docker runtime không khả dụng trong môi trường
  audit, nên chưa có image digest/smoke evidence.

Các hạng mục advanced ở trên được version-scope rõ ràng ngoài MVP v1/v2; chúng
không nằm trong 15 tiêu chí thành công bắt buộc. Full và corrected ablation đã
được chạy/validate/publish riêng, nên bước còn lại chỉ là duy trì compatibility,
đa máy/container evidence và nghiên cứu tối ưu thế hệ sau.

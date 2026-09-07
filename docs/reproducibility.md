# Hướng dẫn tái lập MathZip

Mục tiêu của hướng dẫn này là tái tạo build, test, corpus và raw benchmark mà
không cần dữ liệu/model bí mật. Không có comparative result nào được đóng dấu
“reproduced” chỉ vì lệnh chạy thành công; restored SHA-256 và schema validation
đều phải pass.

## 1. Artifact và phiên bản

Trước một run dùng để báo cáo, ghi:

```bash
git rev-parse HEAD
git status --short
rustc --version --verbose
cargo --version
python3 --version
uname -a
```

Giữ `Cargo.lock` và container image digest cùng result. Nếu worktree dirty, lưu
patch hoặc source-tree manifest SHA-256; chỉ một commit hash không mô tả được
source đã chạy.

Repository dùng:

- Rust 1.85 trở lên theo workspace manifest;
- Python 3.10+; script checked-in chỉ cần standard library cho config dạng
  JSON-compatible YAML;
- PyYAML chỉ cần cho YAML không đồng thời là JSON;
- baseline CLI tùy suite: gzip, bzip2, xz, zstd, lz4, brotli và 7z.

### Recorded publication snapshot

Quick hiện tại dùng codec revision sạch
`6dbc3860910e8d310cfa8bbeadbf97f0529beff1`, source-tree SHA-256
`91797df21983656b4d8020958e3619a646f46dfe86a4560d854e22483838c196`
và MathZip binary SHA-256
`6568f05094d05eac86a12ee608bc10c34c268080a25636bbaa8b9b2f59f63549`.
Focused Silesia dùng revision sạch
`0e3bd26e9883b7b6d6ed51def05b1822efbda6bf`, source-tree SHA-256
`dd883471e0a661461fd56cf892f01dfbc90a8ec2e9d192d87f07b8e0b5e4cfad`
và MathZip binary SHA-256
`485c9cdd7533e34d1a18592bd6652d4ba590b62e6188e0a1a63558614152d154`.
Focused Bit-plane ablation dùng revision sạch
`1703b73f3ef51753ed1f92ea5e2ce28da9bcdf22`, source-tree SHA-256
`612a5cf7d57a2607bfc8c297bf9bf08214ac25e065ea4d9a08ee881ddac733ac`
và MathZip binary SHA-256
`237c4515d958afcc373078badbec902e7736a3582891b734fd4ba555a326094f`.
Focused Segmentation ablation dùng revision sạch
`3c864d915b0d2452da712cd691796a4bdf987876`, source-tree SHA-256
`9c9501edf2a575addc6f45ae5b5fce31352031a79d412d6993674c994d83991d`
và MathZip binary SHA-256
`1b5e433a5321b00ecd7969bf1259db52637b8fee8abddb6e379123fe86bd37f2`.
Focused Max timeout regression dùng revision sạch
`e6ce84274a45b5a6e4738844921f9b374439c643`, source-tree SHA-256
`e0f67fc713a0684287f72014b3c539c2d369324137d1fc77367b08bb27b5516e`
và MathZip binary SHA-256
`60b440370688da1926dabd9a0b1c5fc304969dce095c2abc2e86d03845d828f2`.
Git Copy ablation dùng revision sạch
`163fc78cbadc0fdb5888781080baade7b576811d`, source-tree SHA-256
`6397e3d01b12d567f54c378f036e785ee107916f8d9ab873b13f41a1031e0fd2`
và MathZip binary SHA-256
`c4cc0e9b8a8469f70e5c9d594b127f5bf2eb6ad6a402388bb4595772197c2437`.
Residual ablation dùng revision sạch
`a130e30389cad1efe6db3ffad7764022a446531e`, source-tree SHA-256
`6e0ac49416c7a7d9db4d492df6fa3a125c8ea1556fd7f053580499a9cc85e7dd`
và MathZip binary SHA-256
`a2818ffa822338c2ffa00159b15739a0618417aa8a74a417e3656758181d75d6`.
Recursive ablation dùng revision sạch
`aac547e57647b0135c555c0b59a5b0c01db476ea`, source-tree SHA-256
`0164eab3f424d99e9976ecfe9b1f5ceeff2c8fea88b62624c7b4adb3ad8bf33f`
và MathZip binary SHA-256
`8a40009eafaac4ae63a396de1751d4a4f8519130b66aba49f1a7fb1e427c5559`.
Corrected ablation dùng revision sạch
`e5366fa7c5c1cd5801bb9cc86b19a09592403137`, source-tree SHA-256
`e3813b1523ee29c09f8b60b9d7a06844219e21c20114e3a3ce5762ae75b5323a`
và MathZip binary SHA-256
`728494e5c0d1ba0aad42a039b056d26372cd60b9a744c0a4ad1744552084cd30`.
Full dùng revision sạch
`e60f423b5f85d1716eb2356f2460f97ba4227885`, source-tree SHA-256
`55b6a32cc5abedcdcba8989065be83cbaa8600bede102e73c39306bb58153e63`
và MathZip binary SHA-256
`366ca45932751ecb10d52f4db3f4806b94acd44e3ec9ccbf45e33b45bd82e228`.
enwik8, historical ablation và Quick lịch sử dùng revision sạch
`235d190a20e64b6ddc5ce002379809ebbe8fe0cd`, source-tree SHA-256
`0d566a5ec2985f9e6bdc3ece3cdeb897525b487a61c5b409304e3db7306ce05d`
và MathZip binary SHA-256
`b1fb6882cc7be18c01cdf11c9c66507d8090b5aa0e161ec11ca518b50f254f78`.
Máy đo là Intel Xeon E5-2680 v3, 16 logical/physical cores, 16,715,317,248
byte RAM, Linux 7.0.0-28-generic x86-64, rustc 1.97.1 và Python 3.12.3.

| Profile | Run ID | Status |
|---|---|---|
| Full | `20260726T044843Z-213f07c3` | 24.650/24.650 rows, 73.950 measured trial, strict exact-grid + 15-plot pass; 0 warning |
| Quick | `20260724T134555Z-8b4c06c2` | 477/477 rows, strict exact-grid pass |
| Quick historical | `20260724T025637Z-8563453d` | 468/468 rows, legacy-v1 strict pass |
| Silesia | `20260724T165449Z-4ef6e508` | 108/108 rows, strict exact-grid pass |
| enwik8 | `20260724T031043Z-f6e4fbfe` | 9/9 rows, legacy-v1 strict pass |
| Corrected ablation | `20260725T170925Z-dde3921f` | 252/252 rows, strict exact-grid + 15-plot pass; 0 warning |
| Historical ablation | `20260724T032334Z-aca5bed4` | immutable legacy-v1; 252 rows recorded; 250 successful; 2 Max warm-up timeouts |
| Git Copy ablation | `20260724T140810Z-c825d7e1` | 45/45 rows, strict exact-grid pass |
| Residual ablation | `20260724T143401Z-3bb520a6` | 36/36 rows, strict exact-grid pass |
| Recursive ablation | `20260724T161541Z-70dfeff6` | 30/30 rows, strict exact-grid pass |
| Bit-plane ablation | `20260724T174812Z-583dbb61` | 42/42 rows, strict exact-grid pass |
| Segmentation ablation | `20260725T051723Z-a5f0fdfc` | 90/90 rows, strict exact-grid pass |
| Max timeout regression | `20260725T055807Z-2e6ce6d4` | 4/4 rows, strict exact-grid pass; 0 failure |

Full cùng mười profile artifact hiện tại và historical ablation (không tính
snapshot Quick lịch sử) có 77.979/77.979 measured successful trial khôi phục
đúng SHA-256 input đã ghi. Full riêng có 73.950 trial; corrected ablation có
756 trial. Snapshot Quick lịch sử thêm 1.404 trial thành công, nên toàn bộ 13
result document được track có 79.383/79.383 successful trial.

Không có container image digest vì Docker runtime không khả dụng trong môi
trường đo; các run trên là native evidence và không được gọi là container
reproduction.

Full đã đo 725 input/1.467.705.562 byte và 34 codec-thread key, tức
24.650 row. Checkpoint cho phép dừng/resume an toàn nhưng run công bố hoàn tất
trong một execution interval, `resume_count=0`, với elapsed 129.095,967 giây.
Inline metrics dùng đúng 8.700 MathZip compression/search và không thêm
compression probe thứ hai; screened portfolio ≥1 MiB chặn candidate work cho
standard Balanced/Max.

Quick hiện tại nhúng snapshot 53 input và đo một encrypted payload 4 KiB. Quick
lịch sử là snapshot 52 input trước encrypted generator v2; không dùng config
hiện tại để hồi tố vào 468 row cũ. Full, Quick hiện tại, Silesia, Git Copy,
residual, Recursive, Bit-plane, Segmentation, Max timeout regression và
corrected ablation đều có `expected-grid.json`, `run-identity.json` cùng
resume metadata. Quick historical, enwik8 và historical ablation là ba
artifact legacy-v1 được validator đọc tương thích nhưng không có checkpoint
identity mới.

## 2. Native build

```bash
cargo build --locked --release
cargo test --locked --workspace
```

Nếu `--locked` báo thiếu hoặc stale lockfile, artifact chưa đủ pin để tuyên bố
tái lập. Trong development có thể chạy `cargo build`, nhưng run report phải ghi
lockfile thực tế.

Binary:

```text
target/release/mathzip
target/release/mathzip-bench
```

Ghi digest:

```bash
sha256sum target/release/mathzip target/release/mathzip-bench
```

## 3. Smoke round-trip

```bash
mathzip_smoke_dir=$(mktemp -d /tmp/mathzip-smoke.XXXXXX)
printf 'MathZip deterministic smoke input\n' \
  > "$mathzip_smoke_dir/input.bin"
target/release/mathzip c \
  --mode balanced \
  "$mathzip_smoke_dir/input.bin" \
  "$mathzip_smoke_dir/input.mz"
target/release/mathzip d \
  "$mathzip_smoke_dir/input.mz" \
  "$mathzip_smoke_dir/restored.bin"
sha256sum \
  "$mathzip_smoke_dir/input.bin" \
  "$mathzip_smoke_dir/restored.bin"
target/release/mathzip inspect "$mathzip_smoke_dir/input.mz"
target/release/mathzip verify \
  "$mathzip_smoke_dir/input.bin" \
  "$mathzip_smoke_dir/input.mz"
```

Hai SHA-256 đầu vào/restored phải giống nhau. Dùng working directory riêng khi
chạy suite; không ghi đè file người dùng.

CLI mặc định giới hạn compress input ở 512 MiB; decompress/inspect/verify giới
hạn archive 256 MiB và output 128 MiB. Với input lớn đã được xác minh và machine
có đủ tài nguyên, tăng limit phải explicit, ví dụ:

```bash
target/release/mathzip d \
  --max-archive-bytes 17179869184 \
  --max-output-bytes 8589934592 \
  trusted-large.mz restored.bin
target/release/mathzip inspect \
  --max-archive-bytes 17179869184 \
  --max-output-bytes 8589934592 \
  trusted-large.mz
```

Đây là trust/resource decision của caller, không phải default khuyến nghị cho
archive không tin cậy.

Determinism:

```bash
target/release/mathzip c --mode balanced \
  "$mathzip_smoke_dir/input.bin" "$mathzip_smoke_dir/a.mz"
target/release/mathzip c --mode balanced \
  "$mathzip_smoke_dir/input.bin" "$mathzip_smoke_dir/b.mz"
cmp "$mathzip_smoke_dir/a.mz" "$mathzip_smoke_dir/b.mz"
```

Deterministic contract là cùng input + config + executable/dependency build.
Để kiểm tra mạnh hơn giữa hai máy, ghi cả kiến trúc, compiler và archive hash.

## 4. Synthetic corpus

Generator dùng SHA-256 counter stream và seed lưu trong manifest, không dùng
state ngầm của `random.Random`. Family `encrypted` trước hết tạo telemetry
record 32 byte có cấu trúc, rồi mã hóa bằng ChaCha20-IETF đúng RFC 8439; nó
không phải random bytes được đổi nhãn. Key/nonce synthetic, counter và phép dẫn
xuất đều nằm trong metadata từng entry:

```bash
PYTHONPATH=python python3 python/generate_synthetic.py \
  --output datasets/synthetic \
  --seed 1297748005
```

Corpus được tạo bằng generator version cũ cần tái sinh explicit:

```bash
PYTHONPATH=python python3 python/generate_synthetic.py \
  --output datasets/synthetic \
  --seed 1297748005 \
  --force
```

Quick/full config pin seed `1297748005` (`0x4D5A1025`); giá trị mặc định độc
lập của generator là `0x4D5A2025`. Output gồm `manifest.json` và
`manifest.csv`, mỗi entry có SHA-256. Không sửa byte corpus sau generation.

Full profile dùng directory và size matrix riêng:

```bash
PYTHONPATH=python python3 python/generate_synthetic.py \
  --output datasets/synthetic_full \
  --seed 1297748005 \
  --sizes 0,1,31,256,4096,65536,1048576
```

Tái chạy vào directory khác và so manifest/file hashes:

```bash
PYTHONPATH=python python3 python/generate_synthetic.py \
  --output /tmp/mathzip-synthetic-repro \
  --seed 1297748005
diff -u \
  datasets/synthetic/manifest.json \
  /tmp/mathzip-synthetic-repro/manifest.json
```

ChaCha20 thuần Python được đối chiếu cả block vector section 2.3.2 và encryption
vector section 2.4.2 của RFC 8439 trong `python/tests/test_synthetic.py`.
Giải mã ciphertext generated bằng key/nonce trong manifest phải khôi phục đúng
record plaintext có cấu trúc; test cũng pin key, nonce, plaintext hash và
ciphertext hash cho seed chính thức. Noise variants của cùng size là
post-encryption perturbations từ một base ciphertext, không phải các encryption
session độc lập.

Trong lúc regenerate, `.generation-in-progress.json` làm cache fail closed.
Payload và CSV/JSON được replace nguyên tử; verifier từ chối marker còn sót,
file orphan hoặc hai manifest không cùng path/size/hash. Optional Zstd sample
pin availability, version và executable SHA-256 vào cache identity.

Nếu `zstd` không có, generator ghi sample tùy chọn bị skip; hai environment chỉ
so được khi tool availability giống nhau. Các fixture gzip/bzip2/xz/zip/PNG/
JPEG/ISO-BMFF còn lại deterministic theo code generator.

Full profile còn cần mixed corpus và ba source snapshots tự sinh:

```bash
PYTHONPATH=python python3 python/generate_auxiliary_corpora.py \
  --output datasets/generated \
  --seed 1297748005
```

Manifest ghi file hashes, toolchain và optional ELF/shared-library/Zstd fixture
bị skip. Binary do C compiler sinh có thể khác giữa toolchain, nên phải so theo
manifest của chính run thay vì giả định cross-toolchain byte-identical.

## 5. Standard corpus

Không commit corpus lớn. Manifest pin URL, compressed size, extraction bounds,
member allowlist và checksum:

```text
datasets/manifests/canterbury.json
datasets/manifests/calgary.json
datasets/manifests/silesia.json
datasets/manifests/enwik8.json
datasets/manifests/enwik9.json
datasets/manifests/pizza_chili.json
```

Quick download:

```bash
PYTHONPATH=python python3 python/download_datasets.py \
  --config configs/quick.yaml \
  --output datasets/data
```

Full download có thể cần hàng trăm MB archive và hơn 1 GiB extracted data:

```bash
PYTHONPATH=python python3 python/download_datasets.py \
  --config configs/full.yaml \
  --output datasets/data
```

Chỉ chuẩn bị focused enwik8:

```bash
PYTHONPATH=python python3 python/download_datasets.py \
  --config configs/enwik8.yaml \
  --output datasets/data
```

Chỉ chuẩn bị focused Silesia:

```bash
PYTHONPATH=python python3 python/download_datasets.py \
  --config configs/silesia.yaml \
  --output datasets/data
```

Downloader tải vào cache/file tạm, kiểm tra size/checksum, chống path traversal,
áp extraction bounds rồi mới publish dataset. Download và extraction không
nằm trong timed benchmark.

Để audit một corpus:

```bash
sha256sum datasets/data/canterbury/*
```

So output với combined/expected manifest do downloader tạo; không thay mirror
nếu byte hash khác.

## 6. Benchmark

Quick:

```bash
scripts/benchmark_quick.sh
```

Full:

```bash
scripts/benchmark_full.sh
```

Artifact công bố:

```text
benchmarks/results/full/20260726T044843Z-213f07c3/
benchmarks/results/full/latest-report.md
benchmarks/plots/full/
```

Xác minh độc lập:

```bash
PYTHONPATH=python python3 python/verify_results.py \
  benchmarks/results/full/latest.json \
  --strict \
  --plots benchmarks/plots/full
```

Expected output là `verification passed with 0 warning(s)`. Run phải có
`status=complete`, `result_count=24650`, `failure_count=0`,
`scientifically_compliant_run=true`, config SHA-256
`213f07c3f01c6e13b1721131073e6d0f4f5e592e15ae0cbe24c5705aba0940bf`
và expected-grid SHA-256
`d0e49183f58d18b88a7eb2877b72e9f3cb757961f322dae3befdd04779d59a22`.

Full fail-fast nếu thiếu bất kỳ executable bắt buộc
`gzip bzip2 xz zstd lz4 brotli 7z/7zz`; không tạo hàng nghìn row
`unavailable` rồi gọi đó là run hoàn chỉnh. Nếu tool được cài ngoài system
prefix, prepend directory ổn định vào `PATH` cho cả lần đầu và mọi lần resume.
Config Full dùng inline metrics và strict exact-grid verification; timeout hay
failure đã chạy được giữ thành warning có provenance thay vì bị xóa.
Timeout mỗi operation của Full là 5.400 giây. Mức này được pin sau exact
enwik9 preflight ngày 2026-07-26: Brotli q11 cần 3.489,38 giây, chỉ còn 110,62
giây so với limit cũ 3.600 giây, trong khi bảy cell Max rủi ro còn lại đều
round-trip đúng SHA-256. Bảng lệnh/kết quả diagnostic nằm trong
`docs/benchmark-methodology.md`; các lượt preflight không được ghép vào raw
Full result.

Corrected 20-variant ablation:

```bash
scripts/benchmark_ablation.sh
```

Published run `20260725T170925Z-dde3921f` completed the exact 12-input ×
21-codec grid: 252/252 successful rows, 756 measured trials, zero
failure/warning, one warm-up plus three repetitions, inline metrics, and
9,327.963 seconds elapsed. The clean revision/tree/binary are recorded above.
Its `expected-grid.json`, `run-identity.json`, checkpoint, generated report,
and all 15 PNG plots pass strict verification.

For reproduction analysis, V13/V14 differ only in residual coders:
1,044,616→324,348 B (-68.9505%) and 279.086→251.921 s aggregate median
compression time (-9.7336%); V14 wins/ties/loses 5/1/6 files. V13/V17 differ
only in whole-file fallback: 11 ties and one 36-byte V17 loss on random data.
V13 and V19 Balanced match archive hash and inspection on 12/12 inputs. V20
Max reduces bytes 25.6340% but takes 2.583× aggregate time and has size W/T/L
3/1/8; without `kennedy.xls`, Max is 1,761 B larger. These comparisons must be
recomputed from the raw rows, not inferred from the median-ratio ablation plot.

The immutable historical run `20260724T032334Z-aca5bed4` remains separately
addressable. It has two Max warm-up timeouts and confounded V13/V14/V17
definitions; reproduce its strict evidence with `--allow-failures`, but never
replace its rows or use those legacy pairs for causal attribution. Ablation
và Full vẫn là hai result document riêng, không được ghép hàng.

Focused enwik8:

```bash
scripts/benchmark_enwik8.sh
```

Script này dùng `configs/enwik8.yaml`: enwik8 100 MB, MathZip Fast và baseline
Fast, một thread, một warm-up, ba repetition và timeout 3.600 giây. Có thể đặt
`MATHZIP_SKIP_DOWNLOAD=1` chỉ khi `datasets/data/enwik8` đã được downloader
verify.

Focused toàn bộ Silesia:

```bash
scripts/benchmark_silesia.sh
```

Script dùng `configs/silesia.yaml`: đủ 12 file Silesia, MathZip Fast và tám
control/baseline Fast, một thread, một warm-up, ba repetition, timeout 3.600
giây. Recorded run `20260724T165449Z-4ef6e508` hoàn thành 108/108 row và 324
measured trial, không có failure. MathZip dùng 187.537.684 B cho 211.938.580 B
input (ratio 1,130; 1,567 MB/s; peak RSS lớn nhất 328,750 MiB), so với
73.448.492 B của Zstd Fast (2,886) và 58.417.824 B của XZ Fast (3,628);
MathZip thua cả hai trên 12/12 file. Đây là focused Fast evidence, không thay
Full.

Paired Copy/reference ablation on generated Git snapshots:

```bash
scripts/benchmark_git_copy_ablation.sh
```

This focused run keeps an explicit Balanced model/transform/residual/search
configuration fixed and changes only whether Copy is enabled. It writes to its
own result directory rather than altering the historical 20-variant artifact.
Recorded run `20260724T140810Z-c825d7e1` completed 45/45 rows: Copy reduced
the 15 paired MathZip archives by 40 bytes (0.0440%) and affected only
`combined/all_versions.bin`.

Paired custom/Zstd-residual ablation on the 12-input ablation corpus:

```bash
scripts/benchmark_residual_ablation.sh
```

The two MathZip entries share the same Balanced base and change only the
residual-coder list. Direct Zstd is recorded separately. Recorded run
`20260724T143401Z-3bb520a6` completed 36/36 rows: Zstd residual reduced the
MathZip total from 1,045,829 to 322,067 bytes; direct Zstd used 328,293 bytes.

Paired Adaptive/Recursive segmentation ablation:

```bash
scripts/benchmark_recursive_ablation.sh
```

The two MathZip entries share the same Fast base; one explicitly selects
Adaptive and the other selects Recursive depth 4. Zstd Fast is a separate
control. Recorded run `20260724T161541Z-70dfeff6` completed 30/30 rows on eight
64 KiB synthetic inputs plus `alice29.txt` and `sum`. Recursive reduced the ten
paired archives from 266,151 to 265,911 bytes (240 B; 0.0902%), winning 7,
tying 1 and losing 2, while aggregate compression throughput fell from 0.708
to 0.373 MB/s.

Required Fixed-size sweep plus ChangePoint/Adaptive comparison:

```bash
scripts/benchmark_segmentation_ablation.sh
```

Tám MathZip entry dùng cùng Fast model/transform/residual profile. Sáu entry
Fixed lần lượt dùng 256 B, 1/4/16/64/256 KiB; hai entry còn lại dùng anchor
4 KiB và đổi ChangePoint/Adaptive. Zstd Fast là control riêng. Script yêu cầu
đúng expected grid, ba measured repetition và strict result verification.
Run `20260725T051723Z-a5f0fdfc` hoàn thành 90/90 row và 270 measured trial,
không failure. Trên tổng 714.617 B, ChangePoint dùng 262.012 B (ratio 2,727;
0,913 MB/s), Adaptive dùng 264.305 B (2,704; 0,548 MB/s), Fixed 4 KiB dùng
271.155 B (2,635; 1,556 MB/s) và Zstd Fast dùng 165.733 B (4,312;
13,338 MB/s). ChangePoint thắng Adaptive 8, hòa 1, thua 1.

Replay hai Max timeout lịch sử:

```bash
scripts/benchmark_max_timeout_regression.sh
```

Profile chỉ lấy `kennedy.xls` và `ptt5`, giữ timeout 600 giây, một warm-up và
ba measured repetition. Zstd Max là control. Inspection và phase probe được
bật cho mọi measured MathZip trial; mọi measured archive phải deterministic,
restored SHA-256 phải khớp và strict evidence validation phải pass. Recorded
run `20260725T055807Z-2e6ce6d4` hoàn thành 4/4 row, 12 measured trial và
0 failure. MathZip Max dùng tổng 530.968 B cho 1.542.960 B input (ratio 2,906;
0,003 MB/s), so với Zstd Max 113.190 B (13,632; 1,306 MB/s). Median MathZip
cho `kennedy.xls` là 368,526 s/430.446 B và cho `ptt5` là
158,502 s/100.522 B, đều dưới limit. Phase probe cộng 3,672 s search,
143,288 s fitting và 380,287 s residual coding, lần lượt chiếm 0,696%,
27,177% và 72,127% tổng phase time.

Run dùng revision/tree/binary đã ghi ở mục publication snapshot và giữ ổn định
suốt run. Nó chứng minh current source xử lý đúng hai regression case dưới
600 giây; không sửa hai failure row ở ablation lịch sử và không thay thế Full.

Paired packed/independent Bit-plane ablation:

```bash
scripts/benchmark_bit_plane_ablation.sh
```

Hai MathZip entry giữ nguyên Balanced model/residual/Adaptive configuration và
chỉ đổi `bit-plane-packed` v1 sang `bit-plane-independent` v2; Zstd là control
riêng. Run `20260724T174812Z-583dbb61` hoàn thành 42/42 row trên chín input
64 KiB và năm input 31 B, tương ứng 126 measured trial đã verify. Packed dùng
148.473 B (ratio 3,974; 0,093 MB/s), independent dùng 152.803 B (3,861;
0,800 MB/s), Zstd dùng 81.901 B (7,204; 8,014 MB/s). Independent lớn hơn
packed 4.330 B (2,916%), với 0 thắng, 2 hòa và 12 thua về kích thước, nhưng
nén nhanh hơn khoảng 8,60 lần trên matrix này. Không được ngoại suy kết quả
synthetic hẹp này thành lợi thế tổng dụng.

Hoặc gọi orchestrator:

```bash
PYTHONPATH=python python3 python/run_benchmarks.py \
  --config configs/quick.yaml
```

Full mặc định tạo run mới; tiếp tục một checkpoint explicit bằng script:

```bash
scripts/benchmark_full.sh \
  --resume benchmarks/results/full/RUN_ID
```

Hoặc dùng biến môi trường cho automation:

```bash
MATHZIP_BENCHMARK_RESUME=benchmarks/results/full/RUN_ID \
  scripts/benchmark_full.sh
```

Script không tự scan/chọn “run gần nhất”; đặt đồng thời argument và environment
variable là lỗi. Có thể trỏ trực tiếp tới `RUN_ID/checkpoint.json`. Khi gọi
Python runner trực tiếp, vẫn phải truyền đúng config và MathZip binary như lần
đầu; `--output-dir` không được dùng cùng `--resume`.
Trước khi runner xác minh identity, nhánh resume chỉ verify file hiện hữu: nó
không chạy Cargo build, không regenerate synthetic/auxiliary corpus và không
download. Vì vậy một source/generator mới không thể ghi đè input cũ rồi mới báo
mismatch.
Runner chỉ resume khi config SHA/snapshot, source revision/tree sạch,
executable hashes/build-info, input manifest/live hashes, hostname đã băm,
CPU/core/RAM/OS, affinity/governor, execution environment/container/filesystem
identity và toàn bộ warm-up/repeat/timeout/thread policy khớp chính xác.
Thông báo mismatch chỉ rõ nhóm evidence khác biệt và runner không chạy thêm
row nào. Row bị dừng giữa chừng được chạy lại toàn bộ warm-up + repetitions.

Các script phải tạo working output mới, warm-up, chạy ít nhất ba repetition,
giải nén mỗi archive, so SHA-256 và lưu failure thay vì bỏ qua. Timed region
không gồm build/download.

MathZip wrapper của harness truyền `--max-input-bytes 8589934592`,
`--max-output-bytes 8589934592`, `--max-archive-bytes 17179869184` và
`--max-segments 8388608` để corpus lớn đã kiểm checksum không bị default CLI
512/128/256 MiB/65.536 segment chặn. Raw result phải giữ nguyên các argv này;
chúng không nới limit cho lệnh CLI ngoài benchmark.

Harness tự ghi Git revision/dirty state, source-tree SHA-256, binary hashes
trước/sau run, build revision/dirty state nhúng trong MathZip binary, input
manifest hash/verification và environment metadata. Nếu checkout chưa có HEAD,
nó nhúng manifest path/size/hash của source để run development vẫn audit được,
nhưng MathZip publication mode yêu cầu binary khớp một Git revision sạch.
Sau mỗi row hoàn tất, một checkpoint-row được atomic-write ngoài timed region.
`latest.json`/`latest.txt` không thay đổi cho tới khi verifier-side expected grid
không còn key thiếu, dư hoặc trùng. `results.json` ghi original start, các
execution interval và active elapsed riêng với wall elapsed. Active elapsed là
thời gian đã checkpoint được của các invocation; nếu process bị kill giữa một
row, phần chưa checkpoint của row dở là lower bound và row đó được đo lại.
Một advisory lock theo canonical output root ngăn hai process cùng resume/publish
một run.

Rust-side smoke benchmark cho vài file:

```bash
mkdir -p benchmarks/results
target/release/mathzip-bench \
  --mode balanced \
  --repeats 3 \
  --pretty \
  datasets/synthetic/constant_00/*.bin \
  > benchmarks/results/mathzip-smoke.json
```

Helper này đo in-process MathZip và kiểm tra determinism/round-trip; nó không
thay thế orchestrator baseline, RSS, timeout và environment capture.

## 7. Validate và tạo report

```bash
PYTHONPATH=python python3 python/verify_results.py \
  benchmarks/results/quick/latest.json \
  --strict
PYTHONPATH=python python3 python/verify_results.py \
  benchmarks/results/enwik8/latest.json \
  --strict
PYTHONPATH=python python3 python/verify_results.py \
  benchmarks/results/silesia/latest.json \
  --strict \
  --plots benchmarks/plots/silesia
PYTHONPATH=python python3 python/verify_results.py \
  benchmarks/results/ablation/latest.json \
  --strict \
  --plots benchmarks/plots/ablation
PYTHONPATH=python python3 python/verify_results.py \
  benchmarks/results/ablation/20260724T032334Z-aca5bed4/results.json \
  --strict \
  --allow-failures
PYTHONPATH=python python3 python/generate_plots.py \
  benchmarks/results/quick/latest.json \
  --output benchmarks/plots
PYTHONPATH=python python3 python/generate_report.py \
  benchmarks/results/quick/latest.json \
  --output benchmarks/report.md
scripts/verify_all.sh
```

Có thể truyền directory của một run cụ thể nếu directory đó chứa
`results.json`. `--allow-failures` không tắt strict validation: provenance,
timing protocol, repetition, inspection metrics, deterministic archive,
JSON/CSV parity và SHA evidence vẫn được kiểm tra; chỉ các failure row đã giữ
trong artifact được báo warning. Corrected ablation `latest.json` phải strict
pass với 0 warning và 15 plot bindings; chỉ historical ablation phải cho đúng
hai warning ở `kennedy.xls` và `ptt5`, đều là Max compression warm-up timeout
600 giây. Không dùng cờ này để gọi một suite không hoàn tất là zero-failure.

Nếu CLI của script thay đổi, `--help` của source revision là nguồn đúng. Report
generator chỉ được đọc validated raw JSON/CSV; không điền thiếu bằng estimate.
`plot_manifest.json` ghi `result_document_sha256` trên normalized JSON nội bộ
(`canonical-json-v1`), SHA-256 của source `python/mathzip_bench/plots.py`, và
SHA-256 của `plot_data.json` cùng mọi ảnh được liệt kê. Chuẩn nội bộ này sort
key, dùng separator compact, UTF-8 không ASCII-escape, không newline cuối và từ
chối NaN/Infinity; nó không phải RFC 8785. `verify_results.py --plots ...` kiểm
tra chéo toàn bộ binding, nên không phụ thuộc whitespace/key order của
`results.json` và phát hiện output plot bị sửa.

Trước khi dùng result trong `docs/research-report.md`, audit:

- `status=ok` kéo theo `roundtrip_verified=true`;
- input/restored SHA-256 giống nhau;
- archive size là file thật;
- mọi repetition deterministic hoặc được đánh dấu failure;
- MathZip breakdown cộng đúng compressed bytes;
- command argv, codec version, thread và machine metadata có mặt;
- corpus aggregate giữ failure rows;
- median/repetition count đúng config.

Fuzz smoke và cách chạy ba target được ghi riêng ở
[fuzzing.md](fuzzing.md). Fuzzing là robustness evidence, không thay
round-trip/schema validation của benchmark artifact.

## 8. Docker

Dockerfile là một phần của repository, nhưng audit 2026-07-24 không thể chạy
build/smoke vì host không có Docker runtime. Vì vậy các lệnh dưới đây là quy
trình tái lập, không phải bằng chứng rằng image hiện tại đã được build.

Build:

```bash
docker build --pull -t mathzip:local .
docker image inspect mathzip:local \
  --format '{{json .RepoDigests}} {{.Id}}'
```

Smoke:

```bash
docker run --rm mathzip:local --help
```

Sau khi corpus trên host đã được tạo/tải, mount nó vào repository root nội bộ
`/opt/mathzip` mà Python harness dùng để resolve path tương đối:

```bash
docker run --rm \
  -v "$PWD/datasets:/opt/mathzip/datasets" \
  -v "$PWD/benchmarks:/opt/mathzip/benchmarks" \
  mathzip:local \
  benchmark /opt/mathzip/configs/quick.yaml
```

Không dùng floating tag làm định danh report; lưu image ID/digest, base image
digest và build command. Dataset volume phải có manifest/checksum như native.

## 9. Kết quả mong đợi và không mong đợi

Một reproduction thành công bảo đảm:

- source/build/config/data được định danh;
- codec round-trip đúng;
- phép đo tuân protocol trong giới hạn ghi lại;
- raw artifact đủ để tính lại bảng/plot.

Nó không bảo đảm số timing giống tuyệt đối trên CPU/OS khác. Compression size
của deterministic MathZip SHOULD giống khi format/config/build tương đương;
timing và RSS phải được so trong cùng machine class.

Không coi các trường hợp sau là reproduction:

- chỉ chạy `inspect` nhưng không decompress/hash;
- dùng corpus cùng tên nhưng khác checksum;
- lấy số từ stdout estimate mà không có archive;
- bỏ timeout/failure rows;
- thay baseline version/options không ghi;
- copy bảng đã render mà không có raw JSON/CSV.

## 10. Checklist artifact release

- [x] Source revision/tree hash sạch cho mọi run đã công bố.
- [x] `Cargo.lock`, Rust/Python version.
- [ ] Container image digest hoặc native package inventory.
- [x] Config exact và environment capture cho Full/Quick/Silesia/enwik8/corrected+historical ablation/Git Copy/residual/Recursive/Bit-plane/Segmentation/Max regression; Full, corrected ablation cùng các run checkpoint-era có exact-grid/run identity.
- [x] Dataset manifests, source/license và verified checksums.
- [x] Synthetic seed/generator revision.
- [x] Raw JSON/CSV, schema version và validator evidence cho Full/Quick/Silesia/enwik8/corrected+historical ablation/Git Copy/residual/Recursive/Bit-plane/Segmentation/Max regression.
- [x] Archive/restored hashes trong raw trial evidence.
- [x] Plots/report sinh tự động từ các raw result đã công bố.
- [x] Plot manifest bind normalized result, generator source và mọi output hash.
- [x] Failure/timeout/unavailable rows được giữ.
- [x] README/research report không có claim vượt evidence.
- [x] Full raw JSON/CSV/checkpoint rows/report/15 plots; corrected ablation cung cấp `ablation_comparison.png`.

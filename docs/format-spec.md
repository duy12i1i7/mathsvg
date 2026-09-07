# MathZip Container Format v1 và v2

Trạng thái: đặc tả binary normative cho format version `1` và `2`. V2 là một
extension hẹp chỉ dành cho bit-plane byte-aligned; mọi archive không dùng
extension này tiếp tục được serialize theo v1.

Từ khóa **MUST**, **MUST NOT**, **SHOULD**, **MAY** được hiểu theo
[RFC 2119](https://www.rfc-editor.org/rfc/rfc2119). Archive là một chuỗi octet;
không phụ thuộc endianness, word size hoặc alignment của máy.

## 1. Mục tiêu

Hai version được thiết kế để:

- giải nén offline, không cần model hoặc dictionary ngoài archive;
- parse theo độ dài đã khai báo và kiểm tra bằng checked arithmetic;
- có raw fallback v1 cho mọi byte stream, kể cả file rỗng;
- cho phép encoder tìm model phức tạp nhưng giữ decoder hữu hạn và xác định;
- phát hiện corruption ở header, payload, từng segment và đầu ra cuối;
- mở rộng bằng format version mới mà không diễn giải mơ hồ ID chưa biết.

Format cung cấp integrity, không cung cấp authenticity. SHA-256 và CRC-32 không
thay thế chữ ký số hoặc MAC khi cần xác thực nguồn.

## 2. Quy ước primitive

| Tên | Encoding |
|---|---|
| `u8` | 1 octet |
| `u16`, `u32`, `u64` | unsigned little-endian, đúng 2/4/8 octet |
| `bytes[n]` | đúng `n` octet |
| `uLEB128` | unsigned LEB128 canonical, tối đa 10 octet cho `u64` |
| `SHA-256` | 32 octet theo FIPS 180-4 |
| `CRC-32` | CRC-32/ISO-HDLC (IEEE), polynomial reflected `0xEDB88320` |

CRC-32 dùng `init=0xffffffff`, phản chiếu input/output và
`xorout=0xffffffff`; vector `"123456789"` cho `0xcbf43926`.

Canonical uLEB128 MUST dùng số octet ngắn nhất. Decoder MUST từ chối:

- hơn 10 octet;
- bit có nghĩa vượt `u64`;
- encoding overlong như `0x80 0x00`;
- EOF trước octet kết thúc.

Mọi phép cộng/nhân offset và length MUST dùng checked arithmetic trước khi
slice hoặc allocate.

## 3. Bố cục archive

```text
+----------------------+ offset 0
| Fixed header (80 B)  |
+----------------------+
| Transform descriptors|
+----------------------+
| Segment records      |  payload_size bytes in total
+----------------------+
| Footer (36 B)        |
+----------------------+ exact EOF
```

Kích thước file hợp lệ MUST bằng:

```text
80 + payload_size + 36
```

Không cho phép trailing bytes sau footer.

## 4. Fixed header

Header của cả v1 và v2 dài đúng 80 byte:

| Offset | Size | Field | Quy tắc |
|---:|---:|---|---|
| 0 | 4 | `magic` | ASCII `MZIP`, hex `4d 5a 49 50` |
| 4 | 2 | `version` | `1` hoặc `2` |
| 6 | 2 | `header_len` | `80` |
| 8 | 4 | `flags` | `0`; bit chưa định nghĩa MUST bằng 0 |
| 12 | 8 | `original_size` | số byte đầu ra cuối |
| 20 | 8 | `transformed_size` | số byte sau transform cuối |
| 28 | 8 | `payload_size` | từ byte 80 đến trước footer |
| 36 | 4 | `transform_count` | số transform descriptor |
| 40 | 4 | `segment_count` | số segment record |
| 44 | 32 | `original_sha256` | SHA-256 của file gốc |
| 76 | 4 | `header_crc32` | CRC-32 của byte header `[0,76)` |

### 4.1 Kiểm tra header

Decoder MUST, theo thứ tự an toàn:

1. Bảo đảm input có ít nhất 80 byte.
2. Kiểm tra magic.
3. Đọc `version`, `header_len`; implementation hiện tại chỉ nhận `(1, 80)` và
   `(2, 80)`.
4. Kiểm tra `flags == 0`.
5. Tính và so sánh CRC-32 của 76 byte đầu.
6. Kiểm tra `payload_size + 116 == file_size` bằng checked arithmetic.
7. Áp dụng decode limits trước mọi allocation.

`payload_size` không bao gồm header hoặc footer. Quy tắc kích thước phụ thuộc
version:

- v1: mọi transform giữ nguyên số byte, nên `transformed_size` MUST bằng
  `original_size`;
- v2: đặt \(P=\lceil original\_size/8\rceil\), khi đó
  `transformed_size` MUST bằng \(8P\). Với input không rỗng, v2 tăng từ 0 đến 7
  byte; input rỗng vẫn có transformed size 0.

File rỗng canonical:

```text
original_size = 0
transformed_size = 0
transform_count = 1
segment_count = 0
payload_size = 4  # Identity descriptor
original_sha256 = SHA-256(empty)
```

Vì vậy empty archive v1 dài 120 byte. Whole-file Raw fallback không rỗng có
một Identity descriptor, một 36-byte segment descriptor và Raw residual, nên
kích thước chính xác là `original_size + 156` byte. Đây là expansion bound của
fallback v0.1, không phải cam kết file `.mz` luôn nhỏ hơn input. Raw fallback
luôn dùng Identity và được serialize theo v1, kể cả khi một candidate v2 đã
được đánh giá nhưng không thắng.

## 5. Payload

Payload chứa chính xác:

1. `transform_count` transform descriptor;
2. `segment_count` segment record.

Không có padding ngầm giữa các record. Parser MUST tiêu thụ đúng
`payload_size` byte sau record cuối. Parser không được tìm footer bằng scan
magic vì bytes payload có thể chứa `MZFT`.

## 6. Transform descriptor

Prefix dài 4 byte:

| Offset tương đối | Size | Field |
|---:|---:|---|
| 0 | 1 | `transform_type` |
| 1 | 1 | `transform_flags` |
| 2 | 2 | `param_len` |
| 4 | `param_len` | `params` |

`transform_flags` MUST bằng 0 trong cả v1 và v2. `param_len` là little-endian
u16.

### 6.1 Transform ID registry

| ID | Tên | Version tối thiểu | Parameter | Thay đổi length |
|---:|---|---:|---|---|
| 0 | Identity | 1 | rỗng | không |
| 1 | Delta modulo 256 | 1 | rỗng | không |
| 2 | Previous-byte XOR | 1 | rỗng | không |
| 3 | Packed bit-plane transpose | 1 | rỗng | không |
| 4 | Stride byte-plane | 1 | `stride: u16` little-endian | không |
| 5 | Byte-aligned padded bit-plane | 2 | rỗng | \(8\lceil N/8\rceil\) byte |
| 6..255 | Unassigned | — | — | — |

Transform chain v1 MUST có ít nhất một descriptor. Không biến đổi được biểu
diễn bằng đúng một Identity descriptor params rỗng. Mọi transform MUST có đúng
parameter length quy định; trailing parameter bytes là lỗi.

Stride MUST thuộc `{2, 3, 4, 8, 16, 32, 64}`. Transform chain MUST thỏa:

- mọi transform v1 giữ nguyên length;
- transformed size tính lại từ `original_size` và chain phải đúng field header;
- mọi phép tính length không overflow hoặc vượt decode output limit.

V2 hiện được cố ý giới hạn: `transform_count` MUST bằng 1 và descriptor duy
nhất MUST là ID 5. Identity, transform v1, chain nhiều descriptor hoặc ID 5
trong archive v1 đều MUST bị từ chối. Giới hạn này tránh tạo semantics mơ hồ
cho composition có transform thay đổi length; một format version sau mới có
thể mở rộng chain.

Semantics byte/bit chính xác nằm trong
[mathematical-model.md](mathematical-model.md#3-transform-đảo-ngược).

### 6.2 Layout bit-plane v2

Với input \(X=(x_0,\ldots,x_{N-1})\), đặt
\(P=\lceil N/8\rceil\). Output ID 5 gồm đúng tám span liên tiếp, mỗi span dài
\(P\) byte, theo plane \(k=0,\ldots,7\). Bit của byte input thứ \(i\) được lưu:

```text
output[k * P + floor(i / 8)] bit (i mod 8)
    = (x[i] >> k) & 1
```

Bit trong mỗi byte được xếp LSB-first. Nếu `N mod 8 != 0`, các bit không dùng
ở phía high của byte cuối trong **mỗi** plane MUST bằng 0; decoder MUST từ chối
padding khác 0. Với `N = 0`, output rỗng.

Encoder tham chiếu chạy segmentation, model fitting, Copy search và optimizer
riêng trên từng span \(P\)-byte, rồi mới rebase offset và nối tám partition.
Vì vậy mọi segment do encoder này phát ra nằm trọn trong đúng một logical
plane. Đây là isolation của search/encoder; framing segment vẫn là partition
byte liên tiếp của toàn transformed stream như mục 7.

## 7. Segment record

Mỗi record gồm prefix cố định 36 byte, model parameters và residual payload:

| Offset tương đối | Size | Field |
|---:|---:|---|
| 0 | 8 | `offset` |
| 8 | 8 | `len` |
| 16 | 1 | `model_id` |
| 17 | 1 | `residual_mode` |
| 18 | 1 | `residual_id` |
| 19 | 1 | `segment_flags` |
| 20 | 4 | `model_param_len` |
| 24 | 8 | `residual_len` |
| 32 | 4 | `decoded_crc32` |
| 36 | `model_param_len` | `model_params` |
| ... | `residual_len` | `residual_payload` |

Record size được suy ra bằng checked arithmetic:

```text
36 + model_param_len + residual_len
```

`offset` và `len` tính theo byte của transformed stream. Segment v1/v2 MUST:

- có `len > 0`;
- xuất hiện theo thứ tự offset tăng;
- tạo một partition liên tiếp: segment đầu offset 0, và
  `next.offset == current.offset + current.len`;
- kết thúc đúng `transformed_size`;
- không chồng lấn hoặc tạo lỗ;
- có `segment_flags == 0`;
- có CRC-32 của đúng transformed bytes đã khôi phục trong segment.

Do đó file không rỗng MUST có ít nhất một segment; file rỗng MUST có 0 segment.

Recursive segmentation là lựa chọn search của encoder, không phải cấu trúc
container. Archive serialize đúng ordered leaf partition theo các quy tắc
trên, không serialize binary-tree topology hoặc split token. Vì vậy bật
Recursive không thêm flag/descriptor và decoder không cần biết partition đã
được tìm bằng Fixed, ChangePoint, Adaptive hay Recursive.

### 7.1 Residual mode

| ID | Tên | Khôi phục |
|---:|---|---|
| 0 | AddModulo | `(prediction + residual) mod 256` |
| 1 | XOR | `prediction XOR residual` |
| 2..255 | Unassigned | từ chối |

Mode vẫn phải là giá trị hợp lệ với residual toàn 0.

### 7.2 Model ID registry

| ID | Tên | Trạng thái layout |
|---:|---|---|
| 0 | Raw | params rỗng |
| 1 | Constant | `c: u8` |
| 2 | Affine | `a: u8, b: u8` |
| 3 | Polynomial | `degree: u8`, rồi forward-difference bytes |
| 4 | Periodic | `period: uLEB128`, rồi pattern bytes |
| 5 | Recurrence | `order: uLEB128`, coefficients, seeds |
| 6 | PiecewiseLinear | point count, rồi `(position_gap, value)` |
| 7 | Run | run count, rồi `(run_len, value)` |
| 8 | Sparse | default, count, rồi `(position_gap, value)` |
| 9 | Copy | backward `distance: uLEB128` |
| 10..255 | Unassigned | từ chối |

Normative semantics và validation của parameters nằm trong
[mathematical-model.md](mathematical-model.md#4-prediction-model). Decoder MUST
từ chối ID mà build đó chưa triển khai; không được fallback ID lạ sang Raw.

### 7.3 Residual ID registry

| ID | Tên | Output logic |
|---:|---|---|
| 0 | Raw | đúng `len` residual byte |
| 1 | RLE | `(run_len: uLEB128, value: u8)` |
| 2 | ZeroRun | `(zero_count, literal_count, literal bytes)` |
| 3 | Sparse | nonzero count, gap/value pairs |
| 4 | BitPack | bit width và LSB-first packed values |
| 5 | Zstd | một Zstandard frame độc lập |
| 6..255 | Unassigned | từ chối |

Residual decoder MUST:

- sinh đúng `len` byte, không ít hoặc nhiều hơn;
- tiêu thụ hết `residual_len` byte;
- từ chối run/gap ngoài bounds, varint non-canonical và BitPack padding khác 0;
- với Zstd, sinh đúng `len` byte và từ chối frame lỗi hoặc output sai length;
- kiểm tra output bound trước mỗi append.

V1/v2 không có residual coder `None`. Segment không rỗng luôn cần một coder
sinh đúng `len` residual byte; exact model thường dùng Sparse payload
`nonzero_count=0` (một byte). `residual_len == 0` không hợp lệ với segment
không rỗng. Mức nén Zstd không nằm trong semantics decode; encoder tham chiếu
dùng level 3 và chỉ bật coder ID 5 khi cấu hình yêu cầu explicit.

## 8. Footer

Footer dài đúng 36 byte:

| Offset tương đối | Size | Field |
|---:|---:|---|
| 0 | 4 | `footer_magic` = ASCII `MZFT` |
| 4 | 32 | `archive_sha256` |

`archive_sha256` là SHA-256 của toàn bộ:

```text
header[0..80] || payload[0..payload_size]
```

Footer không tự nằm trong digest. Decoder SHOULD kiểm tra digest trước model
decode; decoder MUST kiểm tra trước khi báo thành công. Sau khi reconstruct và
đảo transform, decoder MUST so sánh SHA-256 đầu ra với `original_sha256`.

CRC segment/header hỗ trợ định vị corruption nhanh. SHA-256 archive/đầu ra là
kiểm tra integrity mạnh hơn. Không checksum nào ở đây chống được một đối thủ có
thể sửa archive và tính lại digest.

## 9. Decode algorithm bắt buộc

```text
parse and validate fixed header
locate exact payload/footer using checked sizes
verify footer magic and archive SHA-256

parse all transform descriptors within payload bounds
validate the version-specific transform set
derive transformed_size from original_size and the transform
require derived size == header.transformed_size

expected_offset = 0
for each segment descriptor:
    validate lengths, IDs and parameter bounds
    require offset == expected_offset and len > 0
    decode exactly len residual bytes
    generate/combine prediction under DecodeLimits
        (Recurrence MUST combine sequentially and feed back restored bytes)
    verify decoded_crc32
    append segment
    expected_offset += len

require parser consumed payload exactly
require expected_offset == transformed_size
reverse transforms in reverse descriptor order
for v2, reject nonzero high padding bits in every plane
require output length == original_size
verify original_sha256
return output
```

Implementation MAY stream segment output, nhưng inverse transform có thể cần
buffer toàn transformed stream ở MVP. Peak memory và limitation này phải được
báo cáo.

## 10. Decode limits và threat model

Archive là dữ liệu không tin cậy. Các mối đe dọa chính:

- khai báo size cực lớn để gây allocation/decompression bomb;
- overflow khi cộng descriptor/parameter/residual lengths;
- varint overlong hoặc không kết thúc;
- segment overlap/gap và copy reference ngoài vùng đã decode;
- run/bitpack sinh nhiều output hơn khai báo;
- model order/period/control-point count cực lớn;
- transform hoặc transform chain khuếch đại công việc decode;
- payload truncated, trailing hoặc ID chưa biết;
- dữ liệu hỏng làm decoder panic hoặc loop vô hạn.

API decoder MUST nhận `DecodeLimits`. Giá trị mặc định của implementation
tham chiếu v0.1:

| Limit | Mặc định |
|---|---:|
| archive bytes | 256 MiB |
| output/original bytes | 128 MiB |
| transformed bytes | v1: bằng original; v2: `8 * ceil(original / 8)` |
| transform count | 16 |
| segment count | 65,536 và không vượt số byte transformed |
| tổng model params của archive | 64 MiB |
| tổng residual bytes của archive | 16 GiB, đồng thời bị chặn bởi archive |
| period | 1,048,576 byte |
| recurrence order | 16 |
| control points hoặc Run/Sparse model entries | 65,536 |
| copy window | phần transformed output đã decode |

Ứng dụng có thể hạ limit. Việc tăng limit phải là lựa chọn explicit, không lấy
trực tiếp từ archive. Đối với mọi archive được chấp nhận, vòng lặp decode MUST
bị chặn bởi count/length đã kiểm tra và tổng output.

Implementation tham chiếu còn chặn aggregate transform work ở
`8 * max_output_size`: Identity/Delta/XOR/Stride tính một đơn vị mỗi byte,
packed BitPlane và padded BitPlane tính tám đơn vị mỗi transformed byte. V2
dùng giới hạn transformed đã round-up tương ứng. Vì vậy một archive nhỏ không
thể yêu cầu một chain BitPlane dài trên output lớn chỉ nhờ count hợp lệ.

Aggregate model work cũng bị chặn ở `8 * transformed_size`. Polynomial được
tính theo số coefficient và Recurrence theo order; encoder tham chiếu giới hạn
Recurrence ở order 8 để archive do nó tạo tương thích budget mặc định. Decoder
vẫn có limit order 16, nhưng chỉ chấp nhận khi tổng model work còn trong budget.

Copy trong cả v1/v2 chỉ tham chiếu đoạn trước không chồng lấn:

```text
source_offset + len <= segment.offset
```

Không archive nào được phép thực thi code, tải tài nguyên, dùng `eval`, tra model
theo tên path/URL, hoặc chọn thuật toán có thời gian không bị chặn.

## 11. Versioning và forward compatibility

- Decoder chỉ hỗ trợ v1 MUST từ chối `version != 1`.
- Decoder tham chiếu hiện tại nhận v1 và profile v2 hẹp mô tả ở mục 6.2; mọi
  version khác MUST bị từ chối.
- V1 MUST từ chối transform ID 5. V2 MUST từ chối mọi transform set khác đúng
  một ID 5 params rỗng.
- Decoder MUST từ chối `header_len != 80` thay vì đoán layout.
- Unknown flag, transform ID, model ID hoặc residual ID MUST bị từ chối.
- Reserved values không được encoder phát ra.
- Thêm ID có semantics mới mà vẫn giữ framing phải dùng format version sau hoặc
  một registry revision được đặc tả rõ; decoder cũ vẫn phải fail closed.
- Đổi checksum coverage, endianness, descriptor length hoặc prediction
  semantics bắt buộc tăng format version.

Forward-compatible ở đây nghĩa là parser phát hiện version/feature không hỗ trợ
một cách rõ ràng; không có nghĩa decoder cũ phải giải mã được model tương lai.

## 12. Canonical encoding

Hai archive khác nhau có thể giải nén cùng output, nhưng encoder deterministic
SHOULD phát ra dạng canonical:

- header flags và descriptor flags bằng 0;
- đúng một Identity descriptor khi không dùng transform khác; không Identity
  dư trong chain có transform khác;
- v2 có đúng một ID 5 và high padding bits của từng plane bằng 0;
- uLEB128 ngắn nhất;
- transform/segment không padding;
- partition liên tiếp và chỉ segment dương;
- parameter không có trailing bytes;
- run gộp tối đa; sparse chỉ lưu residual khác 0; BitPack padding bằng 0;
- exact model dùng encoding residual toàn 0 nhỏ nhất;
- tie-break theo [đặc tả mô hình](mathematical-model.md#8-quy-tắc-deterministic).

Decoder tham chiếu dùng strict parsing cho mọi canonical rule được đánh dấu
MUST/MUST NOT ở tài liệu này. Một công cụ phân tích riêng MAY nhận dạng dạng
không canonical để chẩn đoán, nhưng không được báo đó là archive hợp lệ hoặc bỏ
qua checksum/bounds. CLI `verify` và test corpus dùng strict parsing.

## 13. Inspect output

`mathzip inspect` phải parse an toàn như decoder và tối thiểu báo:

- version, original/transformed/compressed size;
- trạng thái header/archive checksum;
- transform chain;
- segment count;
- distribution model/residual/mode;
- storage breakdown, trong đó:
  - `container_overhead_bytes` = header + footer + transform descriptors;
  - `partition_metadata_bytes` = toàn bộ segment descriptors;
  - `model_parameter_bytes` = model parameters;
  - `actual_residual_coded_bytes` = residual payload lưu thật;
- `metadata_bytes = container_overhead_bytes + partition_metadata_bytes`;
- `raw_model_percentage`: tỷ lệ transformed bytes dùng predictor Raw, bất kể
  coder;
- `raw_fallback_percentage`: tỷ lệ transformed bytes dùng Raw model + Raw
  residual;
- Shannon entropy của stream residual đã decode, tính bằng bit/residual-byte;
- số non-Raw segment có `model params + residual payload` nhỏ hơn:
  - Raw residual body cùng length;
  - Zstd level-3 frame của chính transformed segment đó.

Với mọi archive inspect thành công, breakdown phải thỏa:

```text
compressed_size =
    container_overhead_bytes
  + partition_metadata_bytes
  + model_parameter_bytes
  + actual_residual_coded_bytes
```

Descriptor 36 byte chung cho mọi lựa chọn segment bị loại khỏi hai phép so
“thắng Raw/Zstd”. Hai metric này là probe cục bộ để phân tích model, không phải
kết luận rằng toàn archive thắng codec đối chứng.

Inspect không được báo checksum đầu ra là hợp lệ nếu chưa reconstruct và hash
đầu ra. Trạng thái nên phân biệt `not checked`, `valid`, `invalid`.

## 14. Test vectors cần duy trì

Repository SHOULD chứa file hoặc hex fixture cho:

1. empty archive;
2. một raw byte;
3. constant segment;
4. delta và XOR transform;
5. packed bit-plane v1 có boundary logical plane không byte-aligned;
6. padded bit-plane v2 cho input chia hết và không chia hết cho 8, gồm từ chối
   high padding bit khác 0 và segment encoder không vượt plane boundary;
7. stride có partial record;
8. mỗi model/residual ID đã triển khai;
9. archive hỏng từng checksum;
10. truncated ở mọi field length;
11. varint overlong/overflow;
12. unknown ID/flag/version;
13. segment gap, overlap và tổng length sai;
14. declared allocation vượt `DecodeLimits`.

Mỗi vector hợp lệ phải nêu archive SHA-256 và output SHA-256 để phát hiện thay
đổi format ngoài ý muốn.

Golden v1 tối thiểu hiện được khóa bởi integration test:

| Field | Giá trị |
|---|---|
| Input hex | `00010203` |
| Encoder config | Fast; Fixed 4; Identity; Raw model; Raw residual; no fallback |
| Archive size | 160 byte |
| Archive SHA-256 | `ef495ec6d95d329304d8481376d4ca2a263c9470107dd0f3c8d8392cf01f2c88` |
| Output SHA-256 | `054edec1d0211f624fed0cbca9d4f9400b0e491c43742af2c5b0abebf0c990d8` |

Golden v2 dùng cùng input nhưng ép padded BitPlane:

| Field | Giá trị |
|---|---|
| Input hex | `00010203` |
| Encoder config | Fast; Fixed 4; padded BitPlane ID 5; Raw model; Raw residual; no fallback |
| Archive size | 416 byte |
| Archive SHA-256 | `8fd085925a7747c6668afc1ab359e26c2b1a8cf563eae59a4ff737e495d40312` |
| Output SHA-256 | `054edec1d0211f624fed0cbca9d4f9400b0e491c43742af2c5b0abebf0c990d8` |

Thay đổi bất kỳ giá trị nào trong hai vector này phải được xem là thay đổi
output canonical hoặc format và cần review/versioning explicit.

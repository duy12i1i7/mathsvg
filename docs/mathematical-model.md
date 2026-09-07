# Mô hình toán học và bài toán tối ưu của MathZip

Trạng thái: đặc tả thiết kế cho format version 1 và profile bit-plane hẹp của
version 2; đây là cơ sở để kiểm thử codec. Những họ mô hình chưa có decoder
trong mã nguồn không được phép xuất hiện trong archive v1/v2; xem
[implementation-status.md](implementation-status.md).

## 1. Phạm vi và bất biến lossless

Đầu vào là một chuỗi byte

\[
X=(x_0,\ldots,x_{N-1}),\qquad x_i\in\{0,\ldots,255\}.
\]

Encoder chọn một chuỗi transform đảo ngược được \(T\), tạo \(Y=T(X)\), rồi
chia \(Y\) thành các đoạn liên tiếp, không chồng lấn. Với mỗi đoạn, encoder lưu
một model, tham số của model và residual. Decoder chỉ dùng dữ liệu trong
archive:

\[
\operatorname{Decode}(\operatorname{Encode}(X))=X
\]

cho mọi \(N\geq0\). Đây là điều kiện đúng/sai tuyệt đối; độ khớp, MSE hoặc
entropy ước lượng không thay thế được kiểm tra round-trip.

Quy ước:

- Chỉ số của model bắt đầu từ 0 tại đầu segment.
- Phép toán byte dùng số nguyên không dấu và modulo 256.
- Mọi phép chia số nguyên có dấu phải ghi rõ quy tắc làm tròn. Nếu không ghi,
  model đó chưa đủ đặc tả để đưa vào archive.
- Arithmetic của encoder và decoder phải cho cùng kết quả trên mọi kiến trúc;
  không dùng kết quả floating point làm tham số ngầm.
- Segment có độ dài 0 không được ghi. File rỗng có 0 segment.

## 2. Pipeline

\[
X \xrightarrow{T} Y
\xrightarrow{\text{partition}} (Y_1,\ldots,Y_M)
\xrightarrow{\text{model + residual}} A.
\]

Encoder thực hiện:

1. Phân tích đầu vào và sinh tập transform ứng viên hữu hạn.
2. Với từng transform, sinh tập boundary ứng viên hữu hạn.
3. Fit các model được bật cho mỗi cạnh segment hợp lệ.
4. Sinh prediction và thử các residual coder.
5. Serialize candidate bằng đúng version nhỏ nhất hỗ trợ transform đã chọn.
6. Dùng số byte serialize thật làm chi phí.
7. Chọn đường đi toàn cục; sau cùng so với raw fallback.

Decoder không thực hiện model search. Nó parse descriptor, decode residual,
sinh/kết hợp prediction (tuần tự đối với Recurrence), khôi phục transformed
bytes, đảo transform, rồi kiểm tra SHA-256 của đầu ra.

Format và decoder v1 biểu diễn/đảo được một chain length-preserving có giới
hạn. V2 hiện chỉ nhận đúng một descriptor ID 5. Encoder v0.1 chưa tìm trên tích
Descartes của nhiều transform: mỗi archive candidate do encoder tạo chứa đúng
một descriptor trong thứ tự ưu tiên Identity/Delta/XOR/packed BitPlane/padded
BitPlane/Stride. Đây là giới hạn search, không phải thay đổi semantics của
transform. Nếu whole-file Raw fallback thắng, archive dùng Identity v1.

## 3. Transform đảo ngược

Mỗi transform được áp dụng lên toàn stream, theo thứ tự descriptor. Decoder đảo
theo thứ tự ngược. ID nhị phân nằm trong
[format-spec.md](format-spec.md#6-transform-descriptor).

### 3.1 Identity

\[
y_i=x_i.
\]

Format v1 luôn có ít nhất một transform descriptor. Không biến đổi được biểu
diễn bằng đúng một Identity descriptor không tham số.

### 3.2 Delta modulo 256

\[
y_0=x_0,\qquad y_i=(x_i-x_{i-1})\bmod256.
\]

Đảo:

\[
x_0=y_0,\qquad x_i=(y_i+x_{i-1})\bmod256.
\]

### 3.3 XOR với byte trước

\[
y_0=x_0,\qquad y_i=x_i\oplus x_{i-1}.
\]

Đảo:

\[
x_0=y_0,\qquad x_i=y_i\oplus x_{i-1}.
\]

### 3.4 Packed bit-plane transpose v1

Với \(N\) byte, transform xem output là đúng \(8N\) bit liên tục. Tám plane đi
theo thứ tự \(k=0,\ldots,7\); trong mỗi plane, bit của input đi theo
\(i=0,\ldots,N-1\). Đặt

\[
d=kN+i,\qquad
\operatorname{bit}(Y,d)=(x_i\mathbin{\gg}k)\mathbin{\&}1,
\]

trong đó bit có logical index \(d\) được lưu ở byte
\(\lfloor d/8\rfloor\), vị trí LSB-first \(d\bmod8\):

\[
Y_{\lfloor d/8\rfloor}\mathrel{|}=
\operatorname{bit}(Y,d)\mathbin{\ll}(d\bmod8).
\]

Vì tổng luôn là \(8N\) bit, transformed stream dài đúng \(N\) byte và không có
padding. Khi \(N\) không chia hết cho 8, boundary giữa hai logical plane có thể
nằm giữa một byte; segment descriptor v1 vẫn byte-aligned. Đây là một giới hạn
của bit-plane modelling v1 cần được tính trong benchmark.

### 3.5 Byte-aligned bit-plane độc lập v2

Đặt \(P=\lceil N/8\rceil\). Transform ID 5 tạo tám span byte-aligned liên tiếp,
mỗi span dài \(P\) byte. Với plane \(k\), input index \(i\):

\[
q=kP+\left\lfloor\frac{i}{8}\right\rfloor,\qquad
\operatorname{bit}(Y_q,i\bmod8)
  =(x_i\mathbin{\gg}k)\mathbin{\&}1.
\]

Do đó

\[
|Y|=8\left\lceil\frac{N}{8}\right\rceil.
\]

Bit trong mỗi byte đi theo LSB-first. Khi \(N\bmod8\ne0\), các bit high không
dùng trong byte cuối của từng plane bằng 0; inverse MUST kiểm tra và từ chối
padding khác 0 trước khi khôi phục \(X\).

Khác ID 3, tám boundary luôn byte-aligned. Encoder tham chiếu gọi toàn bộ
segmentation/optimizer/model fitting riêng cho từng span
\(Y[kP,(k+1)P)\). Chỉ sau khi tối ưu xong nó mới cộng `kP` vào offset và nối
tám partition, nên model parameter, Copy search và recursive state không rò
qua plane kế bên. V2 hiện yêu cầu ID 5 là transform descriptor duy nhất; không
composition với transform v1.

Tính năng này độc lập hóa **byte-stream modelling trên từng plane**. Nó không
phải bit-level LFSR; model LFSR ở mục 4.11 vẫn chưa có ID hay decoder.

### 3.6 Byte-plane transpose theo stride

Stride \(s\) của v1 MUST thuộc `{2, 3, 4, 8, 16, 32, 64}`. Forward output là
phép nối các column, kể cả record cuối không đủ:

```text
for c = 0 .. s-1:
    append x[c], x[c+s], x[c+2s], ... while index < N
```

Inverse đi cùng thứ tự column và đặt byte trở lại các index đó. Transform không
đổi kích thước. Tham số archive là đúng hai byte `u16` little-endian, không
phải varint.

### 3.7 Transform chưa thuộc v1/v2 chạy được

BWT, wavelet lifting, Hadamard, Morton order, matrix transpose và delta
multi-byte là hướng mở rộng. Không gán dữ liệu archive cho các ID chưa có
decoder, test vector và giới hạn tài nguyên.

## 4. Prediction model

Mọi model tạo đúng `segment_len` byte prediction. Nếu model không thể tạo đủ
byte trong giới hạn đã khai báo, archive không hợp lệ.

### 4.1 Raw

\[
\hat y_i=0,\qquad r_i=y_i.
\]

Model không có tham số. Raw model + Raw residual là fallback bắt buộc. Encoder
không được chọn candidate lớn hơn raw toàn file, trừ overhead cố định tối thiểu
v1 của container. Khi báo metric, **Raw model** gồm mọi segment dùng predictor
Raw, kể cả khi residual được nén bằng RLE/ZeroRun/Sparse/BitPack/Zstd; **raw
fallback** chỉ gồm cặp Raw model + Raw residual.

### 4.2 Constant

\[
\hat y_i=c.
\]

Tham số canonical là đúng một byte `c`. Candidate fitter phải thử ít nhất mode
của segment; thử thêm byte đầu tiên hoặc median là tùy encoder. Việc chọn chỉ
dựa vào kích thước serialize cuối.

### 4.3 Affine

\[
\hat y_i=(ai+b)\bmod256.
\]

Tham số là hai byte `a`, `b`. Nhân được tính trong một kiểu số nguyên đủ rộng,
sau đó lấy tám bit thấp; không được phụ thuộc overflow của kiểu máy.

### 4.4 Polynomial

Polynomial trong v1/v2 dùng Newton forward-difference basis, không lưu hệ số
monomial.
Với degree \(d\in\{2,3,4\}\), tham số
\(c_k=\Delta^k y_0\bmod256\). Prediction được định nghĩa bằng state machine:

```text
state = [c[0], c[1], ..., c[d]]
repeat segment_len times:
    emit state[0]
    for k = 0 .. d-1:
        state[k] = (state[k] + state[k+1]) mod 256
```

Nó tương đương
\(\hat y_i=\sum_{k=0}^d {i\choose k}c_k\bmod256\), nhưng decoder MUST dùng
state machine trên để tránh ambiguity/overflow. Tham số là `degree: u8`, sau
đó `degree + 1` byte \(c_0,\ldots,c_d\). Bậc khác tập trên bị từ chối.

### 4.5 Periodic

\[
\hat y_i=P[i\bmod p].
\]

Tham số là `p: uLEB128` rồi đúng `p` byte pattern. Điều kiện:
\(1\le p\le\min(\text{segment_len},p_{\max})\). `p_max` là decode limit và
không được vượt 1 MiB trong decoder tham chiếu.

### 4.6 Linear recurrence modulo 256

Encoder thử \(p\in\{1,2,4,8\}\); decoder v1/v2 nhận mọi
\(1\le p\le\text{DecodeLimits.max_recurrence_order}\) và \(p\le n\):

\[
\hat y_i =
\begin{cases}
s_i,&i<p,\\
\sum_{k=1}^{p}a_k y_{i-k}\bmod256,&i\ge p.
\end{cases}
\]

Ở encoder, \(y_{i-k}\) là actual byte trước; ở decoder, đó là byte đã khôi phục
tuần tự. Vì vậy decoder phải decode residual vector trước rồi predict/combine
từng byte theo thứ tự; không được sinh toàn prediction từ prediction cũ. Tham
số là `p: uLEB128`, `p` coefficient bytes \(a_1,\ldots,a_p\), rồi `p` seed
bytes. Residual tại vùng seed bằng 0 trong encoding do encoder tạo.

### 4.7 Piecewise linear

Control points thỏa:

```text
0 = position[0] < ... < position[K-1] = segment_len - 1
```

Tại khoảng \([u,v]\), đặt \(d=v-u\), \(n=i-u\), và
\(\Delta=value[v]-value[u]\) trong số nguyên có dấu:

\[
\hat y_i =
\left(value[u] + \operatorname{trunc}_0(\Delta n/d)\right)\bmod256.
\]

`trunc_0` là làm tròn về 0. Tích phải được tính bằng signed 64-bit đã kiểm tra;
giới hạn segment bảo đảm không overflow. Tham số là số điểm canonical
uLEB128, rồi các cặp `(position_gap: uLEB128, value: u8)`. Gap đầu là
absolute position; gap sau là số index không có control point giữa point trước
và point hiện tại. Decoder nhận segment một byte với đúng một point
`(0,value)`; encoder SHOULD ưu tiên Constant nếu tổng cost không lớn hơn.
Mọi position phải biểu diễn được bằng `u32`; vì vậy model này không hợp lệ cho
segment có endpoint vượt `u32::MAX`.

### 4.8 Run

Run model là biểu diễn exact:

```text
run_count: uLEB128
repeat run_count times:
    run_len: uLEB128
    value: u8
```

Mọi `run_len` dương và tổng đúng segment length. Encoder canonical gộp hai run
kề có cùng value. Prediction chính là stream run đã mở rộng. V1/v2 không
có residual coder `None`, nên exact prediction vẫn mang residual toàn 0
(thường Sparse count 0), không dùng payload rỗng. `run_count` không được vượt
segment length hoặc `DecodeLimits.max_control_points` (giới hạn entry dùng
chung, mặc định 65.536).

### 4.9 Sparse value model

Sparse model trong v1/v2 là prediction exact:

```text
default: u8
exception_count: uLEB128
repeat exception_count times:
    position_gap: uLEB128
    value: u8
```

Gap có cùng quy ước như Piecewise/Sparse residual. Position tăng, nằm trong
segment, và exception value MUST khác default. Prediction khởi tạo toàn
`default` rồi thay exception. Residual vì thế toàn 0 nhưng vẫn được encode bằng
một residual coder đã đăng ký. `exception_count` không được vượt segment length
hoặc `DecodeLimits.max_control_points`.

### 4.10 Copy/reference

Copy dự đoán từ transformed output đã khôi phục. Parameter là backward
`distance`:

\[
\hat y_i=Y[\text{current_offset}-\text{distance}+i].
\]

V1/v2 chỉ cho backward reference không chồng lấn:

```text
segment_len <= distance <= current_segment_offset
```

Tham số là canonical uLEB128 của `distance`. Ràng buộc không chồng lấn giữ
decode time tuyến tính, đơn giản hóa kiểm tra bounds, và không yêu cầu
dictionary ngoài archive. XOR hoặc modulo residual vẫn có thể bổ sung sai khác.

Search của encoder tham chiếu không quét mọi byte trong cửa sổ. Nó lập index
deterministic trên **candidate boundary starts**, khóa bằng bốn byte đầu dưới
dạng `u32` little-endian. Với một segment candidate, chỉ các source boundary có
cùng khóa, nằm trong cửa sổ lùi 1 MiB và thỏa non-overlap được xét theo thứ tự
gần nhất trước; tối đa 16 source hợp lệ được chấm điểm. Đây là heuristic search
của encoder, không thay đổi semantics decode của Copy.

### 4.11 Bit-plane LFSR

Đây là model dự kiến cho segment bit-plane và chưa được triển khai, kể cả khi
transform ID 5 đã tạo tám plane độc lập. Với bit \(b_i\):

\[
\hat b_i=\bigoplus_{k=1}^{p}c_k b_{i-k}.
\]

Tham số tối thiểu gồm order, tap bitset và seed bitset, đều MSB-first. Trước khi
model này được cấp ID riêng trong format, implementation phải chốt padding,
layout tham số, order tối đa và test vector Berlekamp--Massey. Không được tái
dùng ID của byte recurrence cho bit semantics mà không có cờ phân biệt.

### 4.12 Harmonic fixed-point

Model Fourier/harmonic chưa thuộc profile decode v1/v2. Một phiên bản tương lai
phải cố định hoàn toàn Q-format, bảng sin/cos (bao gồm hash hoặc bảng normative),
quy tắc rounding, saturation/modulo, số harmonic tối đa và vector kiểm thử đa
kiến trúc. Lưu vài hệ số lượng tử hóa nhưng gọi hàm `sin`/`cos` của hệ thống
không đáp ứng yêu cầu deterministic.

## 5. Residual

Với mỗi segment, descriptor chọn một trong hai phép kết hợp:

\[
r_i=(y_i-\hat y_i)\bmod256,\quad
y_i=(\hat y_i+r_i)\bmod256
\]

hoặc

\[
r_i=y_i\oplus\hat y_i,\quad
y_i=\hat y_i\oplus r_i.
\]

Phép trừ modulo là trên byte; không dùng signed overflow. Encoder phải thử cả
hai khi có lợi và tính cả byte ID/mode trong chi phí.

Các residual representation dùng chung cho v1/v2:

- **Raw:** đúng `segment_len` byte residual.
- **RLE:** chuỗi `(run_len: canonical uLEB128, value: u8)`; mọi run dương và
  tổng đúng `segment_len`. Encoder canonical gộp hai run cùng value kề nhau;
  decoder vẫn nhận dạng semantically tương đương chưa gộp.
- **ZeroRun:** chuỗi token
  `(zero_count: uLEB128, literal_count: uLEB128, literal_bytes)`. Ít nhất một
  count trong mỗi token phải khác 0; literal bytes không được chứa 0. Decoder
  phải sinh đúng `segment_len` byte và tiêu thụ hết payload.
- **Sparse:** `nonzero_count: uLEB128`; sau đó các cặp
  `(gap: uLEB128, value: u8)`. `gap` là số vị trí 0 kể từ sau exception trước
  (hoặc từ vị trí 0 đối với cặp đầu). `value` phải khác 0; vị trí phải tăng và
  nằm trong segment. Các vị trí không nêu có residual 0.
- **BitPack:** `bit_width: u8` trong 0..8, sau đó từng residual được pack
  LSB-first, bit thấp đến bit cao, vào stream cũng LSB-first.
  `bit_width=0` chỉ hợp lệ nếu mọi residual bằng 0. Với width \(w>0\), mọi
  residual phải nhỏ hơn \(2^w\); bit padding cuối phải 0.
- **Zstd hybrid:** một Zstandard frame độc lập cho residual của segment.
  Encoder tham chiếu dùng level 3; decoder bị chặn bởi `segment_len` và phải
  nhận đúng số byte đó. Coder này tắt mặc định và chỉ bật trong ablation.

ZigZag/Rice/Golomb/Huffman/arithmetic/rANS là candidate cho milestone sau.
Zstd residual phải được báo cáo tách biệt với custom coder và Zstd trực tiếp.

## 6. Chi phí MDL bằng kích thước thật

Đối với candidate \(q\), chi phí storage là:

\[
C(q)=8\cdot |\operatorname{Serialize}(q)|\quad\text{bit}.
\]

Không thay bằng entropy ước lượng, MSE hoặc kích thước residual chưa encode.
Chi phí toàn archive:

\[
C =
L(\text{header})+L(\text{transforms})+
\sum_j[L(\text{segment descriptor})+L(\theta_j)+L(R_j)]
+L(\text{footer}).
\]

Vì cả v1 và v2 đều byte-aligned, mọi chi phí cuối là bội số của 8. Encoder có
thể dùng estimate làm lower bound để prune, nhưng candidate thắng phải được
serialize thật bằng version tương ứng trước khi so sánh cuối.

Objective có tính tài nguyên là:

\[
J=C+\lambda T_\mathrm{decode}+\mu M_\mathrm{decode}.
\]

Giá trị \(\lambda,\mu\), đơn vị đo và model dự đoán tài nguyên phải nằm trong
config benchmark. Kết quả compression ratio chuẩn luôn báo cáo riêng \(C\);
không được gọi một candidate lớn hơn là “nhỏ hơn” chỉ vì \(J\) thấp.

## 7. Phân đoạn và dynamic programming

Cho boundary tăng dần

\[
B=(b_0=0,b_1,\ldots,b_K=|Y|).
\]

Mỗi cạnh \((b_i,b_j)\) chỉ hợp lệ khi độ dài nằm trong bounds. Gọi
\(E(i,j)\) là kích thước serialize nhỏ nhất của mọi model/residual candidate
cho cạnh đó, đã gồm segment descriptor. Dynamic programming:

\[
D[0]=C_\mathrm{prefix},
\]

\[
D[j]=\min_{i<j,\,(i,j)\in\mathcal E}
\left(D[i]+E(i,j)\right).
\]

Backpointer lưu transform/model/residual đã chọn. Header/footer và các trường có
độ dài phụ thuộc tổng số segment phải được tính lại sau khi có đường đi. Nếu
header cố định như v1/v2, chỉ `segment_count` thay đổi giá trị chứ không đổi độ
dài.

Implementation v0.1 sinh:

- `0`, `N` và fixed anchors theo `fixed_segment_size`;
- change point bằng tám nhóm tín hiệu trên hai cửa sổ bằng nhau: entropy byte
  Q8, histogram L1, mean/variance, lag-one autocorrelation, periodicity,
  compression-ratio probe, bit density và prediction-residual regularity;
- Adaptive là hợp của fixed anchors và change points;
- DP ChangePoint/Adaptive chỉ nhìn tối đa `dp_lookback` predecessors và áp dụng
  minimum/maximum segment length;
- Recursive hợp các candidate trên với một feasible backbone, stream-evaluate
  mỗi cạnh được search theo serialized size thật, rồi tối ưu trạng thái
  \(D[j,\ell]\) với \(\ell\le 2^{\texttt{max\_tree\_depth}}\); chỉ path thắng
  được fit lại deterministic để materialize payload;
- Recursive áp minimum/maximum nghiêm ngặt; chỉ toàn bộ input ngắn hơn minimum
  được phép là một leaf ngắn;
- Fixed nối đúng từng cặp anchor liên tiếp; minimum/maximum của profile không
  loại block fixed nhỏ hơn minimum, kể cả block cuối ngắn.

V1/v2 không serialize topology hoặc split token. Mọi partition có \(\ell\) leaf
liên tiếp đều dựng được thành cây nhị phân cân bằng có depth
\(\lceil\log_2\ell\rceil\), nên giới hạn leaf ở trên tương đương giới hạn depth
cho chi phí container. Recursive search prune trạng thái bị state ít leaf hơn với chi
phí thấp hơn hoặc bằng chi phối, đồng thời dùng lower bound số leaf cần thiết
để phủ prefix/suffix theo maximum length. `dp_lookback` vẫn là budget lấy mẫu
candidate predecessor, không phải exhaustive search.
Toàn candidate set Recursive, đã gồm endpoints và feasible backbone, được cap
deterministic ở 262.144 vị trí; persistent frontier có hard cap 2.000.000
state. Vượt cap làm candidate
transform đó trả `NoRepresentation`, cho phép whole-file Raw fallback thắng
thay vì OOM. Bounds Recursive chỉ áp cho searched leaf; whole-file Raw fallback
toàn cục vẫn chủ ý được miễn các bounds này.

Feature extraction không dùng floating point: entropy dùng `log2` Q8;
variance dân số được chuẩn hóa vào Q8; autocorrelation là covariance lag một
được chuẩn hóa; periodicity lấy exact-match score tốt nhất ở lag
2/3/4/8/16/32; compression probe lấy savings của RLE so với Raw; bit density
dùng popcount; prediction-residual regularity là tần suất modal của sai phân
modulo-256. Tổng có trọng số được so với threshold 96. Test giữ nguyên
histogram, mean, variance, entropy và bit density nhưng đổi thứ tự byte để bảo
đảm các tín hiệu tuần tự vẫn có thể tạo change point. Mọi phép tính đều integer
và cửa sổ bị chặn ở 4 KiB.

Các fixed size 256 B, 1/4/16/64/256 KiB được khai báo trong benchmark config.
Beam search, encoded-cost lower bound và early stopping vẫn là search roadmap.
Focused artifact `20260725T051723Z-a5f0fdfc` đo đủ sáu size cùng
ChangePoint/Adaptive trên mười input: ChangePoint 4 KiB dùng 262.012 B,
Adaptive dùng 264.305 B và Fixed 4 KiB dùng 271.155 B.
Byte-aligned plane boundaries đã được áp dụng bởi optimizer ID 5; focused
artifact `20260724T174812Z-583dbb61` đo riêng lựa chọn này trên 14 input.
Focused artifact `20260724T161541Z-70dfeff6` so Adaptive với Recursive depth 4
trên đúng mười input; Recursive giảm 240/266.151 byte nhưng chậm hơn 1,90 lần.
Hai artifact hẹp này không phải bằng chứng cho Full corpus hoặc tham số khác.

## 8. Quy tắc deterministic

Với cùng input, config, executable và dependency versions, encoder phải tạo
cùng byte archive. Các bước song song phải reduce theo khóa ổn định.

Tie-break v0.1 khi \(C\) bằng nhau:

1. Giữa transform candidates, giữ transform được duyệt trước: Identity, Delta,
   XOR, packed BitPlane, padded BitPlane, rồi stride tăng dần.
2. Trong một transform, đường DP có ít segment hơn.
3. Candidate của một segment dùng khóa
   `(model_id, residual_mode_id, residual_coder_id, parameter_bytes,
   residual_bytes)` theo thứ tự từ điển.
4. Nếu tổng DP, số segment và khóa segment cuối vẫn bằng nhau, predecessor đã
   được duyệt trước được giữ.

Objective \(J\) là extension chưa triển khai. Khi bổ sung phải version config
và định nghĩa nó đứng ở đâu trong tie-break thay vì âm thầm đổi archive.

Fit heuristic không được phụ thuộc iteration order của hash map. Random search,
nếu bổ sung, phải dùng PRNG và seed ghi trong config/result; seed không phải
metadata cần cho decode vì archive đã chứa tham số cuối.

## 9. Profile tìm kiếm

- **fast:** Identity/Delta/XOR, fixed 16 KiB, Raw/Constant/Affine/Periodic/
  Recurrence nhỏ và năm custom residual coder v1.
- **balanced:** adaptive anchors 4 KiB, thêm cả packed BitPlane v1, padded
  BitPlane v2 và stride tới 16, toàn model đã đăng ký, polynomial degree 2/3,
  recurrence tới order 4.
- **max:** adaptive anchors 1 KiB, giữ cả hai BitPlane, stride tới 64,
  polynomial degree 4, recurrence encoder tới order 8, period range và control
  points rộng hơn. Max dùng cùng predecessor-count budget 8 như Balanced vì
  boundary set 1 KiB/change-point dày hơn; đây là broader catalog profile,
  không phải strict superset mọi partition của Balanced trên input nhỏ.
  Decoder vẫn chấp nhận order tới 16 như một giới hạn format v1.

Với đúng options chuẩn và input từ 1 MiB, Balanced/Max chuyển sang
`screened_large_v1` để chặn search work nhưng không đổi decoder/container:

- segmentation vẫn là Adaptive; regular anchor hiệu dụng là 64 KiB
  (Balanced) hoặc 256 KiB (Max);
- transform được probe trên tối đa 16 cửa sổ 64 KiB. Identity và
  `BitPlanePadded` luôn được giữ; Balanced/Max encode exact lần lượt tối đa
  3/5 transform finalist;
- mỗi non-Raw model sinh residual AddModulo/XOR toàn segment đúng một lần,
  nhưng proxy tối đa bốn cửa sổ 256 B chọn 2/3 model-mode finalist trước khi
  residual coder được chạy exact trên toàn segment;
- Raw segment và whole-file Raw fallback luôn được encode/so sánh exact;
- Max chạy thêm toàn Balanced screened frontier, giữ Balanced khi hòa, nên
  archive Max lớn không thể lớn hơn archive Balanced trên cùng input.

Proxy transform/model không phải lower bound. Vì vậy “exact MDL” ở policy này
nghĩa là so actual serialized bytes của mọi **finalist đã giữ**, không phải
chứng minh optimum trên toàn catalog trước pruning. Tie-break và sampling chỉ
dùng integer/index xác định; sidecar metrics ghi threshold activation,
requested mode, effective segmentation/anchor, finalist limits và frontier
thắng. Input nhỏ/custom ablation vẫn dùng catalog path exhaustive; CLI
`--exhaustive-search` cho phép tắt screen rõ ràng trên options chuẩn.

Mỗi non-Raw model chỉ predict một lần rồi sinh đồng thời AddModulo và XOR;
thứ tự đánh giá/tie-break vẫn AddModulo trước. Period probe giữ range 256 và
top 6 ở Max nhưng cap tổng ở 65.536 comparison mỗi edge. Khi partial mismatch
tuple đã không thể lọt top-k hiện tại, probe dừng exact; unit test đối chiếu
candidate ranking với exhaustive scoring trên cả ba mode.

Zstd là residual coder thứ sáu nhưng tắt trong cả ba profile bình thường; chỉ một
config/CLI explicit như ablation 14 bật nó. Profile thay đổi search space của
encoder, không thay đổi decoder hoặc format.

## 10. Giới hạn và failure modes

- Dữ liệu ngẫu nhiên, mã hóa hoặc đã nén thường không có model ngắn; raw
  fallback là kết quả đúng.
- Fit tốt không bảo đảm nén tốt do parameter, partition và container overhead.
- Over-segmentation có thể giảm residual nhưng tăng descriptor 36 byte mỗi
  segment.
- Polynomial trên modulo 256 không biểu diễn “smoothness” giống số thực.
- Recurrence/copy có thể lan truyền corruption; checksum segment và SHA-256 đầu
  ra phát hiện nhưng không sửa lỗi.
- Tìm mô tả ngắn nhất tổng quát liên hệ với Kolmogorov complexity và không thể
  giải bằng exhaustive program search thực dụng. MathZip chỉ tối ưu trên một
  model family hữu hạn đã khai báo.

## 11. Bất biến kiểm thử

Mỗi model/residual/transform cần:

- vector encode/decode tham số canonical;
- prediction vector bằng tay, gồm giá trị biên;
- `recover(predict(x), residual(x)) == x`;
- từ chối varint overlong, overflow, truncated payload và trailing bytes;
- giới hạn empty/one-byte/maximum parameter;
- test đa kiến trúc cho mọi arithmetic fixed-point;
- property test `decode(encode(x)) == x`;
- mutation/fuzz test parser không panic, không allocate vô hạn, không loop vô
  hạn.

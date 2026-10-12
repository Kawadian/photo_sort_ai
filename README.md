# Photo Sort AI

Windows／Linuxで写真フォルダを監視し、評価して星1〜5を付けるアプリです。
CLI・日本語WebUIの両方に対応。ローカル評価に加え、任意のOpenAI互換APIも選べます。
Ryzen 5 5500UのCPUによる常時処理と、RTX 3070などのCUDA GPUによる一括処理に対応します。
写真の削除・移動は行いません。低評価の写真を管理ソフト側で絞り込むための選別補助です。

## Windowsで始める

1. Python **3.11（64bit推奨）** をインストールする。python.orgの通常版を使えます。
2. リポジトリをクローン、または「Code → Download ZIP」で取得して展開する。
3. **`install.bat`** を実行。CPU版PyTorch・専用AI・RAW読み込みを導入します。
4. **`start.bat`** を開き、ブラウザーで「評価設定」を開き、対象フォルダを1行ずつ入力して設定を保存する。
5. 「処理時間を計測」で速度を測り、「一括評価」または「フォルダ監視を開始」を押す。

インストーラーの初期設定は **MUSIQ＋NIMA / CPU / 2スレッド / 低優先度**。
初回はインターネット接続と、数GB程度の空き容量が必要になる場合があります。
専用AIの重みは初回利用時に取得します。「モデル準備」で先に取得することもできます。
ローカル方式は取得後の画像評価をローカルで行い、クラウドAI APIやAPIキーを使いません。
`openai`方式を選んだ場合は、指定したAPIへ縮小したJPEGを送信します。
オフライン運用前に、使用するプリセットで準備・評価を一度実行してください。

### 導入パターン

PowerShellでリポジトリのフォルダを開いて実行します。既存の`config.json`は上書きしません。

```powershell
# CPU版：通常の構成
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1

# 軽量解析＋RAW読み込みだけ（PyTorch・専用AIなし）
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Minimal

# RTX 3070用CUDA版。設定画面のdeviceをcudaへ切り替える
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Device cuda

# 独自ONNXをCPUで動かす追加構成
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Minimal -Onnx cpu

# 独自ONNXをDirectMLで動かす追加構成
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Minimal -Onnx directml
```

`-ExecutionPolicy Bypass`はこの起動プロセスにだけ適用し、恒久的なポリシーは変更しません。
CPU版とCUDA版を切り替える場合は、停止後に`.venv`を作り直すと依存の混在を避けられます。

### ExifTool

DNG/JPEG/TIFF内部へ星を書き込むには[ExifTool](https://exiftool.org/)が必要です。
セットアップは未導入でwingetが利用可能な場合、`OliverBetz.ExifTool`の導入を試みます。
失敗した場合は手動で導入し、「ExifToolの場所」に`exiftool.exe`の絶対パスを指定してください。
公式Windows配布版は`exiftool(-k).exe`を`exiftool.exe`へ改名し、付属の`exiftool_files`フォルダも一緒に置きます。
PATH更新後はターミナル／アプリを開き直してください。
JPEG/PNG等を`report`方式で評価するだけならExifToolは不要です。

## Linuxで始める

Python **3.11以上**、Bash、venvが必要です。ブラウザーでWindowsと同じWebUIを使えます。
Ubuntu／Debianでシステム標準Pythonを使う場合の依存導入例：

```bash
sudo apt update
sudo apt install python3 python3-venv libimage-exiftool-perl
```

Python 3.11を別途導入する場合は、そのPythonのvenvパッケージも導入してください。
Fedoraでは`python3`と`perl-Image-ExifTool`等、ディストリビューションに合ったパッケージを使用します。
セットアップスクリプトはシステムパッケージの導入やsudo実行を自動では行いません。

```bash
# CPU版の専用AI＋RAW
./install.sh
./start.sh

# 軽量解析・API用：PyTorchなし
./install.sh --minimal

# CUDA版。WebUIの実行デバイスもcudaへ変更
./install.sh --device cuda

# 独自ONNXをCPUで利用
./install.sh --minimal --onnx cpu

# 設定済みフォルダをCLIで監視（Ctrl+Cで停止）
./watch.sh
```

ZIP展開等で実行権限がない場合は`chmod +x install.sh start.sh watch.sh`、または`bash install.sh`で起動できます。
WebUIはTkinter・デスクトップ・DISPLAYを必要としません。SSHのみのサーバーでも起動できます。
ローカル限定ならSSHポート転送、別端末から直接使うなら後述の外部公開設定を利用してください。
LinuxではDirectMLを使用せず、CPUまたはCUDAを選択します。
CPU版／CUDA版を切り替える場合は、停止後に`.venv`を作り直してセットアップしてください。
状態の既定保存先は`~/.local/share/PhotoSortAI`、設定はリポジトリの`config.json`です。
自動起動が必要な場合は、設定後の`watch.sh`をデスクトップの自動起動やユーザーサービスから実行できます。

## WebUI：ローカル利用・外部公開

既定は`http://127.0.0.1:8765`。`start.bat`／`./start.sh`はWebUIを起動してブラウザーを開きます。
ブラウザーを閉じても評価・監視は続きます。画面の「停止」は評価処理を止め、ターミナルのCtrl+CはWebUIも終了します。
CLIの`scan`／`watch`等も引き続き利用できます。旧Tkinter GUIは削除しました。

| 用途 | オプション | アクセス |
|---|---|---|
| 同じPCのみ | 指定不要 | `127.0.0.1:8765`、他端末から接続不可 |
| SSHポート転送 | `--no-browser` | サーバーはローカル限定のまま。`ssh -L 8765:127.0.0.1:8765 user@server` |
| LAN内の別端末 | `--public --public-url http://サーバーIP:8765 --no-browser` | 全インターフェース待ち受け＋パスワードログイン |
| インターネット公開 | `--public --host 127.0.0.1 --public-url https://photos.example.com --no-browser` | HTTPSリバースプロキシ経由＋パスワードログイン |

外部公開時は12文字以上のパスワードを環境変数`PHOTO_SORT_WEB_PASSWORD`に設定します。
設定がなければ起動を拒否します。パスワードをコマンド引数・設定JSONへ保存しません。
ローカル利用でもこの環境変数を設定するとログインが必要になります。

```powershell
# Windows：LANへ公開。IPは自分のPCのものに変更
$env:PHOTO_SORT_WEB_PASSWORD = "自分で決めた12文字以上のパスワード"
.\start.bat --public --public-url http://192.168.1.50:8765 --no-browser
```

```bash
# Linux：LANへ公開
export PHOTO_SORT_WEB_PASSWORD='自分で決めた12文字以上のパスワード'
./start.sh --public --public-url http://192.168.1.50:8765 --no-browser

# ポートを変更する場合、公開URLのポートも合わせる
./start.sh --public --port 9000 --public-url http://192.168.1.50:9000 --no-browser

# CLIから同じWebUIを起動
.venv/bin/python -m photo_sort_ai --config config.json web --no-browser
```

`--host`は待ち受け先、`--public-url`はブラウザーでアクセスするURLです。
公開URLはHost／Origin検査に使います。公開モードは指定したURLのみを受け付け、別のIPやドメインでは操作できません。
URLのパス以下での設置には対応しないので、専用サブドメインを使用してください。
LAN HTTPは通信を暗号化しません。インターネット公開にはCaddy等でHTTPSを終端します。
たとえばCaddyの設定は次の形です（WebUIを上表のインターネット公開オプションで起動）：

```caddyfile
photos.example.com {
    reverse_proxy 127.0.0.1:8765
}
```

Caddyが別のコンテナ・ホストにある場合は、WebUIの`--host`をそのネットワークから接続できるアドレスに変更します。
その構成ではバックエンドへの通信をプロキシからに制限してください。HTTPS設定時のセッションCookieはSecure属性付きです。
WebUIはWindows／Linux共通のFlask＋Waitressで動かし、開発用サーバーは使用しません。
認証、CSRFトークン、Host／Origin検査、ログイン試行回数制限を実装しています。
ログインした利用者はサーバー上の写真・設定を操作できます。複数ユーザーの権限分離には対応しません。

設定・対象フォルダ・ExifTool／ONNXの場所は**サーバー側**のパスです。
Windows上なら`D:/Photos`、Linux/LXC上なら`/mnt/photos`等を指定します。
評価結果・確率分布・理由・処理時間の確認、縮小プレビュー、CSVと類似候補JSONのダウンロードに対応します。
元のRAWをそのまま配信する機能はありません。プレビューは設定した対象フォルダ内の評価済み写真に限ります。
保存した設定・評価履歴は旧GUIと共通で、移行のために作り直す必要はありません。
評価中の設定保存・履歴リセットは拒否します。停止後に変更してください。

## 評価方式

| プリセット | 処理 | 特徴 |
|---|---|---|
| `light` | 輪郭・明暗・コントラストの統計 | 標準依存のみ。**AIではありません**。粗い選別 |
| `quality` | MUSIQ | pyiqa。画質評価 |
| `aesthetic` | NIMA | pyiqa。見栄えの評価 |
| `balanced` | MUSIQ 65%＋NIMA 35% | pyiqa。画質と見栄えを合算 |
| `topiq` | TOPIQ-NR | pyiqa。参照画像なしの画質評価 |
| `clip` | CLIPIQA | pyiqa。CLIPに基づく品質評価 |
| `laion` | LAION Aesthetic Predictor | pyiqa。美的スコア |
| `technical` | BRISQUE＋NIQE | pyiqa。小さいほどよい指標を反転して合算 |
| `custom` | NRモデルの任意の組み合わせ | JSONで正規化範囲・重みを指定 |
| `ollama` | ローカル画像AIへ文章で指示 | Ollamaを別途導入。CPUでは専用モデルより遅い場合があります |
| `openai` | OpenAI／OpenAI互換の画像対応API | URL・モデル・Chat Completions／Responses・JSON方式等を設定可能 |
| `onnx` | 自分で用意したONNX評価モデル | CPU／DirectML／CUDA。対応ランタイムが必要 |

専用AIの正規化境界は仮の値です。データセットや被写体で採点は偏ります。
星の初期境界は100点換算で **25 / 45 / 65 / 85**。全写真を機械的に五等分する処理はしません。
モデルごとの生スコアもCSVに残るため、自分の基準に合わせて調整できます。
流し撮り、背景ボケ、暗い演出、ノイズと輪郭の違い、思い出の価値の正確な判定は保証しません。

### デバイス

- `cpu`：既定値。CPUスレッド数を制限。5500Uではまず2〜4スレッドで実測してください。
- `cuda`：NVIDIA GPUを明示指定。利用できなければエラーとし、黙ってCPUへ切り替えません。
- `auto`：pyiqaではCUDAが使えればGPU、なければCPU。実際のデバイスを結果のdetailsへ記録します。
- 5500Uの内蔵GPUはpyiqaのCPU/CUDA経路では使用しません。DirectMLは**独自の対応ONNXモデル**を使う場合の選択肢です。MUSIQ等の自動変換機能はありません。
- Ollamaでは`cpu`時に`num_gpu=0`を指定。その他はOllama側のオフロード対応に依存します。

## 画像の読み込み

JPEG/PNG/WebP/TIFFは直接読み込み、EXIFの向きを反映します。RAWは次の方式を選べます。

| `image_source` | 動作 |
|---|---|
| `auto` | 埋め込みプレビュー優先。取得できなければrawpyで現像 |
| `preview` | 埋め込み画像だけ。取得できない写真は失敗として記録 |
| `raw` | rawpyでカメラWB・半解像度のRAW現像 |
| `companion` | 同じフォルダの同名JPEGを評価。なければ失敗 |

プレビュー取得はrawpyを優先し、対応できない場合にExifToolを試します。
プレビュー最小短辺は既定512px、評価用の最大辺は1024px。
縮小画像で評価するため、微妙な合焦を完全に判定する機能ではありません。
埋め込みプレビューは現像設定に追随していない場合があります。現像後の見た目を評価したい場合は書き出したJPEGを対象にしてください。
rawpy非対応の圧縮RAW等は、ExifToolのプレビューや同名JPEG方式を使えます。

## 星の保存方法

| `output` | 動作 |
|---|---|
| `embedded` | DNG/JPEG/TIFFの星と説明欄をExifToolで更新。他の形式はサイドカーへ |
| `sidecar` | XMPファイルへ保存。RAW画素は変更しない |
| `report` | 写真・XMPを変更せず、結果だけDBへ保存 |

- **既存の星を保護する設定が既定です。** 手動の星・拒否評価(-1)・以前に本アプリが付けた星も書き換えません。更新する場合は保護を外すか`--overwrite-rated`を指定します。
- 保護時もAI評価を記録します。結果画面／CSVの`AI_rating`は写真内の既存評価と異なる場合があります。
- DNG/JPEG/TIFFのサイドカーは`写真.dng.xmp`／`写真.jpg.xmp`、その他RAWは`写真.xmp`。同名RAW＋JPEGによる衝突を避けます。**DNGの外部XMPがLightroomで自動読み込みされるとは限りません。DNGの通常の星として扱いたい場合はembeddedを選択してください。**
- 既存XMPの他の項目・現像設定は保持します。壊れたXMPは上書きせずエラーとします。
- バックアップ有効時は更新前ファイル全体を`state_dir/backups`へ保存。対応する`.json`に元の絶対パスが入ります。DNGが多いと容量が増えるため設定で無効化できます。
- RAW画素の再現像・再圧縮はしませんが、DNGコンテナ全体の書き直しとディスクI/Oが発生する場合があります。
- 一部のカメラ由来DNG（例: SonyのARWをAdobe DNG Converter／Lightroomで変換したもの）は、ExifToolが`Error: [minor] Error copying hidden data`で書き込みを拒否します。`exiftool_ignore_minor_errors: true`（WebUIの「ExifToolの軽微なエラーを無視（-m）」、CLIの`--exiftool-ignore-minor`）で`-m`を付けて書き込めます。既定は`false`です。`-m`使用時もRAW画素・プレビュー・既存XMP／現像設定は保持され、書き込み後の星も検証しますが、ExifToolが複製できないメーカー独自の非公開データ（Sonyのhidden data）は新しいファイルへ引き継がれません。
- Lightroom Classicに読み込み済みの写真は「メタデータをファイルから読み込む」で反映。編集済みなら現在の現像設定を先に保存し、他アプリと同じ写真へ同時に書き込まない運用にしてください。クラウド版Lightroomや他ソフトとの同期は自動化していません。

### 説明欄・キャプション

既定では、すべての評価方式の結果をLightroomのキャプション用`XMP-dc:Description`（`dc:description`の`x-default`）へ記録します。
使用モデル、評価方式、算出した星と100点換算スコア、文章のコメント・評価理由、取得できる評価詳細を含めます。
API応答にモデル名があればそれを記録し、指定したモデルIDと異なる場合は両方を残します。モデル名が返らない場合は指定したIDを記録します。
Decisionsは文章のコメントを生成せず、返された選択結果・確率分布・confidenceを掲載します。
ローカル専用AIは各モデルの生スコア・正規化値・重み・デバイス等、ONNXはモデルファイル名・生スコア・実行プロバイダー等を掲載します。
軽量統計にはAIモデルを使っていないことを明記します。API接続先URLやトークン使用量は説明欄へ含めません。

WebUIの「評価設定」→「キャプションの保存」、または`caption_policy`で次を選べます。

| 設定 | 動作 |
|---|---|
| `preserve`（既定） | 説明欄が空なら保存。既存のキャプションや言語別説明・IPTCの説明文は保護 |
| `overwrite` | 標準言語の説明を今回の評価結果へ置き換え。他言語の説明は保持 |
| `off` | 星だけ保存し、説明欄は変更しない |

星の保護と説明欄の保護は独立しています。既存の星を保護したまま、空の説明欄へ評価内容を追加できます。
説明欄の保存先は星と共通（DNG/JPEG/TIFF内部、またはXMPサイドカー）。`report`ではどちらも変更しません。
保存方法や説明欄設定だけを変更した場合は、条件が一致する過去の評価を再利用でき、APIへの再送信は不要です。
過去のAIキャプションも既定では保護されます。新しいモデルや指示で説明を更新する場合は`overwrite`を選んでください。
既存のバックアップ設定が適用され、書き込み後に星とキャプションを検査します。日本語・絵文字・改行に対応します。

CLIの例（`scan`／`watch`／`benchmark`で指定可能。計測では写真を変更しません）：

```bash
.venv/bin/python -m photo_sort_ai scan --caption-policy overwrite
```

Lightroom Classicのキャプションへの対応と読み込み操作は[Adobe Communityの説明](https://community.adobe.com/questions-675/comment-field-991491)も参照してください。
Lightroomのカタログを直接更新する機能はありません。DNGの説明をLightroomで通常のメタデータとして扱う場合は`embedded`を使ってください。

## 監視・履歴

- ポーリングで監視。ローカル／Windowsから見える共有フォルダを同じ方式で扱います。
- サイズ・更新時刻が`settle_seconds`間変わらないことを観測し、画像準備後・評価後にも変更を検査します。コピー完了の絶対保証ではありません。取り込み側で一時拡張子から最終名へリネームできる場合はその方式が適しています。
- 1枚ずつ処理し、停止後は次の写真へ進みません。実行中のモデル初期化／評価の終了を待ち、書き込み前に停止を確認します。
- 自分の星保存後の状態を記録し、監視イベントが再発しても評価を繰り返しません。
- SQLiteに結果・設定の署名・失敗履歴を保存。再起動後は再走査して未処理から続行します。
- 画像準備／モデル／星境界の変更は再評価。保存方式だけの変更なら条件が合う結果を再利用します。
- 失敗は既定最大3回、待ち時間を増やして再試行。監視は次の走査で、一括処理は次回起動で再試行します。
- 同じ`state_dir`の処理はOSロックで重複実行を防ぎます。同じ写真を処理する複数アプリで別々のstate_dirを使わないでください。
- 設定変更の反映には停止→再開が必要。Windowsのスリープ中は処理せず、復帰後に稼働中のアプリが続行します。

結果の既定保存先は`%LOCALAPPDATA%\PhotoSortAI`。
`results.sqlite3`、`app.log`、`benchmark.json`、`backups/`を作ります。写真や結果をGitHubへ送信しません。
API方式は指定先へ評価用JPEGとプロンプトを送信し、RAWファイル・ファイル名・EXIFはリクエストに含めません。

### サインイン時の自動監視

対象フォルダを保存してから実行。現在のユーザーのスタートアップへショートカットを作ります。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\startup.ps1

# 自動起動を解除
powershell -NoProfile -ExecutionPolicy Bypass -File .\startup.ps1 -Remove
```

画面を表示せず`watch`を実行します。停止する場合はタスクマネージャーで該当`pythonw.exe`を終了してください。
WebUIから監視を重ねて開始した場合は二重処理を防ぐエラーになります。
コンソール表示で監視する場合は`watch.bat`を使えます。

## コマンドライン

Windowsでセットアップ済みの例。Linuxでは`.venv/bin/python -m photo_sort_ai ...`で同じサブコマンドを使えます。
WebUIは`web`サブコマンドで起動します。旧`gui`サブコマンド・Tkinter実装は削除しました。

```powershell
# --configはサブコマンドの前に指定
.\.venv\Scripts\python.exe -m photo_sort_ai --config config.json init --folder "D:\Photos" --preset balanced
.\.venv\Scripts\python.exe -m photo_sort_ai doctor
.\.venv\Scripts\python.exe -m photo_sort_ai scan
.\.venv\Scripts\python.exe -m photo_sort_ai watch

# 写真を変更せず、30枚で実測
.\.venv\Scripts\python.exe -m photo_sort_ai benchmark --limit 30

# 評価をやり直し、すでに付いている星も更新
.\.venv\Scripts\python.exe -m photo_sort_ai scan --force --overwrite-rated

# 結果のみ保存。評価方式・デバイスを一時変更
.\.venv\Scripts\python.exe -m photo_sort_ai scan --output report --preset topiq --device cpu

# 条件が一致する評価を再利用し、DNG等へ星を書き込む
.\.venv\Scripts\python.exe -m photo_sort_ai scan --output embedded --preset topiq

# 失敗の試行履歴を解除して再試行
.\.venv\Scripts\python.exe -m photo_sort_ai scan --retry-errors
.\.venv\Scripts\python.exe -m photo_sort_ai export results.csv
.\.venv\Scripts\python.exe -m photo_sort_ai duplicates similar.json --distance 4
```

`--folder`を複数指定すると、その実行の対象フォルダを置き換えます。CLIの上書き指定は設定JSONへ保存しません。
`scan --limit N`は新たに処理成功する写真の上限、`benchmark --limit N`は計測を試みる上限。
一括処理の失敗は終了コード1、成功は0。監視の写真単位エラーはログに記録し、他の写真を続行します。

## 実測・類似写真

ベンチマークはモデル準備時間、初回1枚、以降の平均・中央値、1,000枚／1万枚の推定時間を記録します。
**画像準備＋評価の計測です。星保存・安定待ち・将来のディスク競合は含みません。**
1枚だけ成功した場合はその1枚を平均に使用。5500Uの未測定値を固定速度として扱いません。

類似候補は64bit dHashで検索し、近い候補を連結したグループと、その中の最高得点写真をJSONへ出力します。
単純な画像等で誤一致があり、連結によってグループ両端の写真同士は離れている場合があります。
連写判定・完全な重複保証ではなく、星の引き下げや削除も行いません。

## カスタム設定

`config.json`で既定プリセットは`preset`から復元します。モデル・重みを独自に指定する場合は`preset: "custom"`にしてください。
相対フォルダ・state_dir・ONNXモデルパスは設定JSONの場所が基準です。[設定例](examples/)もあります。

```json
{
  "folders": ["D:/Photos"],
  "preset": "custom",
  "backend": "pyiqa",
  "device": "cpu",
  "metrics": [
    {"name": "topiq_nr", "minimum": 0, "maximum": 1, "weight": 0.6},
    {"name": "nima", "minimum": 3, "maximum": 7, "weight": 0.4}
  ],
  "thresholds": [25, 45, 65, 85],
  "output": "report"
}
```

正規化範囲は例示の調整値です。生スコア分布を見て変更してください。
`lower_better`省略時はpyiqaのモデル情報を使用。正規化後に重み付き平均します。
FRモデル、FIDなど、単一写真からスカラー評価値を返さない方式は対象外です。

### Ollama

[Ollama](https://docs.ollama.com/)を導入し、画像対応モデルを取得します。例：`ollama pull qwen3-vl:4b`。
`preset: "ollama"`にして`ollama_model`、`ollama_prompt`を変更できます。
APIは**ループバックHTTPだけ**に限定し、プロキシ・リダイレクトで外部へ写真を送りません。
rating(整数1〜5)とreasonを検査し、不正な返答を保存しません。
0〜100点は星からの変換表示で、専用モデルの連続スコアとは比較できません。

### OpenAI／OpenAI互換API

追加SDKは不要です。Windowsは`install.ps1 -Minimal`、Linuxは`./install.sh --minimal`でも利用できます。
WebUIの評価方法を「OpenAI互換API」に変更し、「API接続」画面で次を設定してから保存・実行してください。

| 設定 | 内容 |
|---|---|
| API URL | 既定は`https://api.openai.com/v1`。任意のベースURL、または`/chat/completions`／`/responses`／`/decisions`で終わる完全URL |
| API方式 | `chat_completions`／`responses`／`decisions`（OpenAI形式）／`openrouter_decisions`（OpenRouter形式）。指定先が対応する方式を明示的に選ぶ |
| モデル | 指定先で利用できる**画像対応モデルの実際のID**。初期値は空欄 |
| JSON方式 | `json_object`、厳密スキーマの`json_schema`、形式指定を省略する`none` |
| APIキー | 環境変数名を設定。既定は`OPENAI_API_KEY`。WebUIで起動中だけ有効なキーを入力することも可能 |
| 同時リクエスト数 | `api_concurrency`。既定1（1枚ずつ）、最大16。下記「同時リクエスト」参照 |
| 画像詳細 | `auto`／`low`／`high`／`omit`。対応しない互換サーバーでは`omit` |
| 出力トークン上限 | 既定2048。Chatでは`max_completion_tokens`、Responsesでは`max_output_tokens`。0で省略 |
| store指定 | 既定false。互換サーバーで未対応なら`omit`。この指定の扱いはサービスに依存 |
| 追加パラメータ | JSON設定の`openai_extra_body`、またはWebUI。temperature・reasoning_effort等を指定先に合わせて設定 |

URLへの認証情報・クエリ・フラグメントの埋め込みには対応しません。認証はBearerヘッダーです。
APIキーそのものは設定JSON・結果・ログへ保存しません。WebUI入力はサーバープロセスの環境変数へ設定し、再起動後は再入力が必要です。
WebUIから入力するキーの変数名は`OPENAI_API_KEY`／`OPENROUTER_API_KEY`等、`_API_KEY`で終わる名前を使います。他の名前はサーバー起動前に環境変数で設定できます。
キー不要のローカル互換APIは環境変数名を空欄にできます。変数名を指定してキーが未設定なら、送信前にエラーにします。

```powershell
# Windows：このPowerShellから起動するアプリだけにキーを渡す
$env:OPENAI_API_KEY = "自分のAPIキー"
.\start.bat
```

```bash
# Linux：このターミナルから起動するアプリへキーを渡す
export OPENAI_API_KEY='自分のAPIキー'
./start.sh
```

[OpenAI公式用の設定例](examples/openai-api.json)と[ローカル互換API用の設定例](examples/openai-compatible-local.json)があります。
モデルID・写真フォルダ・URLを自分の環境に合わせて変更してください。
WebUIを使わずに実行する場合は、設定例JSONを編集するかCLIの一時上書きを使えます。Linuxの例：

```bash
.venv/bin/python -m photo_sort_ai scan --preset openai \
  --folder "$HOME/Photos" --api-url https://api.openai.com/v1 \
  --api-model YOUR_VISION_MODEL_ID --api-mode responses --api-json-mode json_schema --output report
```

Windowsでも同じオプションを使えます。`--api-key-env`は環境変数名で、キーの値は引数へ渡しません。
古い互換サーバーが`max_tokens`だけに対応する場合は、出力上限を0にし追加パラメータで`{"max_tokens":512}`を指定できます。
未対応パラメータでエラーになった場合、別形式への自動再送はせず設定変更後に再試行します。

Chat／Responsesの返答はrating(整数1〜5)とreasonを検査し、不正値・拒否・途中終了を保存しません。
HTTPエラーは状態コードを記録し、既存の監視の再試行設定に従います。リダイレクトは追従しません。
API方式でも一括・監視・DNG保存・キャッシュ再利用を同じように使えます。
モデル・URL・プロンプト等の変更は再評価を引き起こし、キーの変更だけでは再評価しません。
APIのベンチマークは実際に指定先へ写真を送信して評価します。`prepare-models`は設定とキー取得の確認だけでAPI呼び出しを行いません。
返答のusageを結果details・CSVへ保存します。0〜100点は星からの変換値です。

公式仕様：[画像入力](https://developers.openai.com/api/docs/guides/images-vision) / [構造化出力](https://developers.openai.com/api/docs/guides/structured-outputs)

#### プロンプトのお気に入り

WebUIの「API接続」→「お気に入りのプロンプト」で、評価指示を名前付きで複数管理できます。

1. 「評価プロンプト」に本文を書き、「お気に入りの名前」を入力して「新規登録」。
2. 「使用するプロンプト」で登録済みの名前を選ぶと、本文を入力欄へ読み込みます。
3. 「設定を保存」で、お気に入りの変更と実際に使うプロンプトを確定します。

本文の編集後は「選択したお気に入りを更新」、名前の入力後は「名前変更」、不要なものは「削除」を使えます。
同じ名前を新規登録しても上書きせず、更新操作で明示的に変更します。「標準プロンプト」で初期の評価指示へ戻せます。
削除しても入力中の本文は残ります。設定保存前の操作は未確定なので、再読み込みすると最後に保存した状態へ戻ります。

お気に入りは`config.json`の`openai_prompt_presets`（名前→本文）、現在の評価指示は従来の`openai_prompt`に保存します。
再起動後も残り、既存の設定には空のお気に入り一覧を補います。既存のカスタムプロンプトは変更しません。
名前は1〜80文字、本文は50,000文字以内、100件まで登録できます。
Chat Completions／Responses／OpenAI Decisions／OpenRouter Decisionsのすべてで、選択した本文を使います。
星・JSON等のAPI方式に必要な出力指示は従来通り付加します。お気に入り一覧全体をAPIへ送ることはありません。
一覧の追加・名前変更だけでは写真を再評価せず、使用する本文を変更したときに評価キャッシュが変わります。

CLIでは保存済みのお気に入りを、その実行だけ選べます（Linuxの例。Windowsでも同じオプション）：

```bash
.venv/bin/python -m photo_sort_ai scan --preset openai --api-prompt-preset '人物'
```

`scan`／`watch`／`benchmark`で利用でき、設定ファイルの現在のプロンプトは書き換えません。

#### 同時リクエスト（並列評価）

API方式とOllamaでは`api_concurrency`（WebUIは「API同時処理数」、CLIは`--api-concurrency`）で写真を並列に評価できます。
既定は1で従来どおり1枚ずつです。1〜16を指定でき、API側の待ち時間が長いほど効果があります。
並列化するのは画像準備とAPI呼び出しだけで、星の書き込み・SQLiteの保存・ログ出力は1本のスレッドで行うため、結果の形式と保存内容は1枚ずつの場合と同じです。

HTTP 429／503が返った場合は`Retry-After`（なければ2秒からの指数バックオフ、最大120秒）だけ**全ワーカーをまとめて待機**させ、`api_rate_limit_retries`回（既定2）まで再送します。
それでも失敗した写真は従来どおりエラーとして記録し、`retry_seconds`・`max_retries`の設定で後から再試行します。
Ollamaは既定で1件ずつ処理するため、並列の効果を得るにはOllama側の`OLLAMA_NUM_PARALLEL`も設定してください。

#### Decisions API

モデル名だけを変更しても、Decisionsの返答はChat／Responsesでは読み取れません。
カスタムエンドポイントではモデル情報を取得できない場合もあるため、自動判別せずWebUIの「API方式」またはCLIの`--api-mode`で切り替えます。
モデル固有の分岐はなく、指定した形式と画像入力に対応するDecisionsモデルを利用できます。

| 接続先 | API URL | API方式 | モデル例 |
|---|---|---|---|
| OpenRouter | `https://openrouter.ai/api/alpha/decisions` | `openrouter_decisions` | `openai/gpt-6-luna-decisions` |
| OpenAI | `https://api.openai.com/v1` | `decisions` | `gpt-6-luna` |

[OpenRouter Decisions設定例](examples/openrouter-decisions.json)を利用できます。APIキーの環境変数名は`OPENROUTER_API_KEY`です。
LinuxでもWindowsでも、WebUIの同じAPI設定から利用できます。

Decisionsでは5つの星を選択肢として送信し、`answers`の`rating`に返った`choice`（1〜5）を星に保存します。
自由な文章の理由は生成しません。返った選択結果・確率分布・confidenceは結果details／CSVの`decision`に保存します。
JSON方式・出力トークン上限・store設定はDecisionsには送信しません。画像詳細はOpenRouter形式のみ送信します。
この方式でも一括評価・監視・DNG保存・キャッシュを利用でき、不正な選択・拒否・未完了は保存しません。

仕様参照：[OpenAI Decisions](https://developers.openai.com/api/docs/guides/decisions) / [OpenRouter画像入力](https://openrouter.ai/docs/guides/community/multimodal-decisions)

### ONNX

`pip install '.[onnx]'`（CPU）、`pip install '.[directml]'`（DirectML）、`pip install '.[onnx-cuda]'`（CUDA）のいずれか一つを導入します。
複数のONNX Runtime配布を同じ環境に混在させないでください。
`onnx_path`を指定し、`onnx_provider`、`onnx_size`、`onnx_layout`、`onnx_mean/std`、`onnx_minimum/maximum`をモデルに合わせます。
対応は**単一のfloat32画像入力→単一スカラー出力**。画像を正方形へリサイズし、RGB正規化後NCHW/NHWCで入力します。
複数入力・パッチ分割・NIMAの10段階確率分布などは別の変換アダプタが必要です。自動変換機能はありません。

## 開発・検証

```text
python -m pip install -e .
python -m unittest discover -s tests -v
python -m compileall -q src
```

GitHub ActionsでWindows／Ubuntuの基本テスト、ExifToolのXMP・画素保持、両OSのWebUI実HTTP起動・軽量セットアップとスクリプト構文を確認します。
APIテストはローカルの模擬HTTPサーバーを使用し、外部APIへの送信は行いません。
実モデルの取得・CPU実行は`Optional CPU model smoke`ワークフローを手動実行できます。
同ワークフローでは、テスト用ONNXのNCHW/NHWC入力と実際のCPU推論も確認します。
また、合成したCFAセンサーDNGのLibRaw読み込み・RAW現像・星書き込み後のRAW画素保持を確認します。
テスト用DNGはTIFFにDNGタグを付けたコンテナで、全カメラのセンサーRAW現像・互換性の検証ではありません。

参照：[IQA-PyTorch](https://github.com/chaofengc/IQA-PyTorch) / [モデル設定](https://github.com/chaofengc/IQA-PyTorch/blob/main/pyiqa/default_model_configs.py) / [ExifTool](https://exiftool.org/) / [PyTorch](https://pytorch.org/get-started/locally/)
モデル・重み・ライブラリのライセンスは各配布元の条件に従います。

# Photo Sort AI

Windows／Linuxで写真フォルダを監視し、評価して星1〜5を付けるアプリです。
CLI・日本語GUIの両方に対応。ローカル評価に加え、任意のOpenAI互換APIも選べます。
Ryzen 5 5500UのCPUによる常時処理と、RTX 3070などのCUDA GPUによる一括処理に対応します。
写真の削除・移動は行いません。低評価の写真を管理ソフト側で絞り込むための選別補助です。

## Windowsで始める

1. Python **3.11（64bit推奨）** をインストールする。Tkinterを含むpython.orgの通常版を使えます。
2. リポジトリをクローン、または「Code → Download ZIP」で取得して展開する。
3. **`install.bat`** を実行。CPU版PyTorch・専用AI・RAW読み込みを導入します。
4. **`start.bat`** を開き、「対象フォルダ」を追加して設定を保存する。
5. 「時間を実測（20枚）」で速度を測り、「一括評価」または「監視開始」を押す。

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

Python **3.11以上**、Bash、venvが必要です。GUIはWindowsと同じTkinterの画面です。
Ubuntu／Debianでシステム標準Pythonを使う場合の依存導入例：

```bash
sudo apt update
sudo apt install python3 python3-venv python3-tk libimage-exiftool-perl fonts-noto-cjk xdg-utils
```

Python 3.11を別途導入する場合は、そのPythonのvenv・Tkinterパッケージも導入してください。
Fedoraでは`python3-tkinter`と`perl-Image-ExifTool`等、ディストリビューションに合ったパッケージを使用します。
セットアップスクリプトはシステムパッケージの導入やsudo実行を自動では行いません。

```bash
# CPU版の専用AI＋RAW
./install.sh
./start.sh

# 軽量解析・API用：PyTorchなし
./install.sh --minimal

# CUDA版。GUIの実行デバイスもcudaへ変更
./install.sh --device cuda

# 独自ONNXをCPUで利用
./install.sh --minimal --onnx cpu

# 設定済みフォルダをCLIで監視（Ctrl+Cで停止）
./watch.sh
```

ZIP展開等で実行権限がない場合は`chmod +x install.sh start.sh watch.sh`、または`bash install.sh`で起動できます。
設定画面を表示するにはデスクトップのDISPLAY／Wayland-XWayland環境が必要です。
SSHのみの環境ではCLIを使用します。CLIはTkinter・GUIディスプレイを必要としません。
LinuxではDirectMLを使用せず、CPUまたはCUDAを選択します。
CPU版／CUDA版を切り替える場合は、停止後に`.venv`を作り直してセットアップしてください。
状態の既定保存先は`~/.local/share/PhotoSortAI`、設定はリポジトリの`config.json`です。
自動起動が必要な場合は、設定後の`watch.sh`をデスクトップの自動起動やユーザーサービスから実行できます。

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
| `embedded` | DNG/JPEG/TIFFの`XMP-xmp:Rating`だけをExifToolで更新。他の形式はサイドカーへ |
| `sidecar` | XMPファイルへ保存。RAW画素は変更しない |
| `report` | 写真・XMPを変更せず、結果だけDBへ保存 |

- **既存の星を保護する設定が既定です。** 手動の星・拒否評価(-1)・以前に本アプリが付けた星も書き換えません。更新する場合は保護を外すか`--overwrite-rated`を指定します。
- 保護時もAI評価を記録します。結果画面／CSVの`AI_rating`は写真内の既存評価と異なる場合があります。
- DNG/JPEG/TIFFのサイドカーは`写真.dng.xmp`／`写真.jpg.xmp`、その他RAWは`写真.xmp`。同名RAW＋JPEGによる衝突を避けます。**DNGの外部XMPがLightroomで自動読み込みされるとは限りません。DNGの通常の星として扱いたい場合はembeddedを選択してください。**
- 既存XMPの他の項目・現像設定は保持します。壊れたXMPは上書きせずエラーとします。
- バックアップ有効時は更新前ファイル全体を`state_dir/backups`へ保存。対応する`.json`に元の絶対パスが入ります。DNGが多いと容量が増えるため設定で無効化できます。
- RAW画素の再現像・再圧縮はしませんが、DNGコンテナ全体の書き直しとディスクI/Oが発生する場合があります。
- Lightroom Classicに読み込み済みの写真は「メタデータをファイルから読み込む」で反映。編集済みなら現在の現像設定を先に保存し、他アプリと同じ写真へ同時に書き込まない運用にしてください。クラウド版Lightroomや他ソフトとの同期は自動化していません。

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
GUIから監視を重ねて開始した場合は二重処理を防ぐエラーになります。
コンソール表示で監視する場合は`watch.bat`を使えます。

## コマンドライン

Windowsでセットアップ済みの例。Linuxでは`.venv/bin/python -m photo_sort_ai ...`で同じサブコマンドを使えます。
GUIにはTkinterとデスクトップ環境が必要です。

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
GUIの評価方法を「OpenAI互換API」に変更し、「API設定」タブで次を設定してから保存・実行してください。

| 設定 | 内容 |
|---|---|
| API URL | 既定は`https://api.openai.com/v1`。任意のベースURL、または`/chat/completions`／`/responses`／`/decisions`で終わる完全URL |
| API方式 | `chat_completions`／`responses`／`decisions`（OpenAI形式）／`openrouter_decisions`（OpenRouter形式）。指定先が対応する方式を明示的に選ぶ |
| モデル | 指定先で利用できる**画像対応モデルの実際のID**。初期値は空欄 |
| JSON方式 | `json_object`、厳密スキーマの`json_schema`、形式指定を省略する`none` |
| APIキー | 環境変数名を設定。既定は`OPENAI_API_KEY`。GUIで起動中だけ有効なキーを入力することも可能 |
| 画像詳細 | `auto`／`low`／`high`／`omit`。対応しない互換サーバーでは`omit` |
| 出力トークン上限 | 既定2048。Chatでは`max_completion_tokens`、Responsesでは`max_output_tokens`。0で省略 |
| store指定 | 既定false。互換サーバーで未対応なら`omit`。この指定の扱いはサービスに依存 |
| 追加パラメータ | JSON設定の`openai_extra_body`、またはGUI。temperature・reasoning_effort等を指定先に合わせて設定 |

URLへの認証情報・クエリ・フラグメントの埋め込みには対応しません。認証はBearerヘッダーです。
APIキーそのものは設定JSON・結果・ログへ保存しません。GUI入力はそのプロセスの環境変数へ設定し、再起動後は再入力が必要です。
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
GUIを使わずに実行する場合は、設定例JSONを編集するかCLIの一時上書きを使えます。Linuxの例：

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

#### Decisions API

モデル名だけを変更しても、Decisionsの返答はChat／Responsesでは読み取れません。
カスタムエンドポイントではモデル情報を取得できない場合もあるため、自動判別せずGUIの「API方式」またはCLIの`--api-mode`で切り替えます。
モデル固有の分岐はなく、指定した形式と画像入力に対応するDecisionsモデルを利用できます。

| 接続先 | API URL | API方式 | モデル例 |
|---|---|---|---|
| OpenRouter | `https://openrouter.ai/api/alpha/decisions` | `openrouter_decisions` | `openai/gpt-6-luna-decisions` |
| OpenAI | `https://api.openai.com/v1` | `decisions` | `gpt-6-luna` |

[OpenRouter Decisions設定例](examples/openrouter-decisions.json)を利用できます。APIキーの環境変数名は`OPENROUTER_API_KEY`です。
LinuxでもWindowsでも、GUIの同じAPI設定から利用できます。

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

GitHub ActionsでWindows／Ubuntuの基本テスト、ExifToolのXMP・画素保持、両OSのGUI起動・軽量セットアップとスクリプト構文を確認します。
APIテストはローカルの模擬HTTPサーバーを使用し、外部APIへの送信は行いません。
実モデルの取得・CPU実行は`Optional CPU model smoke`ワークフローを手動実行できます。
同ワークフローでは、テスト用ONNXのNCHW/NHWC入力と実際のCPU推論も確認します。
また、合成したCFAセンサーDNGのLibRaw読み込み・RAW現像・星書き込み後のRAW画素保持を確認します。
テスト用DNGはTIFFにDNGタグを付けたコンテナで、全カメラのセンサーRAW現像・互換性の検証ではありません。

参照：[IQA-PyTorch](https://github.com/chaofengc/IQA-PyTorch) / [モデル設定](https://github.com/chaofengc/IQA-PyTorch/blob/main/pyiqa/default_model_configs.py) / [ExifTool](https://exiftool.org/) / [PyTorch](https://pytorch.org/get-started/locally/)
モデル・重み・ライブラリのライセンスは各配布元の条件に従います。

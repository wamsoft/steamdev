# steamctl — headless Steam Deck (SteamOS devkit) control

GUI なしで Steam Deck を制御するツール。ローカルのプロジェクトをビルドして
Deck に送り込み、起動・観測・リモートデバッグまでをコマンドラインだけで回せる。
エージェント (Claude 等) からの自動運転を想定した設計。

```
steamctl -d <deck-ip> project -p <プロジェクト> ship linux
# → ビルド → 資材構築 → Deck へ転送 → Steam 登録 → 起動 まで 1 コマンド
```

---

# ユーザーガイド

## 1. 必要なもの

| 要件 | 備考 |
|---|---|
| Windows ホスト + [uv](https://docs.astral.sh/uv/) | `scoop install uv` / `winget install astral-sh.uv` 等 |
| Steam Deck (開発者モード有効) | 設定 → システム → 開発者モードを有効化 |
| WSL2 + docker | **Linux ビルドを行う場合のみ** (Ubuntu 内に docker を導入) |
| Python 3.11+ | uv が自動で用意するので通常は意識不要 |

## 2. 初期セットアップ (ホスト側、1 回だけ)

```powershell
# CLI をグローバル導入 (editable: steamctl リポジトリを git pull すれば更新反映)
uv tool install --editable <steamctl リポジトリ絶対パス> --with zeroconf
uv tool update-shell        # ~/.local/bin を PATH へ (初回のみ、要シェル再起動)

steamctl --version          # 動作確認
```

> editable インストールが前提。`deckbuild/` (Linux ビルド環境) の解決が
> リポジトリ実体を参照するため、通常のインストールでは動かない。

### Deck とのペアリング

```powershell
steamctl discover                    # LAN 上の Deck を探す (IP がわかるなら省略可)
steamctl -d <deck-ip> register       # 初回のみ。Deck 側で承認ダイアログが出る
steamctl -d <deck-ip> sync-utils     # デバイス側ヘルパスクリプト転送 (初回必須)
steamctl -d <deck-ip> status         # 疎通確認 (デバイス状態が JSON で返れば OK)
```

公式 SteamOS Devkit Client でペアリング済みの Deck なら **register は不要**
(同じ SSH 鍵を共有するため、そのまま接続できる)。

毎回 `-d` を打ちたくない場合は環境変数で固定: `$env:STEAMCTL_DEVICE = "<deck-ip>"`

### Linux ビルド環境 (Linux ネイティブ版を作る場合のみ)

WSL2 の Ubuntu に docker を入れた上で:

```bash
# WSL 内で。Valve 公式 sniper SDK ベースのビルドイメージを作成 (初回のみ、数 GB DL)
bash <steamctl>/deckbuild/deckbuild.sh image
```

## 3. プロジェクト側の手順 (プロジェクトごと)

### 3-1. deckproject.toml を書く

プロジェクトのルートに配置。ターゲット (linux / windows) ごとに
「どうビルドするか / 何を送るか / どう起動するか」を書く:

```toml
[project]
gameid = "mygame"                     # デプロイ時は mygame_linux / mygame_windows になる

[targets.linux.build]
kind = "deckbuild"                    # sniper コンテナで Linux ビルド
preset = "x64-linux"                  # CMake プリセット名

[targets.linux.stage]
copy = [["bin/x64-linux/Release", "."], ["data", "data"]]
script = ""                           # 独自の資材構築があればコマンドを書く

[targets.linux.deploy]
command = "./mygame data"             # Deck 上での起動コマンド

[targets.windows.build]
kind = "shell"                        # 既存のビルドフローをそのまま書く
command = "make PRESET=x64-windows prebuild build install"

[targets.windows.stage]
copy = [["bin/x64-windows/Release", "."], ["data", "data"]]

[targets.windows.deploy]
command = "mygame.exe data"
settings = { steam_play = "1", compat_tool = "proton-stable" }   # Proton で実行
```

全キーの説明は [docs/DECKPROJECT.md](docs/DECKPROJECT.md)。
実運用例は krkrz_dev の `deckproject.toml` を参照。

### 3-2. (任意) プロジェクト .venv — Python から steamctl を使う場合

検証スクリプト等で `Device` / `LocalForward` を import したいプロジェクトだけ:

```powershell
cd <プロジェクト>
uv venv .venv
uv pip install --python .venv/Scripts/python.exe -e <steamctl リポジトリ> zeroconf
```

`.venv` があると `steamctl project` の build/stage スクリプトは自動で
その venv の python を使う (PATH 先頭に `.venv/Scripts` が注入される)。
`.venv` は .gitignore に入れておくこと。

### 3-3. 動かす

```powershell
steamctl project -p <プロジェクト> show                    # 定義の確認
steamctl -d <deck> project -p <プロジェクト> ship linux    # ビルド→転送→起動 一括
steamctl -d <deck> project -p <プロジェクト> ship windows
```

個別ステップ: `build` / `stage` / `deploy [--start]`。
オプション: `--clean-stage` (stage 作り直し) / `--clean` (デバイス側の余分を削除)。

## 4. 日常操作の早見表

```powershell
steamctl status                     # デバイス状態
steamctl list                       # 入っているタイトル一覧
steamctl run <gameid>               # 起動 / 前面化
steamctl delete <gameid>            # 削除
steamctl screenshot -o shot.png     # 画面キャプチャ
steamctl logs --out ./devkit-logs   # Steam ログ・クラッシュダンプ回収
steamctl exec -- <コマンド>          # SSH ワンライナー
steamctl exec --stream -- tail -F ~/.local/share/Steam/logs/console-linux.txt
steamctl shell                      # 対話シェル
steamctl tunnel -L 18899:8899       # ポートフォワード (アプリの REPL 等へ)
```

開発サイクルの詳細・リモートデバッグ (gdbserver / Proton+msvsmon / krkrz -replweb
REPL 駆動)・実機で確認済みのハマりどころ一覧は
**[docs/WORKFLOW.md](docs/WORKFLOW.md)** を参照。

## 5. うまくいかないとき

| 症状 | 見る場所 |
|---|---|
| デプロイしたのに起動しない | [docs/WORKFLOW.md](docs/WORKFLOW.md) のハマりどころ表 (未インストール Proton 指定が定番) |
| Linux バイナリが即死 | 同上 (共有ライブラリの soname / LD_LIBRARY_PATH) |
| ビルドが通らない | [deckbuild/README.md](deckbuild/README.md) (コンパイラ差し替え等) |
| 接続できない | `steamctl discover` → `info` → `status` の順に切り分け |

---

# 開発者向け情報

## ライブラリ API

```python
from steamctl import Device, deploy, DeploySpec, LocalForward

dev = Device("192.168.1.30")            # or Device.from_name("steamdeck")
dev.sync_utils()                        # デバイス側スクリプト転送 (初回必須)
print(dev.status().raw["steam_status"])

deploy(dev, DeploySpec(
    gameid="mygame",
    local_dir=r"D:/build/mygame",
    argv=["mygame.sh -console"],        # コマンドライン全体を 1 文字列で
    env={"PROTON_LOG": "1"},
    settings={"compat_tool": "proton-stable", "steam_play": "1"},
    start_after=True,
))

# ログをリアルタイム転送
for line in dev.stream("tail -F ~/.local/share/Steam/logs/console-linux.txt"):
    print(line)

# アプリの REPL ポートへトンネル (逆方向は RemoteForward)
with LocalForward(dev.ssh.get_transport(), 18899, "127.0.0.1", 8899):
    ...  # 127.0.0.1:18899 がデバイス側 8899 につながる
```

低レベル API: `dev.run()` (SSH 実行) / `dev.run_json()` / `dev.rsync()` /
`dev.sftp()` / `dev.rpc()` (Steam クライアント RPC) / `dev.screenshot()`。

## 仕組みの要点

- **プロトコル**: mDNS (`_steamos-devkit._tcp`) で発見 → HTTP :32000 でペアリング
  (公開鍵 POST) → 以降は SSH (コマンド実行 / rsync 転送 / トンネル) と、デバイス内
  `steam.pipe` IPC 経由の Steam クライアント制御。全容は
  [docs/PROTOCOL.md](docs/PROTOCOL.md) (公式クライアント解析結果)
- **SSH 鍵は公式クライアントと共有**
  (`%LOCALAPPDATA%\steamos-devkit\steamos-devkit\devkit_rsa`)。どちらで
  ペアリングしても相互に使える
- **rsync/ssh** は Windows では公式クライアント同梱の cygwin ツール
  (なければ msys2) を自動検出
- **Linux ビルド** は Valve 公式 steamrt sniper SDK (Debian 11 / glibc 2.31)
  コンテナ。SteamOS ネイティブでも Steam Linux Runtime コンテナでも動く
  バイナリになる。コンパイラは SDK 同梱の gcc-14 が既定。詳細は
  [deckbuild/README.md](deckbuild/README.md)
- **依存**: 必須は paramiko のみ。zeroconf は extras `[discovery]`
  (mDNS 探索用、IP 直指定なら不要)。Python 3.11+ (tomllib)

## リポジトリ構成

```
src/steamctl/
  keys.py        # devkit RSA 鍵 (公式クライアントと共有)
  discovery.py   # mDNS (_steamos-devkit._tcp) 探索 (zeroconf は遅延 import)
  device.py      # Device: HTTP ペアリング / SSH 実行 / rsync / スクリーンショット等
  deploy.py      # タイトルデプロイフロー (prepare-upload → rsync → create-shortcut)
  project.py     # deckproject.toml 駆動の build/stage/deploy パイプライン
  tunnel.py      # SSH ポートフォワード (正方向 / 逆方向)
  cli.py         # steamctl CLI
deckbuild/       # sniper SDK コンテナによる Linux ビルド環境 (Dockerfile + ラッパ)
docs/            # プロトコル仕様 / 定義リファレンス / ワークフロー
```

## ドキュメント索引

| ドキュメント | 内容 |
|---|---|
| [docs/PROTOCOL.md](docs/PROTOCOL.md) | devkit 制御プロトコル仕様 (公式クライアント解析結果) |
| [docs/DECKPROJECT.md](docs/DECKPROJECT.md) | deckproject.toml リファレンス |
| [docs/WORKFLOW.md](docs/WORKFLOW.md) | 開発サイクル・デバッグ手順・ハマりどころ |
| [deckbuild/README.md](deckbuild/README.md) | Linux ビルド環境の詳細 |

## 検証済み状況

実機 (Steam Deck / SteamOS 3.8.16) で検証済み:

- 基本機能: mDNS 探索、既存鍵での SSH 接続、sync-utils、status、
  deploy → run → delete、screenshot、exec --stream、logs 回収、
  SSH トンネル正方向 (-L) / 逆方向 (-R)
- パイプライン: krkrz を Windows ビルド (Proton 10.0) / Linux ビルド
  (sniper SDK コンテナ → ネイティブ実行) の両方で Deck 上に動作確認
- REPL 連携: krkrz -replweb をトンネル経由で駆動 (TJS 評価 / キー入力注入 /
  エンジン内キャプチャ回収) を確認

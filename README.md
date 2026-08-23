# steamctl — headless Steam Deck (SteamOS devkit) control

Valve 公式 SteamOS Devkit Client (MIT) のプロトコルを解析し、GUI なしで
Steam Deck / Steam Frame を制御するためのライブラリ + CLI。
エージェント (Claude 等) からの自動デプロイ・リモート操作を想定した設計。

## ドキュメント

| ドキュメント | 内容 |
|---|---|
| [docs/PROTOCOL.md](docs/PROTOCOL.md) | devkit 制御プロトコル仕様 (公式クライアント解析結果) |
| [docs/DECKPROJECT.md](docs/DECKPROJECT.md) | deckproject.toml リファレンス (プロジェクト定義) |
| [docs/WORKFLOW.md](docs/WORKFLOW.md) | 日常の開発サイクル・デバッグ手順・ハマりどころ |
| [deckbuild/README.md](deckbuild/README.md) | Steam Deck 向け Linux ビルド環境 (sniper SDK コンテナ) |

実機 (Steam Deck / SteamOS 3.8.16) で検証済み:

- 基本機能: mDNS 探索、既存鍵での SSH 接続、sync-utils、status、
  deploy → run → delete、screenshot、exec --stream、logs 回収、
  SSH トンネル正方向 (-L) / 逆方向 (-R)
- パイプライン: krkrz を Windows ビルド (Proton 10.0) / Linux ビルド
  (sniper SDK コンテナ → ネイティブ実行) の両方で Deck 上に動作確認

## 特徴

- 公式クライアントと**同じ SSH 鍵を再利用** (`%LOCALAPPDATA%\steamos-devkit\...\devkit_rsa`)。
  公式クライアントでペアリング済みのデバイスにはそのまま接続できる。
- Windows では公式クライアント同梱の cygwin ssh/rsync (なければ msys2) を自動検出。
- mDNS 探索 / IP 直接指定の両対応。
- SSH トンネル (正方向/逆方向) でアプリ側 REPL・ソケットサービスに接続可能。

## セットアップ

```
pip install -e .          # または pip install paramiko zeroconf して PYTHONPATH=src
```

## CLI

```
steamctl discover                       # LAN 上の devkit を mDNS 探索
steamctl -d 192.168.1.30 register       # 初回ペアリング (デバイス側で承認)
steamctl -d 192.168.1.30 info           # /properties.json
steamctl -d 192.168.1.30 sync-utils     # デバイス側ヘルパスクリプトを転送 (初回必須)
steamctl -d 192.168.1.30 status         # デバイス状態 (JSON)

# デプロイ & 起動
steamctl -d 192.168.1.30 deploy --gameid mygame --dir D:/build/mygame \
    --command "mygame.sh -console" --clean --start

steamctl -d 192.168.1.30 run mygame     # 起動
steamctl -d 192.168.1.30 list           # インストール済み一覧
steamctl -d 192.168.1.30 delete mygame

# リモート実行・観測
steamctl -d 192.168.1.30 exec -- uname -a
steamctl -d 192.168.1.30 exec --stream -- tail -F ~/.local/share/Steam/logs/console-linux.txt
steamctl -d 192.168.1.30 shell          # 対話シェル
steamctl -d 192.168.1.30 screenshot -o shot.png
steamctl -d 192.168.1.30 logs --out ./devkit-logs

# アプリ側 REPL / socket サービスへのトンネル
steamctl -d 192.168.1.30 tunnel -L 9222:9222          # host:9222 -> deck:9222
steamctl -d 192.168.1.30 tunnel -R 8000:8000          # deck:8000 -> host:8000
```

デバイス指定は `-d` の代わりに環境変数 `STEAMCTL_DEVICE` でも可。

## ライブラリ

```python
from steamctl import Device, deploy, DeploySpec, LocalForward

dev = Device("192.168.1.30")            # or Device.from_name("steamdeck")
dev.register()                          # 初回のみ
dev.sync_utils()                        # デバイス側スクリプト転送 (初回必須)

print(dev.status().raw["steam_status"])

deploy(dev, DeploySpec(
    gameid="mygame",
    local_dir=r"D:/build/mygame",
    argv=["mygame.sh -console"],        # コマンドライン全体を 1 文字列で
    env={"PROTON_LOG": "1"},
    settings={"compat_tool": "proton-experimental", "steam_play": "1"},
    start_after=True,
))

# ログをリアルタイム転送
for line in dev.stream("tail -F ~/.local/share/Steam/logs/console-linux.txt"):
    print(line)

# アプリの REPL ポートへトンネル
with LocalForward(dev.ssh.get_transport(), 9222, "127.0.0.1", 9222):
    ...  # 127.0.0.1:9222 がデバイス側 9222 につながる
```

## プロジェクト定義駆動パイプライン (`steamctl project`)

プロジェクト側に `deckproject.toml` を置くと、ビルド (Win/Linux) → 資材構築
(stage) → Deck へのデプロイ → 起動までを定義駆動で回せる:

```
steamctl project -p <dir> show              # 定義確認
steamctl project -p <dir> build linux       # sniper コンテナで Linux ビルド (deckbuild/)
steamctl project -p <dir> stage linux       # .deckstage/<target> に配布物を構築
steamctl -d <deck> project -p <dir> deploy linux --start
steamctl -d <deck> project -p <dir> ship linux    # build+stage+deploy+起動 一括
```

定義例 (krkrz の場合):

```toml
[project]
gameid = "krkrz"                      # デプロイ時は krkrz_linux / krkrz_windows になる

[targets.linux.build]
kind = "deckbuild"                    # deckbuild/ の sniper コンテナビルド
preset = "x64-linux"
cmakeopt = "-DKRKRZ_USE_SJIS=YES"

[targets.linux.stage]
copy = [["bin/x64-linux/Release", "."], ["src/core/data", "data"]]
script = ""                           # 独自資材構築が要るならコマンドを書く

[targets.linux.deploy]
command = "./krkrz64 data"

[targets.windows.build]
kind = "shell"                        # 既存のビルドフローをそのまま書く
command = "make PRESET=x64-windows prebuild build install"

[targets.windows.stage]
copy = [["bin/x64-windows/Release", "."], ["src/core/data", "data"]]

[targets.windows.deploy]
command = "krkrz64.exe data"
settings = { steam_play = "1", compat_tool = "proton-experimental" }
```

stage の `script` / build の `command` は cwd=プロジェクトルート、環境変数
`STEAMCTL_PROJECT_DIR` / `STEAMCTL_STAGE_DIR` / `STEAMCTL_TARGET` 付きで実行される。

## Linux ビルド環境 (deckbuild/)

Steam Deck 互換の Linux バイナリを作るための Docker (WSL2) ビルド環境。
Valve 公式 steamrt sniper SDK (glibc 2.31) ベース。詳細は
[deckbuild/README.md](deckbuild/README.md)。

## 構成

```
src/steamctl/
  keys.py        # devkit RSA 鍵 (公式クライアントと共有)
  discovery.py   # mDNS (_steamos-devkit._tcp) 探索
  device.py      # Device: HTTP ペアリング / SSH 実行 / rsync / スクリーンショット等
  deploy.py      # タイトルデプロイフロー (prepare-upload → rsync → create-shortcut)
  project.py     # deckproject.toml 駆動の build/stage/deploy パイプライン
  tunnel.py      # SSH ポートフォワード (正方向 / 逆方向)
  cli.py         # steamctl CLI
deckbuild/       # sniper SDK コンテナによる Linux ビルド環境 (Dockerfile + ラッパ)
docs/            # プロトコル仕様 / 定義リファレンス / ワークフロー
```

# steamctl — headless Steam Deck (SteamOS devkit) control

Valve 公式 SteamOS Devkit Client (MIT) のプロトコルを解析し、GUI なしで
Steam Deck / Steam Frame を制御するためのライブラリ + CLI。
エージェント (Claude 等) からの自動デプロイ・リモート操作を想定した設計。

解析結果のプロトコル仕様は [docs/PROTOCOL.md](docs/PROTOCOL.md) を参照。

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

## 構成

```
src/steamctl/
  keys.py        # devkit RSA 鍵 (公式クライアントと共有)
  discovery.py   # mDNS (_steamos-devkit._tcp) 探索
  device.py      # Device: HTTP ペアリング / SSH 実行 / rsync / スクリーンショット等
  deploy.py      # タイトルデプロイフロー (prepare-upload → rsync → create-shortcut)
  tunnel.py      # SSH ポートフォワード (正方向 / 逆方向)
  cli.py         # steamctl CLI
docs/PROTOCOL.md # プロトコル仕様 (解析結果)
```

# Steam Deck 開発ワークフロー

steamdev 一式を使った日常の開発サイクル。エージェント (Claude) が自律的に
回す場合もこの手順に従う。

## 0. 前提 (初回セットアップ)

1. Deck 側: 開発者モード有効化 + devkit サービス有効 (公式手順どおり)
2. ホスト側: `pip install paramiko zeroconf` (または `pip install -e .`)
3. ペアリング: 公式 SteamOS Devkit Client でペアリング済みなら**不要**
   (同じ鍵を共有)。新規なら `steamdev -d <ip> register` → Deck 側で承認
4. `steamdev -d <ip> sync-utils` — デバイス側ヘルパスクリプト転送 (初回必須)
5. Linux ビルドする場合: WSL2 + docker + `deckbuild/deckbuild.sh image`
6. プロジェクトに `deckproject.toml` を書く (docs/DECKPROJECT.md)

デバイス指定は毎回 `-d <ip>` か、環境変数 `STEAMDEV_DEVICE` で固定。

## 1. 基本サイクル

```bash
# 疎通確認 (Deck の電源が入っているか / どの状態か)
steamdev discover                 # LAN 探索 (mDNS)
steamdev status                   # OS/セッション/Steam クライアント状態

# ビルド → 送り込み → 起動 (1 コマンド)
steamdev project -p <proj> ship linux
steamdev project -p <proj> ship windows

# ビルド済みで送るだけなら
steamdev project -p <proj> deploy linux --start

# 起動・終了・切り替え
steamdev run <gameid>                             # 再起動 (前面化)
steamdev exec -- "pkill -f '<gameid>/exe名[.]ext'" # 終了 (下記の注意参照)
steamdev list                                     # 入っているタイトル一覧
steamdev delete <gameid>                          # 削除
```

## 2. 観測・デバッグ

```bash
# 画面確認 (エージェントの目)
steamdev screenshot -o shot.png

# プロセス確認
steamdev exec -- "pgrep -af devkit-game/<gameid>"

# タイトルの stdout (Steam ログ経由) をリアルタイム追跡
steamdev exec --stream -- "tail -F ~/.local/share/Steam/logs/console-linux.txt"

# ログ・クラッシュダンプ一括回収
steamdev logs --out ./devkit-logs

# 対話シェル / 任意コマンド
steamdev shell
steamdev exec -- "ls ~/devkit-game"
```

### アプリ側 REPL / ソケットサービスに接続する

アプリが REPL・HTTP・ソケットサービスを持つ場合 (krkrz の -replweb 等)、
localhost バインドでも SSH トンネルで届く:

```bash
# ホスト:18080 → Deck:8080 (アプリの待ち受けポート)
steamdev tunnel -L 18080:8080
# 逆方向: Deck 上のアプリ → ホスト側サービス
steamdev tunnel -R 18888:8000
```

ライブラリからは `LocalForward` / `RemoteForward` (with 文対応)。

### 実例: krkrz の -replweb を REPL 駆動する (実機検証済み)

krkrz を `-replweb` 付きで起動すると Deck の 127.0.0.1:8899 に HTTP+SSE サーバが
立つ (localhost バインドなのでトンネル必須)。

1. 起動コマンドに `-replweb` を含めてデプロイ:
   `deploy.command = "./krkrz data -replweb -loglevel=info"`
   **注意: 起動引数はショートカット登録時に Steam 側へ焼き込まれる**ため、
   デバイス上の `<gameid>-argv.json` を書き換えても反映されない。引数変更は
   再デプロイ (create-shortcut のやり直し) で行う。
2. トンネル: `steamdev tunnel -L 18899:8899` (ライブラリなら `LocalForward`)
3. TJS 評価: `POST http://127.0.0.1:18899/cmd` に式を body で送る。
   **応答 body は受理ステータスのみ ("0"/"1")。評価結果は SSE
   `GET /events` にログ (`{"cls":"result","text":"=> ..."}`) として流れる**
   (直近 2000 行のバックログ付きなので後から接続しても拾える)。
4. Agent API で操作・観測:
   - `Agent.keyPress(13)` / `Agent.click(x,y)` — 入力注入 (実入力と同経路)
   - `Agent.captureScreen('/tmp/cap.png')` — エンジン内キャプチャ (次フレーム保存)
   - PNG は sftp で回収して目視確認
5. アプリ側の追加 API は `WebServer.register` で公開できる (skill krkrz-webui 参照)

検証済みフロー: 評価 (`1+2*3 => 7`) → Enter 注入でデモ起動 → captureScreen →
ESC 注入で復帰、まで全て動作。

### リモートデバッガ

- Linux ネイティブ: settings `gdbserver = "1"` → `<deck-ip>:2345` に gdb 直結
- Proton (Windows exe): settings `steam_play_debug = "1"` + VS リモートデバッガ
  (msvsmon が LAN 直結。セットアップは公式クライアントの機能を利用)

## 3. ハマりどころ (実機で確認済み)

| 症状 | 原因と対処 |
|---|---|
| deploy 後に起動しない (無反応) | `compat_tool=proton-experimental` 等が Deck に未インストール。compat_log.txt に "not installed"。`proton-stable` を使う |
| `pkill -f <パターン>` が exit 127 で失敗 | パターンが自分のリモートシェルにマッチして自爆。`krkrz64[.]exe` のようにブラケットを挟む |
| Linux バイナリが即死 | 同梱 .so の soname リンク欠落 / RPATH なし。stage script で soname コピーを作り `env = { LD_LIBRARY_PATH = "." }` |
| Proton 初回起動が遅い | prefix 生成中。数十秒待つのが正常 |
| `run-game` 直後にプロセスが見えない | Steam 側の処理に数秒〜十数秒ラグ。ポーリングで待つ |
| 新旧タイトルが両方画面に残る | 前に起動したものが裏に生存。pkill で明示終了 |
| ビルドが `_mm256_*` 未定義で失敗 | sniper SDK 既定の gcc-10 が古い。deckbuild は gcc-14 を既定にしている (DECKBUILD_CC/CXX で変更可) |

## 4. エージェント運用の指針

- 起動確認は「プロセス存在 + スクリーンショット」の 2 点セットで行う
  (プロセスがいても前面とは限らない。gamescope は最後に起動したものを前面化)
- 状態を変える操作 (delete / pkill / セッション再起動) の前に `status` と
  `pgrep` で現状を確認する
- `steamos-get-status` は呼ぶたびに無線省電力を切る副作用がある (Deck のみ)。
  高頻度ポーリングには `exec -- pgrep ...` を使う
- テスト後は `delete` でタイトルを消し、`~/devkit-game/` にゴミを残さない

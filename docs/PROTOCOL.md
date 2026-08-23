# SteamOS Devkit 制御プロトコル仕様

Valve 公式 SteamOS Devkit Client の解析結果。

ライセンス: 上流リポジトリ (gitlab.steamos.cloud/devkit/steamos-devkit) の
トップレベル LICENSE が MIT (Copyright (c) 2017-2022 Valve Software inc.,
Collabora Ltd)。配布バンドル内で同文ヘッダを持つのは devkit_client/__init__.py
のみで、devkit-utils 等は上流リポジトリの LICENSE がカバーする形。
クライアント本体は Python 製で、GUI (imgui) は薄いラッパに過ぎず、
実際の制御は以下の 3 層で完結している。

```
[ホスト]                              [Steam Deck]
 mDNS browse ──────────────────────►  avahi: _steamos-devkit._tcp を広告
 HTTP :32000 ──────────────────────►  steamos-devkit-service (ペアリング用)
 SSH :22 (専用RSA鍵) ──────────────►  sshd → ~/devkit-utils/* スクリプト実行
   └ rsync over ssh (ファイル転送)         └ steam.pipe → Steam クライアント IPC
```

## 1. デバイス発見 (mDNS / DNS-SD)

- サービスタイプ: `_steamos-devkit._tcp.local.`
- SRV レコードのポート = devkit HTTP サービスのポート (既定 **32000**)
- TXT レコード:
  - `txtvers` : `1` 以外は非互換として無視
  - `login` : SSH ログインユーザ (通常 `deck`)
  - `settings` : JSON 文字列 (`{"sshd": "1", ...}`)
  - `devkit1` : レガシーエントリポイント (shlex 分割)
- IP 直接指定でも全機能が使える (mDNS はオプション)。

## 2. ペアリング (devkit HTTP サービス, ポート 32000)

認証前にアクセスできる唯一の口。デバイス側の `steamos-devkit-service` が処理する。

| エンドポイント | メソッド | 内容 |
|---|---|---|
| `/properties.json` | GET | `{"login": "deck", "devkit1": [...], "settings": "<JSON文字列>"}`。`settings` は **JSON の中に JSON 文字列** が入る二重構造。パースは `strict=False` 推奨 |
| `/login-name` | GET | レガシー。ログインユーザ名をプレーンテキストで返す |
| `/register` | POST | SSH 公開鍵の登録 (ペアリング) |

### /register の仕様

- `Content-Type: text/plain`、ボディは 1 行:

```
ssh-rsa <base64> devkit-client:<user>@<host> 900b919520e4cf601998a71eec318fec
```

- 末尾の固定トークン (`MAGIC_PHRASE`) は簡易的な相互確認用。デバイス側の
  `ssh-approve-key` がこれを検証し、必要なら Steam クライアント上に承認ダイアログを出す。
  そのためタイムアウトは 30 秒に設定する。
- 403 応答のボディには行単位で JSON `{"error": ...}` が混ざることがある。
- 鍵はパスフレーズなし RSA 2048。保存場所 (公式クライアントと共有可能):
  - Windows: `%LOCALAPPDATA%\steamos-devkit\steamos-devkit\devkit_rsa`
  - Linux: `~/.config/steamos-devkit/devkit_rsa`
  - **同じ場所を使えば公式クライアントで登録済みのデバイスへ即 SSH 可能**(本ライブラリはこの方式)。

## 3. SSH チャネル

- 認証: 上記 RSA 鍵、ユーザは `properties.json` の `login`。
- コマンド実行は paramiko 相当で十分。`StrictHostKeyChecking=no` 運用。
- ファイル転送は rsync over ssh。Windows では cygwin 系 ssh/rsync が必須
  (Windows 標準 OpenSSH は rsync のトランスポートにならない)。
  公式クライアント同梱の `windows-client/cygroot/bin/{ssh,rsync,cygpath}.exe`
  か msys2 を利用する。
- rsync の基本形:

```
rsync -av -z --chmod=Du=rwx,Dgo=rx,Fu=rwx,Fog=rx \
  -e "<ssh> -o StrictHostKeyChecking=no -i <devkit_rsa>" \
  [--delete --delete-excluded --delete-delay]  # クリーンアップロード
  [--update]      # リモート側の新しいファイルを残す
  [--checksum]    # チェックサム検証
  <filter args>   # --include/--exclude
  <local>/ <login>@<ip>:<remote>/
```

## 4. デバイス側スクリプト (~/devkit-utils)

**前提: クライアントが自分で rsync アップロードする** (公式クライアントは接続確認のたびに
`devkit-utils/` を `~/devkit-utils` に同期する)。つまりデバイス側 API の実体は
「クライアントが送り込んだ Python スクリプト」であり、自前ツールでも同じものを送り込めばよい。

| スクリプト | 役割 | 入出力 |
|---|---|---|
| `steamos-get-status --json` | デバイス状態一括取得 | JSON (下記) |
| `steamos-prepare-upload --gameid X` | アップロード先作成 | `{"user": "deck", "directory": "/home/deck/devkit-game/X"}` |
| `steam-client-create-shortcut --parms <json>` | タイトル登録 | `{"success": ...}` / `{"error": ...}` |
| `steamos-list-games` | インストール済みタイトル | `[{"gameid": ...}]` |
| `steamos-delete` | タイトル削除 / Steam リセット | `--delete-title X` / `--delete-all-titles` / `--reset-steam-client` |
| `steam-devkit-rpc <cmd> k=v...` | Steam クライアント汎用 RPC | 下記 |
| `steamos-set-steam-client` | 起動する Steam クライアント切替 | トランポリン `~/devkit-game/devkit-steam` を生成 |
| `steamos-dump-controller-config` | コントローラ設定ダンプ | `/tmp/config_*.vdf` に出力 |
| `deckard-capture` | Steam Frame 用スクリーンショット | `{"success": true, "output": "/tmp/...png"}` |

### steamos-get-status --json の主なフィールド

- `hostname`, `os_name`, `os_version`, `os_info` (/etc/os-release 全体)
- `is_deckard` : Steam Frame (VR) かどうか。多数のコードパスが分岐
- `session_status` : `gamescope` / `plasma-x11` / ... 、`session_select` : セッション切替コマンド名
- `steam_status` : `SteamStatus.NOT_RUNNING|OS|OS_DEV|SIDELOADED|ERROR`
- `steam_current_args` : 実行中 steam プロセスの argv (/proc から取得)
- `cef_debugging_enabled` : steamwebhelper がポート 8080 を listen しているか
- `steam_launch_flags` : `$XDG_RUNTIME_DIR/steam/env/*` の内容
- `user_password_is_set`, `renderdoc_*` など
- **IP アドレスは含まれない** (mDNS / 指定アドレスから得る)。ホスト側で
  `machine.address` として管理する。

## 5. Steam クライアント IPC (デバイス内)

デバイス上で Steam クライアントを制御する仕組み。SSH 経由で
`steam-devkit-rpc` を叩くことでホストから間接的に利用できる。

- パイプ: `~/.steam/steam.pipe` へ 1 行書き込み:

```
devkit-1 steam://devkit-1/<session_token>/<command>/?<urlencoded params>
```

- `session_token` は `~/.steam/steam.token` の内容。
- 応答はファイルシステム経由: Steam が `<response>.lock` を作成し、
  `<response>` (成功) または `<response>.error` (失敗) を書いて `.lock` を消す。
  タイムアウト 5 秒。
- 既知コマンド: `create-shortcut`, `delete-shortcut`, `list-shortcuts`,
  `run-game` (`gameid=X`), `dumpcontrollerconfig`。
- 前提条件: Steam クライアント稼働中 (`~/.steam/steam.pid` の生存確認)。

## 6. タイトルデプロイ (Update Title) の全手順

1. `steamos-prepare-upload --gameid X` → 転送先 `/home/deck/devkit-game/X` とユーザ名
2. rsync でローカルビルドをアップロード (§3 の形式、filter/--delete 任意)
3. `steam-client-create-shortcut --parms '<json>'`:

```json
{
  "gameid": "X",
  "directory": "/home/deck/devkit-game/X",
  "argv": ["<起動コマンドライン全体を 1 つの文字列で>"],
  "env": {"PROTON_LOG": "1"},
  "settings": {
     "steam_play": "1",
     "compat_tool": "proton-experimental",
     "gdbserver": "1",
     "steam_play_debug": "1",
     "steam_play_debug_version": "2022"
  },
  "force_appid": "480"
}
```

   - `argv` は「配列」だが公式クライアントは**コマンドライン全体を 1 要素**として渡す。
     環境変数プレフィクス (`PROTON_LOG=1 game.exe -w`) はホスト側で分離して `env` に入れる。
   - `force_appid` を指定するとアップロード先に `steam_appid.txt` を書く。
   - `compat_tool` の値: `proton-stable`, `proton-experimental`,
     `SteamLinuxRuntime` (scout), `SteamLinuxRuntime_sniper`,
     `SteamLinuxRuntime_4`, `SteamLinuxRuntime_4-arm64`, `lepton`
   - デバイス側では `~/devkit-game/X-argv.json`, `X-env.json`, `X-settings.json` に保存され、
     Steam に「Devkit Game」ショートカットとして登録される。
4. 起動: `steam-devkit-rpc run-game gameid=X`

gameid の制約: `^[A-Za-z_][A-Za-z0-9_.]+$`。
予約名 `steam`, `steamdeckard`, `steamvr`, `steamvrdeckard` は Steam クライアント自体の
サイドロードに使われる特殊 ID。

## 7. リモートデバッグ

### gdbserver (ネイティブ Linux タイトル)
- タイトル設定 `gdbserver=1` → 起動ラッパが `gdbserver` 経由で起動
- Steam クライアント自体: `steamos-set-steam-client --gdbserver` →
  トランポリンに `export DEBUGGER="gdbserver 0.0.0.0:2345"`
- **ポート 2345 に LAN から直結** (トンネル不要)。

### msvsmon (Proton / Windows タイトル, Visual Studio)
- ホストの VS Remote Debugger (`vswhere` で検出) を
  `~/devkit-msvsmon/msvsmon{2017,2019,2022}` へ rsync
- Proton バグ回避で `webservices.dll` (x86/x64) を上書き転送
- `msvsmoninstall.py` をデバイス側で実行、VS から LAN 経由で直接アタッチ
- タイトル設定: `steam_play_debug=1|2` (2 = アタッチ待ち。要 Child Process
  Debugging Power Tool 拡張)、`steam_play_debug_version=<year>`

## 8. その他の操作 (SSH ワンライナー)

| 操作 | コマンド |
|---|---|
| スクリーンショット (Deck) | `DISPLAY=:0 xprop -root -f GAMESCOPECTRL_DEBUG_REQUEST_SCREENSHOT 32c -set GAMESCOPECTRL_DEBUG_REQUEST_SCREENSHOT <1-4>` → `/tmp/gamescope*.png` を sftp 取得 (1=ベースプレーン, 2=全レイヤ, 3=合成後, 4=画面バッファ) |
| セッション再起動 | `steamos-session-select gamescope` (Frame: `systemctl --user restart steam`) |
| CEF リモートデバッグ有効化 | `touch ~/.steam/steam/.cef-enable-remote-debugging` + セッション再起動 → `http://<ip>:8081` (DevTools フロント)。内部ポートは 8080 |
| Steam 起動環境変数 | `$XDG_RUNTIME_DIR/steam/env/<KEY>` にファイルとして書く (steam-launch-wrapper が読む固定セットのみ有効: `PROTON_LOG`, `ENABLE_VULKAN_RENDERDOC_CAPTURE` 等) |
| パフォーマンスオーバーレイ | `mangohudctl set no_display true|false` |
| フレームタイム記録 | `mangohudctl set log_session true|false` → `~/mangoapp_*.csv` を rsync 回収 |
| GPU トレース | `gpu-trace --capture --no-gpuvis -o /tmp/xxx-trace.zip` (失敗時 `gpu-trace --start` → 5 秒待ち → 再試行) |
| RGP キャプチャ | `touch /tmp/rgp.trigger` → `/tmp/*.rgp` をポーリング取得 |
| RenderDoc リプレイサーバ | `RENDERDOC_TEMP=/home/deck renderdoccmd remoteserver -d` |
| ログ回収 | rsync ダウンロード: `~/.local/share/Steam/logs`, `/tmp/dumps`, `~/steam-*.log` (Proton) |
| 再起動 | `/usr/bin/steamos-polkit-helpers/steamos-reboot-now` |

## 9. ホスト側連携ポート

- 公式 GUI は `127.0.0.1:32010` で HTTP を待ち受け (`ClientAPI`)、ビルドシステムから
  `POST /post_event` (`{"type":"build","status":"success","name":"<gameid>"}`) を受けて
  自動アップロードを行う。`GET /selected_devkit`, `GET /ssh_key_path`,
  `GET /title_settings` もある。CI 連携の参考になる。

## 10. アプリ側 REPL / ソケットサービスとの連携 (本ライブラリでの拡張)

公式クライアントにはポートフォワード機構がない (デバッガは LAN 直結)。
自前アプリの REPL・ソケットサービス・localhost バインドのサービスに対しては
SSH トンネルが最も確実:

- direct-tcpip チャネルでローカルポート → デバイスポート (REPL へ接続)
- リバースフォワードでデバイスポート → ホストポート (アプリからホストの Claude 側
  サービスへ接続させる)
- 標準入出力の転送は `ssh.exec_command` のチャネルをそのままストリームとして
  読み書きする (行単位ストリーミング / インタラクティブ両対応)。
- タイトルの stdout はリモートには直接出ない。Steam のログ
  (`~/.local/share/Steam/logs/console-linux.txt` 等) に落ちるので
  `tail -F` の SSH ストリーミングで実質的なリアルタイム転送ができる。

## 11. 制約・注意

1. `~/devkit-utils` の同期を先に行わないとデバイス側スクリプト呼び出しは全て失敗する。
2. Steam クライアント経由の操作 (create-shortcut / run-game 等) は Steam 稼働中のみ。
3. `steamos-get-status` は呼ぶたびに無線省電力を無効化する副作用がある (Deck のみ)。
4. HTTP プロキシ設定があると LAN 上のデバイスへの HTTP が壊れる。公式はプロキシを無効化している。
5. ペアリングの安全性は MAGIC_PHRASE + デバイス側承認 UI 頼み。信頼できる LAN でのみ使う。

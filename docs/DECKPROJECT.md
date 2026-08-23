# deckproject.toml リファレンス

`steamctl project` コマンド群が読むプロジェクト定義ファイル。プロジェクトの
ルートに `deckproject.toml` を置くと、ビルド → 資材構築 (stage) → Steam Deck
へのデプロイ → 起動までを定義駆動で実行できる。

```
steamctl project -p <dir> show                  # 定義の確認
steamctl project -p <dir> build <target>        # ビルドのみ
steamctl project -p <dir> stage <target>        # 資材構築のみ (.deckstage/<target>)
steamctl -d <deck> project -p <dir> deploy <target> [--start]   # stage + デプロイ
steamctl -d <deck> project -p <dir> ship <target>               # build+stage+deploy+起動
```

オプション: `--clean-stage` (stage ディレクトリを作り直す)、`--clean` (デバイス側の
余分なファイルを削除する rsync --delete アップロード)。

## 全体構造

```toml
[project]
gameid = "mygame"          # ベース ID。省略時はフォルダ名。
                           # ターゲット別 ID は "<gameid>_<target>" になる
                           # (targets.<t>.deploy.gameid で個別指定も可)

[targets.<name>]           # ターゲットは任意個 (例: linux / windows)
  [targets.<name>.build]   # 省略可: 省略時 build はスキップ
  [targets.<name>.stage]   # 省略可だが copy か script どちらかは実質必須
  [targets.<name>.deploy]  # deploy する場合は command が必須
```

gameid の制約: `^[A-Za-z_][A-Za-z0-9_.]+$`。
`steam` / `steamdeckard` / `steamvr` / `steamvrdeckard` は予約済み。

## [targets.*.build]

| キー | 意味 |
|---|---|
| `kind` | `"deckbuild"` = 同梱の sniper コンテナビルド / `"shell"` = 任意コマンド (既定) |
| (deckbuild) `preset` | CMake プリセット名。既定 `x64-linux` |
| (deckbuild) `build_type` | `Release` / `Debug`。既定 `Release` |
| (deckbuild) `cmakeopt` | configure への追加引数 (例 `-DKRKRZ_USE_SJIS=YES`) |
| (shell) `command` | 実行コマンド。文字列ならシェル経由、配列なら argv 直接実行 |

- `deckbuild` は WSL2 の docker で steamrt sniper SDK コンテナを起動し
  `cmake --preset` → build → install を回す (deckbuild/README.md 参照)。
  成果物はソース側 `bin/<preset>/<build_type>/` に出る。
- `shell` は既存のビルドフロー (make 等) をそのまま書く。Windows ターゲットの
  VS ビルドなどは各自の環境前提なのでこちらを使う。

## [targets.*.stage]

デプロイ対象フォルダ `.deckstage/<target>/` を合成するルール。

| キー | 意味 |
|---|---|
| `copy` | `[["コピー元", "コピー先"], ...]` の配列。パスはプロジェクトルート相対。コピー元はファイル/フォルダ/glob。コピー先は stage ディレクトリ相対 (`"."` = 直下) |
| `script` | copy 適用後に実行する任意コマンド。独自の資材構築 (変換・生成・圧縮等) はここで行う |

`script` は cwd=プロジェクトルート、以下の環境変数付きで実行される:

- `STEAMCTL_PROJECT_DIR` — プロジェクトルート絶対パス
- `STEAMCTL_STAGE_DIR` — stage ディレクトリ絶対パス
- `STEAMCTL_TARGET` — ターゲット名

copy は増分 (上書き) コピー。完全に作り直したいときは `--clean-stage`。

## [targets.*.deploy]

| キー | 意味 |
|---|---|
| `command` | **必須**。デバイス上での起動コマンドライン全体を 1 つの文字列で。cwd はアップロード先 (`~/devkit-game/<gameid>/`) |
| `env` | 起動時環境変数の辞書 (例 `{ LD_LIBRARY_PATH = "." }`) |
| `settings` | Steam ショートカットに渡す設定辞書 (下記) |
| `gameid` | ターゲット別 gameid の明示指定 |
| `appid` | `steam_appid.txt` に書く AppID (Steamworks API を使う場合) |
| `clean_upload` | true でデバイス側の余分なファイルを削除して同期 |
| `filter` | rsync フィルタ引数の配列 (例 `["--exclude=*.pdb"]`) |

### settings に書ける主なキー

| キー | 値 | 意味 |
|---|---|---|
| `steam_play` | `"1"` / `"0"` | Proton (Steam Play) で実行するか |
| `compat_tool` | 下表 | 実行環境の指定 |
| `gdbserver` | `"1"` | gdbserver 経由で起動 (ポート 2345 に LAN 直結) |
| `steam_play_debug` | `"1"` / `"2"` | msvsmon デバッグ (1=起動, 2=アタッチ待ち) |
| `steam_play_debug_version` | `"2022"` 等 | 使用する VS リモートデバッガ |

compat_tool の値:

| 値 | 実行環境 |
|---|---|
| `proton-stable` | 安定版 Proton (インストール済みの最新安定版に解決) |
| `proton-experimental` | Proton Experimental (**未インストールだと無言で起動失敗**するので注意) |
| `SteamLinuxRuntime_sniper` | Steam Linux Runtime 3.0 コンテナ (Linux バイナリを Steam 配布と同一環境で実行) |
| (指定なし) | Linux バイナリを SteamOS ネイティブで直接実行 |

## 実例 (krkrz)

krkrz_dev リポジトリの `deckproject.toml` を参照。要点:

- Linux: deckbuild でビルド → `bin/x64-linux/Release` + `data/` を stage →
  soname リンク欠落 (`libSDL3.so.0`) を stage script で補完 →
  `LD_LIBRARY_PATH=.` を付けて `./krkrz data` で起動
- Windows: 既存 make フローでビルド → `krkrz64.exe data` を
  `steam_play=1, compat_tool=proton-stable` で起動

## トラブルシューティング

- **デプロイ後に起動しない (無反応)**: `compat_tool` が未インストールの Proton を
  指していないか。`steamctl exec -- "tail -20 ~/.local/share/Steam/logs/compat_log.txt"`
  で `not installed` を確認。
- **Linux バイナリが起動直後に死ぬ**: 共有ライブラリ解決を疑う。
  `steamctl exec -- "cd ~/devkit-game/<id> && LD_LIBRARY_PATH=. ldd ./<exe> | grep 'not found'"`
- **Proton 初回起動が遅い**: prefix 生成のため数十秒かかるのは正常。
- **起動確認**: `steamctl exec -- "pgrep -af devkit-game/<id>"` と
  `steamctl screenshot` の組み合わせが手早い。

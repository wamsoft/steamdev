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

## 資材が Deck に届くまでの流れ (責務分担)

```
[build]  成果物を作るだけ
   kind=deckbuild → sniper コンテナで cmake install → bin/<preset>/<build_type>/
   kind=shell     → 任意コマンド (make install 等)
        ↓
[stage]  .deckstage/<target>/ に「デプロイされるフォルダの完全な姿」を合成
   ① sources: 複数ソースをマージ配置 (mirror/flatten + glob、ハードリンク)
   ② script : glob で表現できない加工 (生成・変換・soname 補完等)
        ↓
[deploy] .deckstage/<target>/ を丸ごと rsync → ~/devkit-game/<gameid>/
   + 起動コマンド・env・settings を Steam に登録 (運ぶだけ、選別しない)
```

- 資材構築の独自ルールは **すべて stage (sources + script) に集約**する
- 転送は rsync なので 2 回目以降は差分のみ。デバイス側の残骸掃除は `--clean`

## 全体構造

```toml
[project]
gameid = "mygame"          # ベース ID。省略時はフォルダ名。
                           # ターゲット別 ID は "<gameid>_<target>" になる
                           # (targets.<t>.deploy.gameid で個別指定も可)

[targets.<name>]           # ターゲットは任意個 (例: linux / windows)
  [targets.<name>.build]   # 省略可: 省略時 build はスキップ
  [targets.<name>.stage]   # 省略可だが sources か script どちらかは実質必須
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
**krkrz_android (app-config.json) / krkrz_web (web-config.json) の
`assetPack.sources` と同書式・同セマンティクス**。

| キー | 意味 |
|---|---|
| `sources` | ソース定義の配列 (下記)。複数ソースを stage ディレクトリへマージ配置 |
| `script` | sources 適用後に実行する任意コマンド。glob で表現できない資材加工 (生成・変換等) はここで行う |
| `copy` | (旧書式) `[["元", "先"], ...]`。内部で mirror エントリに変換される。新規は sources を使う |

### sources エントリ

```toml
sources = [
    { type = "mirror", from = "bin/x64-linux/Release", to = "" },
    { type = "mirror", from = "src/core/data", to = "data", exclude = ["**/*.bak", "**/*.psd"] },
    { type = "flatten", from = "archive", include = ["*.xp3"] },
]
```

※ TOML のインラインテーブルは 1 行で書くこと (複数行に割るとパースエラー)。

| キー | 意味 |
|---|---|
| `type` | `"mirror"` = ツリー構造を保って配置 / `"flatten"` = 階層を潰してファイル名だけで配置 |
| `from` | ソースフォルダ。プロジェクトルート相対 or 絶対。`${VAR}` 展開あり (`${PROJECT_DIR}` + 環境変数) |
| `to` | 配置先 (stage 相対、`""` = 直下)。省略時: mirror+相対→from のパス / mirror+絶対→末尾フォルダ名 / flatten→ルート |
| `include` | Ant 風 glob の配列 (既定 `["**/*"]`)。`**/` = 階層跨ぎ、`*` = `/` 以外の 0 文字以上、`?` = 1 文字 |
| `exclude` | 除外 glob の配列 |
| `comment` | 自由記述 (無視される) |

挙動 (krkrz_web `tools/stage_web.py` / krkrz_android gradle `runCopyRules` と同一):

- **重複配置先は先勝ち** — 後のソースが同じパスに来ても上書きしない
  (差分オーバーレイは先に書く)
- **剪定** — sources の期待に無い既存ファイルは stage から削除される
  (script 生成物も一旦消えるが、直後の script 再実行で戻る)
- **増分** — サイズ + mtime(秒) が一致するファイルはスキップ
- **ハードリンク自動判定** — 同一ボリュームなら os.link (実体複製なし・即時)、
  クロスボリューム等はファイル単位で実コピーにフォールバック。
  ハードリンクのため **stage 内のファイルを直接編集すると元ファイルも変わる**点に注意

`script` は cwd=プロジェクトルート、以下の環境変数付きで実行される
(プロジェクトに `.venv` があればその python が優先される):

- `STEAMCTL_PROJECT_DIR` — プロジェクトルート絶対パス
- `STEAMCTL_STAGE_DIR` — stage ディレクトリ絶対パス
- `STEAMCTL_TARGET` — ターゲット名

完全に作り直したいときは `--clean-stage`。

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

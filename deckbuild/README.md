# deckbuild — Steam Deck 向け Linux ビルド環境

CMake プリセット構成のプロジェクト (krkrz 等) を、Steam Deck で確実に動く形で
Linux ビルドするための Docker ベースのビルド環境。WSL2 上の docker で動かす。

## なぜこの構成か

Linux バイナリの互換性はほぼ **ビルド環境の glibc バージョン**で決まる
(新しい glibc でビルドしたものは古い glibc では動かない)。

- WSL の Ubuntu (例: 26.04 = glibc 2.43) で直ビルド → SteamOS (3.8 系) より
  新しいため実機で動かないリスクが高い
- **Valve 公式 Steam Linux Runtime 3.0 "sniper" SDK** (Debian 11 / glibc 2.31)
  コンテナでビルド → SteamOS ネイティブでも、Steam 配布タイトルと同じ
  sniper コンテナ内 (`compat_tool=SteamLinuxRuntime_sniper`) でも動く

つまり「Steam が公式にサポートする Linux ターゲット」に合わせるのが本ツール。

## セットアップ (初回のみ)

```bash
# WSL 内 (docker が使えること)
./deckbuild.sh image
# Windows から:  .\deckbuild.ps1 image
```

イメージ内容: steamrt sniper SDK + CMake 3.31 + vcpkg (full clone) + nasm/yasm
(vcpkg の libvpx 等が要求)。

コンパイラは SDK 同梱の **gcc-14** (Valve バックポート版) を既定で使う。
SDK 標準の gcc-10 は新しめの intrinsic (`_mm256_cvtsi256_si32` 等) を持たず
コンパイルが通らないことがある。gcc-14 でも glibc ターゲットは 2.31 のまま。
差し替えは環境変数 `DECKBUILD_CC` / `DECKBUILD_CXX` (例: clang)。
コンパイラを変えたら `deckbuild.sh clean` でビルドツリーを作り直すこと。

## ビルド

```bash
# ソースツリーを指定して configure + build + install
./deckbuild.sh -s /mnt/d/work/myproject all

# Windows から
.\deckbuild.ps1 -Src D:\work\myproject all

# プリセット/構成の指定、追加 CMake 引数
PRESET=x64-linux BUILD_TYPE=Debug CMAKEOPT='-DFOO=ON' ./deckbuild.sh -s ... all

# 個別ステップ / コンテナ内調査 / ビルドツリー破棄
./deckbuild.sh -s ... configure|build|install|shell|clean

# 非 CMake プロジェクト: 任意のビルドコマンドをコンテナ内 (cwd=ソースルート) で実行
./deckbuild.sh -s ... run 'make linux'
./deckbuild.sh -s ... run './build.sh --release'
```

## CMake 以外のビルドシステム

`all`/`configure`/`build`/`install` の定型フローは CMake プリセット前提だが、
**コンテナ環境自体はビルドシステムを問わない**。make / autotools / meson /
自前スクリプト等は `run` で任意コマンドを流せばよい (CC/CXX は gcc-14 が
環境変数で設定済み。素の `gcc` は 10 なので、Makefile 等が CC を尊重しない
場合は明示指定する)。成果物がソースツリー側に出るなら stage sources で
そのまま拾える。ビルドツリーをコンテナ volume 側に置く場合は `export` で取り出す。

- ビルドツリー (`build/<preset>`) は docker named volume 上に置く
  (drvfs bind mount の遅い I/O を回避)。ソースパスごとに独立。
- vcpkg のバイナリキャッシュは `deckbuild-cache` volume に永続化。
  初回は依存ライブラリのソースビルドで時間がかかるが 2 回目以降は速い。
- 成果物は `cmake --install` でソース側 `bin/<preset>/<build_type>/` に出る
  ので Windows からそのまま見える。

## Steam Deck へのデプロイ (steamdev と連携)

```bash
# ネイティブ実行 (SteamOS の glibc は sniper より新しいのでそのまま動く)
steamdev -d <deck-ip> deploy --gameid mygame \
    --dir D:/work/myproject/bin/x64-linux/Release \
    --command "./mygame data" --start

# Steam Linux Runtime (sniper) コンテナ内で実行 = Steam 配布時と同一環境
steamdev -d <deck-ip> deploy --gameid mygame \
    --dir D:/work/myproject/bin/x64-linux/Release \
    --command "./mygame data" \
    --set compat_tool=SteamLinuxRuntime_sniper --start
```

Windows ビルド (Proton 実行) と Linux ビルドを **同じデータ構成**で並置する場合は
gameid を分けるか (`mygame_win` / `mygame_linux`)、同一フォルダに exe と ELF を
同居させて `--command` だけ切り替える。

## 注意

- krkrz の configure は FetchContent (harfbuzz / sqlite3 / SDL3 等) で
  ネットワークアクセスが必要。コンテナはネットワークありで実行される。
- vcpkg manifest の `builtin-baseline` 解決のため vcpkg は full clone
  (shallow だと失敗する)。
- イメージの sniper SDK はローリング更新される。再現性を厳密にしたい場合は
  Dockerfile の FROM をダイジェスト固定にする。

## 依存ライブラリが足りないときの対処ガイド

configure が `Could NOT find xxx` で止まったときの手順。

### 0. まず SDK に本当に無いか確認

```bash
docker run --rm deckbuild-sniper bash -c "dpkg -l | grep -i <name>"
docker run --rm deckbuild-sniper bash -c "pkg-config --list-all | grep -i <name>"
docker run --rm deckbuild-sniper bash -c "apt-cache search <name>"   # apt で入るか
```

sniper SDK はゲーム系の -dev を広く同梱している (SDL2, libpng, bz2, freetype,
openal, vulkan 等)。見つからないのに configure が失敗する場合は、探し方の問題
(パッケージ名違い / pkg-config の .pc 名違い) のこともある。

### 1. 解決手段を優先順に試す

**① プロジェクト側のフォールバックを使う (最優先)**

多くのプロジェクトは「システムのライブラリを使う or 同梱/自動取得してビルド」の
切替オプションを持つ。命名の定番:

- `<PROJ>_SYSTEM_<LIB>=OFF` (例: devilutionX の `DEVILUTIONX_SYSTEM_LIBSODIUM=OFF`
  → FetchContent で取得し静的リンク)
- `USE_SYSTEM_<LIB>=OFF` / `BUNDLED_<LIB>=ON` / `FETCHCONTENT_...`

イメージを汚さず、静的リンクになりがちで配布も楽になるため最優先。
`grep -iE "SYSTEM_|BUNDLED|FetchContent" CMakeLists.txt cmake/ CMake/` で探す。

**② そもそも要らない機能なら切る**

テスト (`BUILD_TESTING=OFF`)、ベンチマーク、ドキュメント、任意機能
(`NONET=ON` 等) の依存なら、機能ごと無効化して回避するのが最短。

**③ vcpkg manifest に足す (vcpkg 使用プロジェクト)**

krkrz のような vcpkg manifest プロジェクトなら `vcpkg.json` の
`dependencies` に追加するだけ。静的ビルドされ、Deck 側に .so を運ぶ必要が無い。

**④ apt でイメージに追加 (Dockerfile 追記)**

ビルドツール (nasm / yasm / gettext / smpq 等、**実行時に不要なもの**) はこれが
正解。`deckbuild/Dockerfile` の apt-get 行に追記して `deckbuild.sh image` で再構築:

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends nasm yasm <追加> \
    && rm -rf /var/lib/apt/lists/*
```

**ランタイムライブラリを apt (-dev) で足す場合は要注意**: SDK に -dev を足せば
ビルドは通るが、生成バイナリはその **.so を実行時にも要求**する。Deck 側
(SteamOS ネイティブ / sniper runtime) にその .so が存在するか確認し、無ければ
.so を stage に同梱して `LD_LIBRARY_PATH=.` で起動する (krkrz の libSDL3 と同じ
パターン)。迷ったら①③の静的リンク系に倒すほうが安全。

**⑤ ソースから静的ビルドしてイメージに焼く (最後の手段)**

Dockerfile に該当ライブラリの configure/make install を書く。更新追従の手間が
残るので、①〜④で解決できない場合のみ。

### 2. ビルド後にランタイム依存を検証する

```bash
# コンテナ内で
readelf -d <exe> | grep NEEDED                       # 動的リンクしている .so 一覧
objdump -T <exe> | grep -o "GLIBC_[0-9.]*" | sort -Vu | tail -1    # <= 2.31 なら OK
```

NEEDED に「SDK にしか無い .so」が出てきたら④の注意事項の状況。同梱 + soname
リンク補完 + `LD_LIBRARY_PATH=.` で対処する。

### 実例 (このリポジトリで実際に踏んだもの)

| 事象 | 分類 | 対処 |
|---|---|---|
| vcpkg の libvpx が nasm を要求 | ビルドツール不足 | ④ Dockerfile に nasm/yasm 追加 |
| devilutionX が libsodium-dev を要求 | ランタイム系ライブラリ | ① `DEVILUTIONX_SYSTEM_LIBSODIUM=OFF` + `DEVILUTIONX_STATIC_LIBSODIUM=ON` (FetchContent 静的。**静的も指定しないと NEEDED に libsodium.so が残る**) |
| devilutionX が GTest を要求 | テストのみの依存 | ② `BUILD_TESTING=OFF` |
| devilutionX の install が空 (何も入らない) | パッケージング条件 | install ルールが `CPACK=ON` + `BUILD_ASSETS_MPQ=ON` 前提だった。smpq を④で追加し両方 ON (キャッシュ済み OFF が残るのでプリセットで明示) |
| devilutionX 実行時に libSDL2_image が無い | **SteamOS ネイティブに無い .so** | deploy settings で `compat_tool = "SteamLinuxRuntime_sniper"` — ビルド環境と同一のコンテナで実行すれば SDK にあるものは全部ある |
| gcc-10 に新しい intrinsic が無い | コンパイラ世代 | SDK 同梱 gcc-14 に切替 (deckbuild 既定) |
| krkrz の libSDL3 soname 欠落 | 同梱 .so の解決 | stage script で補完 + `LD_LIBRARY_PATH=.` |
| devilutionX が `./save` に書けず起動失敗 | アプリの書込先 | 存在しない相対ディレクトリを指定していた。自動作成される既定パス (XDG) に任せるか、書込先を実在パスにする |

**実行環境の選び方の目安**: バイナリが SDK の .so (SDL2_image 等) に動的リンク
しているなら `compat_tool = "SteamLinuxRuntime_sniper"` が最も確実
(ビルド環境 = 実行環境)。ネイティブ実行したいなら NEEDED を確認して
SteamOS に無いものを静的化するか同梱する。

### install が無い / 条件付き / 想定外の場所に入る場合

deckbuild は「configure → build → `cmake --install` → `bin/<preset>/<build_type>/`」
を標準フローとするが、上流の install 事情は様々。ケース別に:

**A. 想定外の場所にインストールされる** — 問題ない。install 先のレイアウトが
どうであれ、deckproject.toml の stage `sources` で好きな形に組み替えられる。
例 (devilutionX): バイナリは `bin/`、資材は `share/diasurgical/devilutionx/` に
入るので、stage で両方拾って直下へ寄せる:

```toml
sources = [
    { type = "mirror", from = "bin/x64-linux/Release/bin", to = "" },
    { type = "flatten", from = "bin/x64-linux/Release/share/diasurgical/devilutionx", to = "", include = ["devilutionx.mpq"] },
]
```

**B. install ルールが条件付きで空になる** — `cmake --install` が何も出力しない
(エラーも出ない) 場合はこのパターンを疑う。devilutionX は install 一式が
`CPACK=ON` (Linux では + `BUILD_ASSETS_MPQ=ON`、smpq 必須) の内側にあった。
上流の条件を CMakeUserPresets の cacheVariables で満たすのが正攻法。
**注意: option() の既定値はキャッシュに残る**ので、途中からツールを足した場合は
プリセットで明示 ON にする (再 configure だけでは変わらない)。

**C. install が本当に無い** — ビルドツリーは docker volume 内でホストから
見えないため、`export` コマンドで必要物を取り出す:

```bash
# build/<preset>/ 相対パスを指定 → ソース側 .deckbuild/<preset>/ にコピーされる
./deckbuild.sh -s <src> export mygame assets
```

取り出した後は stage sources で `.deckbuild/x64-linux/...` を参照する。
(`.deckbuild/` は .gitignore に入れておく)

### プリセットが無いプロジェクトへの適用

deckbuild は CMake プリセット前提。上流にプリセットが無い場合は、チェックアウトに
**`CMakeUserPresets.json`** (ローカル用・通常 gitignore 対象) を置いて定義する:

```json
{
    "version": 3,
    "configurePresets": [
        {
            "name": "x64-linux",
            "generator": "Ninja",
            "binaryDir": "${sourceDir}/build/x64-linux",
            "cacheVariables": { "CMAKE_BUILD_TYPE": "Release" }
        }
    ]
}
```

回避オプション (①②) もここの cacheVariables に集約すると、上流を一切変更せずに
ビルド構成を完結できる。

## 実機検証済み (krkrz)

krkrz_dev の x64-linux プリセットをこの環境でビルドし、Steam Deck 実機で
ネイティブ動作を確認済み。生成バイナリの依存は GLIBC ≤ 2.30、GLIBCXX 依存なし
(libstdc++ 静的リンク) で、SteamOS ネイティブ / sniper コンテナの両方で動く。
検証コマンド:

```bash
# コンテナ内で
objdump -T <exe> | grep -o "GLIBC_[0-9.]*"   | sort -Vu | tail -1   # <= 2.31 なら OK
objdump -T <exe> | grep -o "GLIBCXX_[0-9.]*" | sort -Vu | tail -1
readelf -d <exe> | grep -E "NEEDED|RPATH"    # 同梱 .so の soname/RPATH 確認
```

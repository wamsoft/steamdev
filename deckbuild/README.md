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

イメージ内容: steamrt sniper SDK + CMake 3.31 + vcpkg (full clone)。

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
```

- ビルドツリー (`build/<preset>`) は docker named volume 上に置く
  (drvfs bind mount の遅い I/O を回避)。ソースパスごとに独立。
- vcpkg のバイナリキャッシュは `deckbuild-cache` volume に永続化。
  初回は依存ライブラリのソースビルドで時間がかかるが 2 回目以降は速い。
- 成果物は `cmake --install` でソース側 `bin/<preset>/<build_type>/` に出る
  ので Windows からそのまま見える。

## Steam Deck へのデプロイ (steamctl と連携)

```bash
# ネイティブ実行 (SteamOS の glibc は sniper より新しいのでそのまま動く)
steamctl -d <deck-ip> deploy --gameid mygame \
    --dir D:/work/myproject/bin/x64-linux/Release \
    --command "./mygame data" --start

# Steam Linux Runtime (sniper) コンテナ内で実行 = Steam 配布時と同一環境
steamctl -d <deck-ip> deploy --gameid mygame \
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

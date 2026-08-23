# 適用例: devilutionX

上流を一切変更せずに、外部の CMake プロジェクト
([devilutionX](https://github.com/diasurgical/devilutionX) — Diablo 1 エンジン再実装)
を Steam Deck へ ship する構成例。追加するのは 2 ファイルだけ:

- `CMakeUserPresets.json` — プリセットが無い上流に x64-linux プリセットを
  ローカル定義。依存回避オプション (libsodium 静的化 / テスト無効 /
  CPACK+MPQ パッケージング有効) もここに集約
- `deckproject.toml` — ビルド (deckbuild) / stage (バイナリ + devilutionx.mpq +
  spawn.mpq を直下へ合成) / デプロイ (sniper ランタイム実行) の定義

## 再現手順

```powershell
git clone --depth 1 https://github.com/diasurgical/devilutionX.git devilutionx
cd devilutionx
copy <steamctl>/examples/devilutionx/CMakeUserPresets.json .
copy <steamctl>/examples/devilutionx/deckproject.toml .

# シェアウェアデータ (無償配布) を取得
mkdir gamedata
curl -L -o gamedata/spawn.mpq https://github.com/diasurgical/devilutionx-assets/releases/download/v4/spawn.mpq

# ビルド → 資材構築 → Deck へ転送 → 起動
steamctl -d <deck-ip> project -p . ship linux
```

Steam Deck / SteamOS 3.8.16 実機で Diablo Shareware タイトルメニュー表示まで
確認済み (2026-08-24)。

## この例が示していること

- **プリセットの無い上流** → `CMakeUserPresets.json` で非侵襲に適用
- **依存問題の解決** (deckbuild/README.md のガイドの実地例):
  - libsodium 不足 → FetchContent + 静的リンク (`DEVILUTIONX_STATIC_LIBSODIUM=ON`)
  - GTest 不足 → `BUILD_TESTING=OFF`
  - install が条件付きで空 → `CPACK=ON` + `BUILD_ASSETS_MPQ=ON` (+ イメージに smpq)
- **想定外の install 先** (`share/diasurgical/...`) → stage sources の flatten で吸収
- **SteamOS ネイティブに無い .so** (SDL2_image) →
  `compat_tool = "SteamLinuxRuntime_sniper"` でビルド環境と同一のコンテナ実行
- **書込先の罠** → `--save-dir ./save` (存在しない相対パス) は Read-Only エラーに
  なるため、自動作成される既定パスに任せる

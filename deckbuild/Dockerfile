# Steam Deck 向け Linux ビルド環境
#
# Valve 公式の Steam Linux Runtime 3.0 "sniper" SDK (Debian 11 / glibc 2.31) を
# ベースにする。ここでビルドしたバイナリは:
#   - SteamOS ネイティブ (glibc はより新しい) でそのまま動く
#   - Steam Linux Runtime (compat_tool=SteamLinuxRuntime_sniper) コンテナ内でも動く
# つまり Steam 公式配布と同じターゲットに合わせられる。
#
# 追加要素:
#   - 新しめの CMake (SDK 同梱の Debian 11 cmake はプリセット v2 に足りない)
#   - vcpkg (full clone: manifest の builtin-baseline 解決に履歴が必要)

FROM registry.gitlab.steamos.cloud/steamrt/sniper/sdk:latest

USER root

# ビルドツール類を追加:
# - nasm/yasm: vcpkg の一部ポート (libvpx 等) が要求するアセンブラ
# - smpq: MPQ アーカイバ (devilutionX 等の資材パッケージングが要求)
# Debian 11 (bullseye) の LTS 終了で bullseye-security の pool が deb.debian.org
# から消えた (インデックスは残るため 404 になる)。archive.debian.org に向け直す。
RUN sed -i 's#http://deb.debian.org/debian-security#http://archive.debian.org/debian-security#' \
        /etc/apt/sources.list /etc/apt/sources.list.d/* 2>/dev/null; \
    apt-get update && apt-get install -y --no-install-recommends nasm yasm smpq \
    && rm -rf /var/lib/apt/lists/*

ARG CMAKE_VERSION=3.31.7
RUN mkdir -p /opt /usr/local/bin \
    && curl -fsSL "https://github.com/Kitware/CMake/releases/download/v${CMAKE_VERSION}/cmake-${CMAKE_VERSION}-linux-x86_64.tar.gz" \
        | tar -xz -C /opt \
    && for f in /opt/cmake-${CMAKE_VERSION}-linux-x86_64/bin/*; do \
        ln -sf "$f" /usr/local/bin/"$(basename "$f")"; done \
    && cmake --version

ENV VCPKG_ROOT=/opt/vcpkg
RUN git clone https://github.com/microsoft/vcpkg "$VCPKG_ROOT" \
    && "$VCPKG_ROOT/bootstrap-vcpkg.sh" -disableMetrics

# バイナリキャッシュは /cache (ホスト側ボリューム) に置き、イメージ再作成でも保持
ENV VCPKG_DEFAULT_BINARY_CACHE=/cache/vcpkg-bincache
ENV PATH="$VCPKG_ROOT:$PATH"

WORKDIR /work

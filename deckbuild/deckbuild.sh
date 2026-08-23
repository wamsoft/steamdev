#!/usr/bin/env bash
# deckbuild - Steam Deck 向け Linux バイナリを steamrt sniper SDK コンテナでビルドする
#
# WSL2 (または Linux) 上の docker で実行する。ソースツリーを /work に bind mount し、
# CMake プリセット (既定: x64-linux) の configure → build → install を回す。
#
# 使い方:
#   deckbuild.sh image                 # ビルド用 docker イメージを作成 (初回のみ)
#   deckbuild.sh [-s SRC] all          # configure + build + install
#   deckbuild.sh [-s SRC] configure|build|install
#   deckbuild.sh [-s SRC] shell        # コンテナ内シェル (調査用)
#
# 環境変数:
#   PRESET      CMake プリセット名 (既定 x64-linux)
#   BUILD_TYPE  Release / Debug (既定 Release)
#   CMAKEOPT    configure に渡す追加引数 (例: -DKRKRZ_USE_SJIS=YES)
#   DECKBUILD_IMAGE  イメージ名 (既定 deckbuild-sniper)
#
# ビルドツリーはソース直下の build/<preset> だが、bind mount (9p) の遅さを避ける
# ため /work/build を docker named volume に載せ替える。成果物は install で
# ソース側 bin/<preset>/<build_type> に出るので Windows 側からそのまま見える。

set -euo pipefail

IMAGE="${DECKBUILD_IMAGE:-deckbuild-sniper}"
PRESET="${PRESET:-x64-linux}"
BUILD_TYPE="${BUILD_TYPE:-Release}"
CMAKEOPT="${CMAKEOPT:-}"
SRC="$(pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

while getopts "s:p:t:h" opt; do
    case "$opt" in
        s) SRC="$(realpath "$OPTARG")" ;;
        p) PRESET="$OPTARG" ;;
        t) BUILD_TYPE="$OPTARG" ;;
        h) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) exit 1 ;;
    esac
done
shift $((OPTIND - 1))
CMD="${1:-all}"

if [ "$CMD" = image ]; then
    exec docker build -t "$IMAGE" "$SCRIPT_DIR"
fi

if [ ! -d "$SRC" ]; then
    echo "source dir not found: $SRC" >&2
    exit 1
fi

# ソースパスごとに独立したビルドボリューム (パスの hash で識別)
SRC_ID="$(printf '%s' "$SRC" | md5sum | cut -c1-10)"
BUILD_VOL="deckbuild-build-${SRC_ID}"
CACHE_VOL="deckbuild-cache"

run_in_container() {
    docker run --rm -i $( [ -t 0 ] && echo -t ) \
        -v "$SRC":/work \
        -v "$BUILD_VOL":/work/build \
        -v "$CACHE_VOL":/cache \
        -e VCPKG_DEFAULT_BINARY_CACHE=/cache/vcpkg-bincache \
        -w /work \
        "$IMAGE" bash -c "mkdir -p /cache/vcpkg-bincache && $1"
}

case "$CMD" in
    configure)
        run_in_container "cmake --preset $PRESET $CMAKEOPT"
        ;;
    build)
        run_in_container "cmake --build build/$PRESET --config $BUILD_TYPE"
        ;;
    install)
        run_in_container "cmake --install build/$PRESET --config $BUILD_TYPE --prefix bin/$PRESET/$BUILD_TYPE"
        ;;
    all)
        run_in_container "cmake --preset $PRESET $CMAKEOPT \
            && cmake --build build/$PRESET --config $BUILD_TYPE \
            && cmake --install build/$PRESET --config $BUILD_TYPE --prefix bin/$PRESET/$BUILD_TYPE"
        ;;
    shell)
        run_in_container "bash" || true
        ;;
    clean)
        docker volume rm "$BUILD_VOL"
        ;;
    *)
        echo "unknown command: $CMD (image|configure|build|install|all|shell|clean)" >&2
        exit 1
        ;;
esac

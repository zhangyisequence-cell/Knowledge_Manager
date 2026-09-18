#!/usr/bin/env bash
set -euo pipefail

LLAMA_TAG="v0.4.1"
LLAMA_COMMIT="b29c606e28a01b1bc8c1351026a0fa6e616bf6c4"
INSTALL_ROOT="/opt/knowledge-manager/llama"
INSTALL_DIR="${INSTALL_ROOT}/${LLAMA_COMMIT}"
MARKER="${INSTALL_DIR}/.knowledge-manager-llama-build"

SOURCE_ARCHIVE=""
if [[ "${1:-}" == "--build" && $# -eq 3 && "${2:-}" == "--source-archive" && -n "${3:-}" ]]; then
    SOURCE_ARCHIVE="$3"
elif [[ "${1:-}" != "--build" || $# -ne 1 ]]; then
    echo "Usage: sudo bash $0 --build [--source-archive PINNED_TAR_GZ]" >&2
    echo "This script does nothing unless --build is supplied explicitly." >&2
    exit 2
fi
if [[ "${EUID}" -ne 0 ]]; then
    echo "Run as root on the target Ubuntu server." >&2
    exit 1
fi
if [[ ! -r /etc/os-release ]]; then
    echo "Cannot verify Ubuntu release: /etc/os-release is missing." >&2
    exit 1
fi
# shellcheck disable=SC1091
source /etc/os-release
if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "24.04" ]]; then
    echo "Refusing unsupported OS: expected Ubuntu 24.04, got ${ID:-unknown} ${VERSION_ID:-unknown}." >&2
    exit 1
fi
if [[ -L "${INSTALL_ROOT}" || -L "${INSTALL_DIR}" ]]; then
    echo "Refusing symbolic-link install path: ${INSTALL_DIR}" >&2
    exit 1
fi
if [[ -e "${INSTALL_DIR}" ]]; then
    if [[ -f "${MARKER}" ]] \
        && grep -Fxq "commit=${LLAMA_COMMIT}" "${MARKER}" \
        && [[ -x "${INSTALL_DIR}/bin/llama-server" ]]; then
        echo "Already built at: ${INSTALL_DIR}"
        echo "Run llama-server --version and the documented acceptance checks before use."
        exit 0
    fi
    echo "Refusing unexpected existing directory: ${INSTALL_DIR}" >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends build-essential ca-certificates cmake git

work_dir="$(mktemp -d /tmp/knowledge-manager-llama-build.XXXXXX)"
staging="${INSTALL_ROOT}/.${LLAMA_COMMIT}.staging.$$"
cleanup() {
    rm -rf -- "${work_dir}"
    if [[ -d "${staging}" ]]; then
        rm -rf -- "${staging}"
    fi
}
trap cleanup EXIT

if [[ -n "${SOURCE_ARCHIVE}" ]]; then
    script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
    /usr/bin/python3 -I "${script_dir}/prepare_llama_source.py" \
        --archive "${SOURCE_ARCHIVE}" --destination "${work_dir}/llama.cpp"
else
    git clone --depth 1 --branch "${LLAMA_TAG}" \
        https://github.com/ggml-org/llama.cpp.git "${work_dir}/llama.cpp"
    actual_commit="$(git -C "${work_dir}/llama.cpp" rev-parse HEAD)"
    if [[ "${actual_commit}" != "${LLAMA_COMMIT}" ]]; then
        echo "llama.cpp revision mismatch: expected ${LLAMA_COMMIT}, got ${actual_commit}" >&2
        exit 1
    fi
fi

cmake -S "${work_dir}/llama.cpp" -B "${work_dir}/llama.cpp/build" \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=OFF \
    -DGGML_NATIVE=OFF \
    -DGGML_SSE42=ON \
    -DGGML_AVX=ON \
    -DGGML_F16C=ON \
    -DGGML_AVX2=OFF \
    -DGGML_FMA=OFF \
    -DGGML_BMI2=OFF \
    -DGGML_AVX_VNNI=OFF \
    -DGGML_AVX512=OFF \
    -DGGML_CPU_ALL_VARIANTS=OFF \
    -DGGML_CUDA=OFF \
    -DGGML_HIP=OFF \
    -DGGML_MUSA=OFF \
    -DGGML_VULKAN=OFF \
    -DGGML_SYCL=OFF \
    -DGGML_OPENCL=OFF \
    -DGGML_RPC=OFF \
    -DLLAMA_BUILD_TESTS=OFF \
    -DLLAMA_BUILD_EXAMPLES=OFF \
    -DLLAMA_BUILD_UI=OFF \
    -DLLAMA_USE_PREBUILT_UI=OFF \
    -DLLAMA_BUILD_SERVER=ON
cmake --build "${work_dir}/llama.cpp/build" --target llama-server --parallel 2

install -d -m 0755 "${INSTALL_ROOT}" "${staging}/bin"
install -m 0755 "${work_dir}/llama.cpp/build/bin/llama-server" \
    "${staging}/bin/llama-server"
{
    echo "tag=${LLAMA_TAG}"
    echo "commit=${LLAMA_COMMIT}"
    if [[ -n "${SOURCE_ARCHIVE}" ]]; then
        echo "source_archive_sha256=03fb04316eb32a7b7347004a79a8dd60531c02f5a064d853fed3b53828951723"
    fi
    echo "cpu=AVX1,F16C,SSE4.2;no-AVX2,no-FMA,no-BMI2"
} > "${staging}/.knowledge-manager-llama-build"
chmod 0644 "${staging}/.knowledge-manager-llama-build"
mv -- "${staging}" "${INSTALL_DIR}"
echo "Installed llama-server at ${INSTALL_DIR}/bin/llama-server"
echo "No service was enabled or started, and no model was downloaded."

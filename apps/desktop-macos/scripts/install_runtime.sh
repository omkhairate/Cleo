#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_ROOT="$(cd "$ROOT_DIR/../.." && pwd)"
LOCK_DIR="$ROOT_DIR/.build/install_runtime.lock"

PYTHON_BIN_SOURCE="${CLEO_BUNDLED_PYTHON_BIN:-/Users/apollo/miniforge3/bin/python3}"
PYTHON_STDLIB_SOURCE="${CLEO_BUNDLED_PYTHON_STDLIB:-/Users/apollo/miniforge3/lib/python3.10}"
SITE_PACKAGES_SOURCE="${CLEO_BUNDLED_SITE_PACKAGES:-$PROJECT_ROOT/.venv/lib/python3.10/site-packages}"
APP_SUPPORT_DIR="${CLEO_APP_SUPPORT_DIR:-$HOME/Library/Application Support/Cleo}"
RUNTIME_ROOT="${CLEO_RUNTIME_ROOT:-$APP_SUPPORT_DIR/runtime}"
FORCE_REBUILD_RUNTIME="${CLEO_FORCE_REBUILD_RUNTIME:-0}"
RUNTIME_MODE="${CLEO_RUNTIME_MODE:-link}"
PROJECT_ENV_FILE="${CLEO_PROJECT_ENV_FILE:-$PROJECT_ROOT/.env}"

RUNTIME_SITE_PACKAGES_ITEMS=(
  anyio
  annotated_types
  certifi
  charset_normalizer
  dotenv
  exceptiongroup
  filelock
  fsspec
  functorch
  h11
  hf_xet
  httpcore
  httpx
  huggingface_hub
  idna
  jinja2
  markupsafe
  mpmath
  networkx
  numpy
  packaging
  PIL
  pydantic
  pydantic_core
  pydantic_settings
  regex
  requests
  safetensors
  sniffio
  sympy
  tokenizers
  torch
  torchgen
  torchvision
  tqdm
  transformers
  typing_extensions.py
  typing_inspection
  urllib3
  yaml
)
RUNTIME_SITE_PACKAGES_GLOBS=(
  "anyio-*.dist-info"
  "annotated_types-*.dist-info"
  "certifi-*.dist-info"
  "charset_normalizer-*.dist-info"
  "exceptiongroup-*.dist-info"
  "filelock-*.dist-info"
  "fsspec-*.dist-info"
  "h11-*.dist-info"
  "hf_xet-*.dist-info"
  "httpcore-*.dist-info"
  "httpx-*.dist-info"
  "huggingface_hub-*.dist-info"
  "idna-*.dist-info"
  "jinja2-*.dist-info"
  "markupsafe-*.dist-info"
  "mpmath-*.dist-info"
  "networkx-*.dist-info"
  "numpy-*.dist-info"
  "packaging-*.dist-info"
  "pillow-*.dist-info"
  "pydantic-*.dist-info"
  "pydantic_core-*.dist-info"
  "pydantic_settings-*.dist-info"
  "python_dotenv-*.dist-info"
  "pyyaml-*.dist-info"
  "regex-*.dist-info"
  "requests-*.dist-info"
  "safetensors-*.dist-info"
  "sniffio-*.dist-info"
  "sympy-*.dist-info"
  "tokenizers-*.dist-info"
  "torch-*.dist-info"
  "torchvision-*.dist-info"
  "tqdm-*.dist-info"
  "transformers-*.dist-info"
  "typing_extensions-*.dist-info"
  "typing_inspection-*.dist-info"
  "urllib3-*.dist-info"
)

mkdir -p "$ROOT_DIR/.build"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "Another Cleo runtime install is already running."
  echo "If that is stale, stop it and remove:"
  echo "  $LOCK_DIR"
  exit 1
fi

cleanup() {
  rm -rf "$LOCK_DIR"
}

trap cleanup EXIT

link_path() {
  local source_path="$1"
  local destination_path="$2"
  rm -rf "$destination_path"
  ln -s "$source_path" "$destination_path"
}

copy_clean() {
  local source_path="$1"
  local destination_path="$2"
  COPYFILE_DISABLE=1 cp -X "$source_path" "$destination_path"
  xattr -c "$destination_path" 2>/dev/null || true
}

copy_tree_clean() {
  local source_dir="$1"
  local destination_dir="$2"
  rm -rf "$destination_dir"
  mkdir -p "$destination_dir"
  if ! COPYFILE_DISABLE=1 cp -cR "$source_dir/." "$destination_dir" 2>/dev/null; then
    COPYFILE_DISABLE=1 cp -R "$source_dir/." "$destination_dir"
  fi
  xattr -cr "$destination_dir" 2>/dev/null || true
  find "$destination_dir" -name '.DS_Store' -delete
  find "$destination_dir" -name '__pycache__' -type d -prune -exec rm -rf {} +
  find "$destination_dir" \( -name '*.pyc' -o -name '*.pyo' \) -delete
}

copy_tree_filtered() {
  local source_dir="$1"
  local destination_dir="$2"
  shift 2
  rm -rf "$destination_dir"
  mkdir -p "$destination_dir"
  COPYFILE_DISABLE=1 rsync -a \
    --delete \
    --exclude '.DS_Store' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '*.pyo' \
    "$@" \
    "$source_dir/" "$destination_dir/"
  xattr -cr "$destination_dir" 2>/dev/null || true
  find "$destination_dir" -name '__pycache__' -type d -prune -exec rm -rf {} +
  find "$destination_dir" \( -name '*.pyc' -o -name '*.pyo' \) -delete
}

copy_runtime_site_packages() {
  setopt local_options null_glob
  local destination_dir="$1"
  rm -rf "$destination_dir"
  mkdir -p "$destination_dir"

  echo "Copying required Python packages..."
  local item
  for item in "${RUNTIME_SITE_PACKAGES_ITEMS[@]}"; do
    local source_path="$SITE_PACKAGES_SOURCE/$item"
    if [[ -e "$source_path" ]]; then
      echo "  - $item"
      if [[ -d "$source_path" ]]; then
        case "$item" in
          torch)
            copy_tree_filtered "$source_path" "$destination_dir/$item" \
              --exclude 'include' \
              --exclude 'share' \
              --exclude 'testing' \
              --exclude 'test' \
              --exclude 'package' \
              --exclude '_inductor' \
              --exclude 'distributed' \
              --exclude 'jit' \
              --exclude 'onnx' \
              --exclude 'profiler' \
              --exclude 'quantization' \
              --exclude 'ao'
            ;;
          functorch)
            copy_tree_filtered "$source_path" "$destination_dir/$item" \
              --exclude 'dim' \
              --exclude 'einops' \
              --exclude 'experimental'
            ;;
          torchgen)
            echo "    skipping torchgen internals"
            ;;
          *)
            copy_tree_clean "$source_path" "$destination_dir/$item"
            ;;
        esac
      else
        copy_clean "$source_path" "$destination_dir/$item"
      fi
    fi
  done

  local pattern
  for pattern in "${RUNTIME_SITE_PACKAGES_GLOBS[@]}"; do
    local source_path
    for source_path in "$SITE_PACKAGES_SOURCE"/$~pattern; do
      [[ -e "$source_path" ]] || continue
      local target_name="${source_path:t}"
      if [[ -d "$source_path" ]]; then
        copy_tree_clean "$source_path" "$destination_dir/$target_name"
      else
        copy_clean "$source_path" "$destination_dir/$target_name"
      fi
    done
  done

  xattr -cr "$destination_dir" 2>/dev/null || true
}

runtime_manifest() {
  setopt local_options null_glob
  {
    stat -f "python-bin:%m:%z" "$PYTHON_BIN_SOURCE"
    stat -f "python-stdlib:%m:%z" "$PYTHON_STDLIB_SOURCE"
    for item in "${RUNTIME_SITE_PACKAGES_ITEMS[@]}"; do
      [[ -e "$SITE_PACKAGES_SOURCE/$item" ]] && stat -f "site-package:%N:%m:%z" "$SITE_PACKAGES_SOURCE/$item"
    done
    local pattern source_path
    for pattern in "${RUNTIME_SITE_PACKAGES_GLOBS[@]}"; do
      for source_path in "$SITE_PACKAGES_SOURCE"/$~pattern; do
        [[ -e "$source_path" ]] && stat -f "site-package:%N:%m:%z" "$source_path"
      done
    done
    stat -f "bridge:%m:%z" "$ROOT_DIR/local_bridge.py"
    find "$PROJECT_ROOT/packages/assistant-core/src/assistant_core" -type f -print0 \
      | xargs -0 stat -f "assistant-core:%N:%m:%z" 2>/dev/null
  } | shasum -a 256 | awk '{print $1}'
}

if [[ ! -x "$PYTHON_BIN_SOURCE" ]]; then
  echo "Bundled Python executable not found at $PYTHON_BIN_SOURCE"
  exit 1
fi

if [[ ! -d "$PYTHON_STDLIB_SOURCE" ]]; then
  echo "Bundled Python standard library not found at $PYTHON_STDLIB_SOURCE"
  exit 1
fi

if [[ ! -d "$SITE_PACKAGES_SOURCE" ]]; then
  echo "Bundled site-packages not found at $SITE_PACKAGES_SOURCE"
  exit 1
fi

if [[ "$RUNTIME_MODE" != "link" && "$RUNTIME_MODE" != "copy" ]]; then
  echo "Unsupported CLEO_RUNTIME_MODE: $RUNTIME_MODE"
  echo "Use 'link' or 'copy'."
  exit 1
fi

mkdir -p "$APP_SUPPORT_DIR"
if [[ -f "$PROJECT_ENV_FILE" ]]; then
  COPYFILE_DISABLE=1 cp -f "$PROJECT_ENV_FILE" "$APP_SUPPORT_DIR/.env"
  xattr -c "$APP_SUPPORT_DIR/.env" 2>/dev/null || true
fi

local_manifest="$(runtime_manifest)"
manifest_file="$RUNTIME_ROOT/.manifest"

if [[ "$FORCE_REBUILD_RUNTIME" != "1" && -f "$manifest_file" ]]; then
  cached_manifest="$(cat "$manifest_file")"
  if [[ "$cached_manifest" == "$local_manifest" ]]; then
    echo "Cleo runtime is already up to date."
    echo "  runtime: $RUNTIME_ROOT"
    exit 0
  fi
fi

temp_root="$RUNTIME_ROOT.tmp"
rm -rf "$temp_root"
mkdir -p "$temp_root/bin" "$temp_root/python-home/lib/python3.10" "$temp_root/site-packages" "$temp_root/app" "$temp_root/bridge"

echo "Installing Cleo runtime in '$RUNTIME_MODE' mode..."

if [[ "$RUNTIME_MODE" == "link" ]]; then
  link_path "$PYTHON_BIN_SOURCE" "$temp_root/bin/python3"
  link_path "$PYTHON_STDLIB_SOURCE" "$temp_root/python-home/lib/python3.10"
  link_path "$SITE_PACKAGES_SOURCE" "$temp_root/site-packages"
  link_path "$PROJECT_ROOT/packages/assistant-core/src/assistant_core" "$temp_root/app/assistant_core"
  link_path "$ROOT_DIR/local_bridge.py" "$temp_root/bridge/local_bridge.py"
else
  copy_clean "$PYTHON_BIN_SOURCE" "$temp_root/bin/python3"
  chmod +x "$temp_root/bin/python3"

  copy_tree_clean "$PYTHON_STDLIB_SOURCE" "$temp_root/python-home/lib/python3.10"
  rm -rf \
    "$temp_root/python-home/lib/python3.10/site-packages" \
    "$temp_root/python-home/lib/python3.10/test" \
    "$temp_root/python-home/lib/python3.10/tkinter" \
    "$temp_root/python-home/lib/python3.10/turtledemo" \
    "$temp_root/python-home/lib/python3.10/idlelib" \
    "$temp_root/python-home/lib/python3.10/ensurepip" \
    "$temp_root/python-home/lib/python3.10/distutils"

  copy_runtime_site_packages "$temp_root/site-packages"
  copy_tree_clean "$PROJECT_ROOT/packages/assistant-core/src/assistant_core" "$temp_root/app/assistant_core"
  copy_clean "$ROOT_DIR/local_bridge.py" "$temp_root/bridge/local_bridge.py"
fi

cat > "$temp_root/run_bridge.sh" <<'EOF'
#!/bin/zsh
set -euo pipefail

RUNTIME_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_SUPPORT_DIR="$HOME/Library/Application Support/Cleo"
mkdir -p "$APP_SUPPORT_DIR" "$APP_SUPPORT_DIR/huggingface"

export PYTHONHOME="$RUNTIME_DIR/python-home"
export PYTHONPATH="$RUNTIME_DIR/site-packages:$RUNTIME_DIR/app"
export CLEO_APP_PACKAGES="$RUNTIME_DIR/app"
export CLEO_STATE_FILE_PATH="$APP_SUPPORT_DIR/state.json"
export HF_HOME="$APP_SUPPORT_DIR/huggingface"
export TRANSFORMERS_CACHE="$APP_SUPPORT_DIR/huggingface"
export HUGGINGFACE_HUB_CACHE="$APP_SUPPORT_DIR/huggingface/hub"
export TOKENIZERS_PARALLELISM="false"

if [[ -f "$APP_SUPPORT_DIR/.env" ]]; then
  set -a
  source "$APP_SUPPORT_DIR/.env"
  set +a
fi

exec "$RUNTIME_DIR/bin/python3" "$RUNTIME_DIR/bridge/local_bridge.py" "$@"
EOF
chmod +x "$temp_root/run_bridge.sh"
xattr -cr "$temp_root" 2>/dev/null || true
printf '%s\n' "$local_manifest" > "$temp_root/.manifest"

rm -rf "$RUNTIME_ROOT"
mv "$temp_root" "$RUNTIME_ROOT"
xattr -cr "$RUNTIME_ROOT" 2>/dev/null || true

echo "Cleo runtime installed."
echo "  runtime: $RUNTIME_ROOT"
echo "  state: $APP_SUPPORT_DIR/state.json"
echo "  mode: $RUNTIME_MODE"

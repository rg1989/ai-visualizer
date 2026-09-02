#!/bin/bash
# Runner setup for the wake-train workflow (Linux x86_64, Python 3.11). Idempotent.
set -euo pipefail
cd "$(dirname "$0")"
sudo apt-get update -qq >/dev/null && sudo apt-get install -y -qq espeak-ng >/dev/null
pip install -q --upgrade pip
pip install -q torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cpu
pip install -q "numpy>=2,<3" "scipy<1.17" soundfile pyarrow pyyaml tqdm onnx onnxruntime \
  scikit-learn requests speechbrain==0.5.14 audiomentations==0.33.0 torch-audiomentations==0.11.0 \
  acoustics==0.2.6 pronouncing==0.2.0 deep-phonemizer==0.0.19 mutagen==1.47.0 torchinfo==1.8.0 \
  torchmetrics==1.2.0 webrtcvad piper-tts==1.3.0
[ -d openwakeword ] || git clone -q https://github.com/dscripka/openwakeword
git -C openwakeword checkout -q 368c0371
pip install -q -e ./openwakeword --no-deps
[ -d piper-sample-generator ] || git clone -q --branch v3.2.0 --depth 1 https://github.com/rhasspy/piper-sample-generator
cp generate_samples.py piper-sample-generator/generate_samples.py
mkdir -p piper-sample-generator/models
[ -s piper-sample-generator/models/en_US-libritts_r-medium.pt ] || curl -fsSL --create-dirs -o piper-sample-generator/models/en_US-libritts_r-medium.pt \
  https://github.com/rhasspy/piper-sample-generator/releases/download/v2.0.0/en_US-libritts_r-medium.pt
for f in embedding_model.onnx melspectrogram.onnx; do
  [ -s openwakeword/openwakeword/resources/models/$f ] || curl -fsSL --create-dirs -o openwakeword/openwakeword/resources/models/$f \
    https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/$f
done
ls -la piper-sample-generator/models/ openwakeword/openwakeword/resources/models/
for w in "show dan" "show dahn" "showdan" "shodan"; do printf '%-10s ' "$w"; espeak-ng -q --ipa -v en-us "$w"; done
python -c "import torch, openwakeword, openwakeword.data, openwakeword.utils, piper, speechbrain, audiomentations, pronouncing; print('imports ok, torch', torch.__version__)"

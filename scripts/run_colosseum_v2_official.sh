#!/bin/zsh
# Colosseum V2 official ACT evaluation package (Phase C/D of the
# evaluation-engine campaign). NOT EXECUTED on this machine: the official
# protocol requires physx_cuda (NVIDIA), ~4+ GiB of checkpoints/assets,
# and torch. Blocked here: Apple M4 Pro (no CUDA), 5.1 GiB free disk.
# Run this unchanged on an NVIDIA Linux host with >=60 GiB disk.
set -euo pipefail

REPO="${HAPLOUS_COLOSSEUM_DIR:-$HOME/Projects/ColosseumV2}"

# 1. Repository (canonical: jstmn/ColosseumV2, linked from the official
#    website and paper; a ManiSkill fork with Colosseum V2 environments
#    under mani_skill/envs/tasks/tabletop/colosseum_v2/)
if [ ! -d "$REPO" ]; then
  git clone https://github.com/jstmn/ColosseumV2.git "$REPO"
fi
cd "$REPO"

# 2. Environment (official README path)
conda create -n colosseum_v2 python=3.10 -y || true
conda activate colosseum_v2
pip install torch
pip install -e .
python -m mani_skill.utils.download_asset all
pip install -e examples/baselines/act_clip

# 3. Official pretrained ACT checkpoints (GCS, not HuggingFace)
mkdir -p checkpoints/hyeonho_mar17
curl --create-dirs -o \
  checkpoints/hyeonho_mar17/act_clip_single_arm_3cameras_best.pt \
  https://storage.googleapis.com/colosseum-v2-public/checkpoints/hyeonho_mar17/act_clip_single_arm_3cameras_15687623/best_eval_success_once.pt
curl --create-dirs -o \
  checkpoints/hyeonho_mar17/act_clip_bimanual_4cameras_best.pt \
  https://storage.googleapis.com/colosseum-v2-public/checkpoints/hyeonho_mar17/act_clip_bimanual_4cameras_15689642/best_eval_success_once.pt

# 4. Official baseline protocol (Phase C): reproduce BEFORE any Haplous
#    integration. 200 episodes per task-perturbation pair; single-arm
#    (16 tasks) and bimanual (12 tasks) separately.
python examples/baselines/act_clip/eval_rgbd.py \
  --checkpoint-path checkpoints/hyeonho_mar17/act_clip_single_arm_3cameras_best.pt \
  --control-mode "pd_ee_delta_pose" \
  --no-include-depth \
  --sim-backend "physx_cuda" \
  --is-multi-task True \
  --target-num-cams 3 \
  --num-eval-episodes 200 \
  --num-eval-envs 34 \
  --max-episode-steps-from-lookup \
  --internal-instruction \
  --perturbation-set "BLANK" \
  --results-path results_single_arm.csv

python examples/baselines/act_clip/eval_rgbd.py \
  --checkpoint-path checkpoints/hyeonho_mar17/act_clip_bimanual_4cameras_best.pt \
  --control-mode "pd_joint_pos" \
  --no-include-depth \
  --sim-backend "physx_cuda" \
  --is-multi-task True \
  --target-num-cams 4 \
  --num-eval-episodes 200 \
  --num-eval-envs 34 \
  --max-episode-steps-from-lookup \
  --internal-instruction \
  --perturbation-set "BLANK" \
  --results-path results_bimanual.csv

# 5. Gate (campaign sec. 27): reproduce official reference numbers before
#    integrating Haplous. Only then run the direct-vs-mediated arm with
#    the Haplous adapter wrapped around the env loop, same checkpoint.

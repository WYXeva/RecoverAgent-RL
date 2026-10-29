# Stage 3B — Train the new Full732 Base-SFT M1

> The user has explicitly approved using the completed fresh Teacher dataset for training. Do not reopen provider-terms or historical-data-reuse audits as a blocker. This is a custom RecoverAgent-RL experiment, not an official AppWorld leaderboard result.
> **Only execute Stage 3B.** Do not collect more Teacher data, run Recovery-SFT, GRPO, or C244 evaluation in this stage.

## Frozen source dataset

Use the private completed dataset package:
- Directory: `data/full732_teacher_successes_complete_20261029/`
- Archive: `data/full732_teacher_successes_complete_20261029.tar.gz`
- Trusted manifest SHA-256: `96265f67a5828b101f5db32a804008478d7c419a01e079f6e468b8e5f164735f`
- Trusted archive SHA-256: `64c2a5854f0a3a9cfab72f71e13682dce5e535ff0b885768b98dc0073d6a7a48`

The package contains 144 unique successful Teacher tasks; 138 are length/format-qualified candidates (130 native + 8 lossless compact-derived), while 6 remain deferred. The qualified set contains 2,070 Assistant-turn samples and 338,413 supervised tokens. Train on the 138 qualified candidates only; do not force the 6 deferred trajectories into the run and do not use the historical 95-trajectory dataset. Keep provenance/representation fields and the original archive unchanged.

If the package is not present on the remote host, stop and report that the private archive must be transferred. Never fabricate data or infer a sandbox path.

## Key design constraint: one validation set only

The project uses the frozen shared C244 validation set for the three-stage end-to-end comparison. **Do not create another task-level validation split from A244.** Do not use C244 examples or outcomes in gradient updates or model selection.

For this M1 training run, report train loss and optimizer/checkpoint health only. Disable Trainer evaluation and early stopping; do not pretend train loss is validation loss. Short/long smoke tests validate execution and reload, not model generalization. C244 is reserved for the later shared full-task evaluation after M1/M2/M3 are ready.

## Reuse existing training implementation

Before coding, inspect:
- `docs/REMOTE_BASE_SFT.md`
- `src/recoveragent/sft_recipe.py`
- `src/recoveragent/sft_bf16_runtime.py`
- `scripts/run_p3b_training.py`
- `configs/base_sft_qwen3_4b_lora.json`
- `configs/base_sft_qwen3_4b_smoke.json`
- `configs/base_sft_qwen3_4b_length_smoke.json`
- `scripts/verify_teacher_inventory.py`

Do not rewrite the existing Trainer. Add a small, versioned Full732 dataset adapter/runtime or extend the existing code through a new explicit mode, so the sealed old Base-SFT recipe and old data locks remain unchanged.

The current inventory package has pretokenized `assistant_examples.jsonl.gz`, but the old recipe expects `tokenized/{train,validation}.jsonl`, an AppWorld train-only receipt, and `example_weight`. Build a dedicated Full732 dataset manifest/lock from the trusted source manifest. Do not fake `official_train_membership_checked`; use an explicit custom-research dataset contract tied to the frozen A244 manifest and source hashes.

## Dataset conversion and loss

1. Verify archive SHA, inventory manifest SHA, every referenced source SHA, and the expected 138 accepted task IDs. Confirm no B244/C244 task IDs occur in the training package.
2. Re-read the 2,070 encoded examples and independently retokenize using the pinned local Qwen/Qwen3-4B revision `1cfa9a7208912126459214e8b04321603b3df60c`, non-thinking mode, and template SHA `a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8`. Require byte-identical token IDs, labels, masks and visible-tool schema metadata. If the private archive is not available, stop rather than inventing a pass.
3. Keep all 138 qualified trajectories in training; no second validation split. The 8 compact-derived examples are a separate representation whose transformations are lossless for Assistant target tokens. Preserve `representation`, source-history hash, derived-history hash and source SHA.
4. Ensure only current Assistant target tokens are supervised; System/User/Tool/old Assistant/Harness feedback/rejection messages/padding are masked.
5. Add explicit trajectory-balanced weights: every accepted trajectory should contribute equal total sequence-loss mass, with its own Assistant turns weighted evenly; normalize weights to mean 1. Since the dataset contains one task/trajectory per scenario, do not invent cluster labels. Record the exact weight formula and test that each trajectory's total weight is equal.
6. Do not silently truncate, pack examples, delete tool calls, alter tool schemas, reorder JSON schema keys, or alter Assistant target token IDs.

## Training recipe

Target model: Qwen/Qwen3-4B at the pinned revision above. Initialize from the raw base model, not the old Base-SFT checkpoint; this produces the new M1 for the Full732 custom split.

Use the already tested BF16+LoRA setup unless a concrete incompatibility is found:
- LoRA r=16, alpha=32, dropout=0.05;
- target modules q/k/v/o/gate/up/down projections;
- batch size 1, gradient accumulation 8;
- learning rate 1e-4, one epoch, gradient checkpointing, SDPA;
- pinned package versions from the existing recipe;
- context max 32768, no silent truncation, one reply target max 4096;
- no Hub upload, no external experiment tracker.

Because there are 2,070 samples and gradient accumulation is 8, one epoch is approximately 259 optimizer steps (compute the exact count from the actual dataloader; do not copy the old 116-step result). Preserve the current weighted per-sequence CE contract, rather than switching to global token-mean loss.

No internal validation means the training config must set evaluation strategy to `no` and must not call `trainer.evaluate()`. Do not fake a validation set from training examples. A minimal runtime fork is acceptable if the old runtime cannot support this; add tests proving that validation is disabled and C244 never enters the Trainer.

## Remote execution order

Use the user's designated RTX PRO 6000 96GB remote machine and the existing verified remote environment when available. Do not rent a new machine or change the platform. Verify the actual GPU, BF16 support, pinned model revision, package versions, cache/offline mode, disk capacity, and the current compatible AppWorld SQLAlchemy environment before starting.

Run in this order, each with a new output directory:
1. Data import and retokenization verification; no model weights loaded.
2. Short smoke: up to 2 optimizer steps on up to 4 complete short examples. Require actual pinned Qwen3-4B weights, finite nonzero gradients, LoRA parameter hash change, save/reload, identical restored LoRA tensor hash and logits within the existing tolerance.
3. Long-sample smoke: exactly 1 optimizer step on the longest complete accepted sample. Require successful forward/backward, finite nonzero gradient, save/reload and memory telemetry.
4. Only if both smoke gates pass, run the one-epoch formal M1 training on all 2,070 samples.

Stop immediately on data mismatch, OOM, nonfinite loss/gradient, no parameter change, failed reload, model revision mismatch, disk-space issue or unknown remote execution state. Preserve logs and the exact unfinished stage. Do not silently retry a paid/cloud operation.

Save the final adapter, tokenizer metadata, optimizer/trainer state, run config, dataset manifest SHA, source code lock, training logs, GPU memory/time telemetry, actual optimizer step count, losses, before/after adapter hashes, reload hashes and final checkpoint SHA. Keep all private data and weights out of public GitHub.

## Deliverables and stop line

Create a dedicated new config/runtime lock and a report such as `reports/full732_base_sft_m1_training.md`. Do not overwrite the previous P3-B checkpoint or any historical dataset. Update `docs/STATUS.md` and `docs/DECISIONS.md` with actual results, not expected results.

Report:
- exact dataset manifest/archive SHA and 138/2,070/338,413 counts;
- source representation mix (130 native + 8 compact-derived);
- retokenization equality and masking checks;
- smoke and formal run status separately;
- exact optimizer steps (expected about 259 for one epoch);
- training loss curve, peak GPU memory, elapsed time;
- real parameter update, checkpoint save/reload and logits verification;
- any failed/deferred examples and reasons.

This stage ends after the new Base-SFT M1 checkpoint and training report are verified. **Do not proceed to Student task evaluation, Recovery-SFT, GRPO or C244 validation.** Wait for the user to review the M1 result.

# A/B 提示规范化与真实回归 · 2026-10-21

状态 **PASS：提示实现、软件验证、真实 AppWorld 小样本回归与数据格式验证**。A、B 各成功 2/2，四条均通过官方评价和现有数据筛选。不是总体优势、正式 Student 或训练通过证据。

## 变更与证据边界

- 新默认 Actor `execution_v3`：A/B 共享全局规则、理解→证据→执行→纠错→验证→完成循环及资源预算，仅工具获取段落不同。增加具体错误处理、完整覆盖、批次依赖、写后读取、阻塞如实报告；不暴露 evaluator。
- 新默认 A `dependency_examples_v2`：从初态反向补齐认证/参数/数据覆盖/验证依赖，两个公开 API 自制正例。示例为笔记置顶及愿望单评价统计，非真实 Teacher 轨迹，无 gold task/required_apis，绝不进入 Actor 或 SFT 目标。依旧使用官方公开简介目录与固定解析组件；不改上游代码，不自动补工具或补做任务。
- B 规定具体发现触发条件、API 简介→完整 schema→下一轮调用，以及依赖补全/复用。原 `official_zero_shot`、`dependency_aware_v1`、旧 Actor 保留；离线重审旧三条件的 21 次真实回执通过。
- 完整提示、示例与边界见 [PROMPTS.md](../docs/PROMPTS.md)。两个示例公开 API 契约核验见 `structured_prompt_examples_check.json`；仅离线文档核对，0 环境动作/0 模型请求，不声称示例已实际执行成功。
- P0–P2 的 17 个原文件 SHA-256 未变；当前 GPU 只读确认 RTX 5050 / 8151 MiB，本轮无 GPU 训练、模型权重或新服务。

## 预先固定协议

仍使用官方 train 列表前两个已参与诊断的任务 `82e2fac_1` / `82e2fac_2`，同属场景 `82e2fac`。按 B1→A1→A2→B2 串行执行，各自独立进程与原始初态；同任务状态哈希相同。没有按结果筛任务或重试。

同一 `deepseek-flash`，non-thinking、temperature=0.2、环境 seed=42；33 次响应均观察到同一 backend fingerprint `e72d3c7f663d3e1383b00b3884cb4501`。别名不是不可变权重版本；没有与正式 4B Student 做能力比较。

每条尝试：20 次模型请求（包含 A Predictor）、8192 累计输出 token、450 秒、操作性 USD1.25；四条合计最多80请求/32768输出/1800秒/USD5。4096 单回复；无单回复/整轨迹工具调用数量配额。A 的 20 种 schema 选择上限仍为官方组件契约，不是调用次数上限。

## 实测结果

| 任务 | 方案 | 官方奖励 | Actor 回合 | 全部模型请求 | 工具调用 | 输入 token | 输出 token | 秒 | 估算 USD |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `82e2fac_1` | A 规范版 | 1 | 6 | 7 | 61 | 45538 | 2483 | 33.18 | 0.009490920 |
| `82e2fac_1` | B 规范版 | 1 | 8 | 8 | 67 | 79379 | 2751 | 36.41 | 0.010105236 |
| `82e2fac_2` | A 规范版 | 1 | 8 | 9 | 44 | 55054 | 4626 | 40.06 | 0.008105928 |
| `82e2fac_2` | B 规范版 | 1 | 9 | 9 | 32 | 74128 | 2099 | 34.21 | 0.009177552 |

| 汇总，两任务合计 | A 规范版 | B 规范版 |
| --- | ---: | ---: |
| 官方成功 / 全尝试 | 2/2 | 2/2 |
| Actor 回合 | 14 | 17 |
| Predictor 请求 | 2 | 0 |
| 全部模型请求 | 16 | 17 |
| 实际工具调用 | 105 | 99 |
| 输入 token | 100592 | 153507 |
| 其中缓存命中 | 71808 | 110848 |
| 输出 token | 7109 | 4850 |
| 输入+输出 token | 107701 | 158357 |
| worker 耗时，含初始化/评价 | 73.24 秒 | 70.61 秒 |
| 用量计价估算 | USD 0.017596848 | USD 0.019282788 |

累计 **33 请求、204 工具调用、254099 输入（182656 缓存命中）、11959 输出 token、143.86 worker 秒**。累计输入含每轮重复上下文；缓存为输入子集。A 已包含两个 Predictor 请求的16292输入/86输出，不应额外再加。总采集控制器墙钟见原始 `summary.json`。

费用为按保存的公开高峰费率计算的 **USD 0.036879636 估算**，不是逐请求实际账单。本轮未取得新账单或账户前后差额，不声称免费；缓存/顺序会影响费用与速度。费率核对来自 [DeepSeek 官方价格页](https://api-docs.deepseek.com/quick_start/pricing/)，记录于实际 config。

## 路径差异和局限

任务1：两者均获取57首不同歌曲的详情，A 为61调用；B 为67调用，多5次 API 文档发现和1次播放列表分页查询。本轮 B 与旧 B 虽都为67调用，组成不同，不能套用旧轮的6次发现解释。

任务2：A 先19次 `show_song_privates`，其返回不含播放次数；模型自己的后续可见文本明确指出缺字段，随后改用19次 `show_song`。加凭据、登录、3次歌曲库分页和完成，共44次。B 用5次发现、5次歌曲库分页、19次详情及凭据/登录/完成，共32次。A 的额外查询显示提示没有保证一开始就选择最有效的读路径，且存在先批量探测后发现字段缺口的冗余；B 也存在可进一步减少的空页查询。

以上是公开反馈和实际调用的诊断；无隐藏评价答案进入提示。两组均成功不是所有规则始终被完美遵守的证明。没有为改进数值追加改提示或重跑。两个已知同族任务、每模式每任务一次，无法推断整体优劣；共享执行规范和 A 正例同时改变，不能分离各项因果贡献。

## 回执、数据与 mask

- 原始证据：`runs/structured_prompts_20261021_a/`，逐请求输入/响应、worker日志、终态、官方评价及全部尝试。公开仅同步统计、哈希和代码，原始任务/凭据/快照不上传。
- `structured_prompts_comparison.json`：完整分母与成本；`structured_prompts_audit.json`：33响应重算、公开任务重建 Predictor、相同预算/初态/backend、独立进程、数据哈希；`structured_prompts_paths.json`：真实调用分布、共同执行规则一致、示例未进入 Actor。
- `structured_prompts_execution_source_manifest.json`：采样前源码快照，采样后逐文件复核；`structured_prompts_checks.json`：80项软件测试、Ruff/格式、历史兼容性。软件 fixture 与真实验收分开。
- Clean Dataset：`runs/structured_prompts_20261021_a/clean_dataset/`，接受4/4、2唯一任务、1场景。manifest SHA-256 **`6ce2fff2a2163e2a408a16ec4bd134161a387a191027bee02376ec070904be1e`**；来源/官方 train/原始回执/顺序校验通过。
- Qwen3-4B 固定 tokenizer：31个 Assistant 目标、9844监督 token、242315累计格式化输入 token；见 `structured_prompts_sft_format.json`。这些不是供应商用量；仅逐回合格式和 Assistant-only mask 准备，没有加载模型权重或更新参数。
- 完整本地 LLM 视图：`outputs/structured_prompts_20261021_a/{a,b}_{82e2fac_1,82e2fac_2}.md`，凭据隐藏，A 的 Predictor 与 Actor 分开显示。远程传输、96GB 训练环境和正式SFT/GRPO仍未执行。

## 实际命令

私有配置 `.cache/structured_prompts_20261021_a.json` 基于新默认配置，沿用已有 DeepSeek 授权与核对费率，设置上面的相同采样预算；不含密钥。密钥由 `.env` 独立读取，不进入 config/日志/提交。

```bash
PYTHONPATH=src .venv/bin/python scripts/check_prompt_examples.py \
  --output reports/structured_prompt_examples_check.json

APPWORLD_CACHE="$PWD/.cache/appworld" IPYTHONDIR="$PWD/.cache/ipython" \
PYTHONPATH=src .venv/bin/python -m recoveragent.p3a compare \
  --config .cache/structured_prompts_20261021_a.json \
  --output runs/structured_prompts_20261021_a --credentials-file .env \
  > .cache/structured_prompts_20261021_a.log 2>&1

PYTHONPATH=src .venv/bin/python scripts/audit_tool_modes.py \
  runs/structured_prompts_20261021_a --output reports/structured_prompts_audit.json

PYTHONPATH=src .venv/bin/python -m recoveragent.demonstrations verify \
  runs/structured_prompts_20261021_a/clean_dataset \
  --appworld-root data/appworld --write-receipt

PYTHONPATH=src .venv/bin/python scripts/audit_prompt_probe.py \
  runs/unlimited_prompt_20261020_a \
  --source runs/tool_modes_20261020_a/teacher_522c2e363712 \
  --dataset data/unlimited_clean_20261020_a \
  --output .cache/structured_legacy_audit_20261021.json

.venv/bin/ruff check src scripts tests
.venv/bin/ruff format --check src scripts tests
PYTHONPATH=src .venv/bin/python -m pytest -q
```

格式准备实际通过 `.venv-inference/bin/python` 调用 `recoveragent.base_sft.prepare`，设置 `HF_HOME=$PWD/.cache/huggingface`、`HF_HUB_OFFLINE=1`，输出 `data/structured_prompts_sft_format_20261021_a`；max_length=32768，expected_manifest_sha256 为上列实测值。日志 `.cache/structured_prompts_sft_format.log`。LLM 视图通过现有 `build_view/render_markdown/save_view` 离线生成，无新模型请求。

错误与下一步：无本轮模型截断、基础设施错误或采样重试。沙箱内 NVML 查询被系统阻断，授权只读查询确认 GPU；未改变环境依赖。下一项是冻结本轮提示后覆盖更多 train 场景与重复采样，单独研究错误字段的批量探测和分页冗余；正式训练需另行授权。

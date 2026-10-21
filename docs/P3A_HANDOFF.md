# P3-A 执行与远程数据交接

本文件记录 v0.4 的原始数据交接，既有包及哈希保持原样；当前 v0.8 扩样、冻结协议和新包哈希见 `docs/TEACHER_EXPANSION.md`，双工具模式见 `docs/TOOL_MODES.md`。正式 Student 为 Qwen/Qwen3-4B；本文件没有训练入口。初次实际结果见 `reports/p3a_report.md`：当时得到 1 条官方成功 Teacher 示范和 8 条 4B Assistant 监督格式样本；后续数据与状态见 `docs/STATUS.md`，没有启动训练。下文旧试点的数量预算属于历史，不用于新扩样。

## Teacher 配置与正常任务采集

`configs/p3a_teacher.json` 是默认禁用付费调用的配置。模型固定为本地已有凭据所对应的 `deepseek-flash`，使用原生结构化 Function Calling 和 non-thinking。只从官方 train 任务初始状态执行，独立进程串行运行，业务工具均由 Teacher 决定，评价与快照不进入 Actor。

凭据通过 `--credentials-file .env` 或环境变量读取；只使用配置中的 `api_key_env` 获取密钥，endpoint/model 以版本化配置为准，不随 dotenv 静默改变。dotenv 按数据解析，不执行 shell，不自动展开变量。请求日志没有 Authorization header；HTTP 错误只记录状态码，不保存可能回显凭据的错误正文。

真正调用前必须具备本轮费用授权、金额/币种、已核验费率及来源/日期。在新的本地配置中填写 `authorization.approved/reference/max_cost` 和 `teacher.pricing`；不要把拥有 API Key 等同于付费授权。默认配置保持关闭。本次用户回复“费用不限”后，已用忽略的本地配置完成试点；公开仓库不携带凭据或可直接运行的已授权费用配置。默认禁用不能被误读为本次授权仍缺失。

```bash
export PYTHONPATH="$PWD/src"
export APPWORLD_CACHE="$PWD/.cache/appworld"
export IPYTHONDIR="$PWD/.cache/ipython"
.venv/bin/python -m recoveragent.p3a preflight \
  --config configs/p3a_teacher.json --credentials-file .env \
  --output runs/p3a_new_preflight.json

# 新 run 的命令模板；本次已执行的 A/B/C 命令见阶段报告。
.venv/bin/python -m recoveragent.p3a collect \
  --config data/p3a_authorized_config.json --credentials-file .env \
  --output runs/p3a_new_run
```

试点限制：最多 2 个任务、80 次模型请求、32768 总生成 token、30 分钟；默认每任务最多 40 回合、40 次真实工具调用、16384 生成 token、15 分钟；实现硬上限为 80 回合/80 调用。C 轮在扣除 A/B 用量后采用 66 回合/80 调用，未扩大累计模型预算。付费请求无自动重试。每次使用新 run ID。每次请求前按整个供应商上下文上限预留最坏输入费用，未知响应保留费用/token 预留并停止试点。

原生批量调用先整体校验当时 schema/参数/ID/预算，再逐个执行；必须收齐全部结果才进入下一回合或保存终态。`parallel_tool_calls=false` 在实测服务上不能强制单调用。P3 终态格式不作为 P0/P2 恢复接口输入。

原始 `requests/*.request.json` / `*.response.json`、可见多轮 messages、当时的工具 schema、逐回合信息、终态快照和独立官方评价均留在本地。保存终态并关闭 world 后才调用官方 `evaluate_task`，仍使用原任务参照。

`teacher.revision` 当前是供应商文档对应的版本说明，**不是可固定的权重 commit**。真实执行会保存响应 model 和 system_fingerprint，并拒绝运行内变化；即便如此，也不保证 API 供应商以后继续提供完全相同权重。正式数据报告必须保留这一限制，不能把文档版本当作已执行证明。

## 筛选与费用

只有 `teacher_normal`、原始初态、官方 train 来源、官方严格成功、消息/工具调用完整且可与真实请求回执对应的轨迹能进入示范集。拒绝格式错误、超限/未完成批次、截断、确定的约束违规和连续三次相同调用且相同返回的无进展循环。循环检查是版本化启发式；安全只覆盖官方终态检查，额外风险标记未测。

普通 Assistant 文本是合法消息，不伪装成工具调用；若 Teacher 返回无调用的最终文本，则正常结束并官方评价。局部工具执行错误不自动等于整任务失败；原始尝试和成本始终保留。

费用字段区分：真实供应商 usage、按已核验费率计算的 `usage_priced_cost`、中断后的 `unknown_cost_reservation`、以及未获得账单时为 null 的 `billed_cost`。不能把估算费用写成实际扣款。本次记录的 USD 0.027231792 是高峰费率估算；官方账户前后减少 CNY 0.04 是账户级观察差额，未排除并发使用，见 `reports/p3a_observed_cost.json`。零尝试时成功率为 null，不能报告成 0%。

## 可移植数据包

采集器自动导出 `runs/<run>/clean_dataset/`。也可独立导出和核验：

```bash
.venv/bin/python -m recoveragent.demonstrations export runs/P3A_RUN \
  --output data/CLEAN_DATASET
.venv/bin/python -m recoveragent.demonstrations verify data/CLEAN_DATASET \
  --expected-manifest-sha256 EXPECTED_SHA256 --write-receipt
```

本次真实非空包为 `data/p3a_clean_20261020_all/`，含 6 次尝试证据和 1 条成功示范。可信 manifest SHA-256 为 `464ea9bef9e794082b266ae6ed53bc5be358eba3e0c74a026325526a70214b30`。合并多个采集 run 时使用 `--include-run`，保留协议调试失败和全部成本。初始 `data/p3a_empty_handoff_20261020_a/` 是授权前空包历史，不能用于训练。

包结构：

- `demonstrations.jsonl`：未分词的完整多轮消息、逐回合可见 schema、Teacher/任务/场景/版本和来源哈希。
- `evidence/<trajectory_id>/`：控制侧原始轨迹、请求/响应及官方评价；不传给训练模型。
- `attempts.json`：全部成功、失败、失效尝试，过滤原因和费用分母。
- `source_configs/`：实际 run 配置及 SHA-256 来源，不含凭据。
- `manifest.json` / `manifest.sha256`：数据身份、Teacher、目标 Student、AppWorld 版本、每文件 SHA-256、schema 哈希、样本顺序与成本。

校验拒绝新增/丢失/改写文件、符号链接、顺序变化、跨划分数据、来源不一致、无法重算的统计。必须从可信交接记录获得 manifest SHA-256；一起被恶意替换的文件及旁边校验和不能构成真实性证明。

只有拿到已授权主机/目录后才执行 SSH/rsync。以下传输命令**未执行**：

```bash
rsync -a --checksum data/CLEAN_DATASET/ "$REMOTE_HOST:$REMOTE_DATASET_DIR/"
# 在远程 AppWorld venv 中执行相同 verify 命令，传入可信 EXPECTED_SHA256；
# 不沿用本机绝对路径，使用远程实际 AppWorld root。
```

不上传原始任务、示范、隐藏评价、快照或凭据到公开 GitHub。本轮已验证真实非空包复制到 `data/p3a_transfer_rehearsal_20261020/` 后的哈希、成员、来源及顺序；没有远程迁移或跨主机快照验收。

## Qwen3-4B Base-SFT 格式准备

固定 model/tokenizer revision 为 `1cfa9a7208912126459214e8b04321603b3df60c`，non-thinking，工具模板 SHA-256 为 `a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8`。数据包不带 1.7B token IDs。

远程准备命令模板（未在远程执行，不包含训练）：

```bash
python3 -m venv .venv-prep
.venv-prep/bin/python -m pip install -r requirements/data-prep.lock.txt
export PYTHONPATH="$PWD/src"
export HF_HOME="$PWD/.cache/huggingface"
.venv-prep/bin/python scripts/download_base_tokenizer.py \
  --output reports/remote_tokenizer_download.json
# 先在 AppWorld venv 运行 verify --write-receipt，再在 tokenizer venv 执行：
HF_HUB_OFFLINE=1 .venv-prep/bin/python -m recoveragent.base_sft data/CLEAN_DATASET \
  --expected-manifest-sha256 EXPECTED_SHA256 --output data/BASE_SFT_TOKENIZED
```

每个 Assistant 回合产生一条样本，使用该回合的完整已见历史及当时的工具 schema；只监督本回合 Assistant 文本、函数名、参数与结束格式 token。此前所有 Assistant 回合、system/user/tool 和 padding 都 mask 为 -100。完整轨迹的各 Assistant 回合各监督一次，因此仍覆盖全部正常 Teacher 行为。固定的非思考提示 token 不作为目标。

每回合同时检查字符串与 token 级生成前缀相等，拒绝静默截断；数据包必须绑定指定 4B revision 和 non-thinking 配置。该设计避免全轨迹用最终 schema 而泄露未来工具，也避免 Qwen 多轮重渲染改写旧思考块造成 mask 偏移。按 task/scenario 分组，不随机拆同源回合；当前不做 packing、不启动 optimizer。

训练数据代码已准备，合成单调用/批量检查和真实成功轨迹的 8 个 Assistant 样本 mask 均通过。真实样本覆盖 29 次工具调用、1146 个监督 token，最大 12115 token；未加载权重或计算梯度，不是实际 SFT 通过。远程仍需检查实际 GPU/驱动/CUDA/存储、AppWorld worker、训练栈与将来的真实参数更新/重载。可运行：

```bash
# 未来在实际远程机器运行；本轮只运行了 --scope local-preparation。
python scripts/remote_preflight.py --scope remote --output reports/remote_environment.json
```

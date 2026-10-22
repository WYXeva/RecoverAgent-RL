# Harness v1 工程实现与验证（2026-10-22）

基线：`473693c3b768940679a2367d149fd54293e27496`，开发分支`codex/p0-p2-recovery`。本轮用户授权工程化失败诊断方案；保持正式Student为Qwen/Qwen3-4B，不启动SFT/GRPO。

总体：**实现/软件/本机真实环境复验PASS；真实Teacher自纠正效果BLOCKED（余额不可用）**。正式数据仍95条，数量目标PARTIAL；P3-B及以后NOT_STARTED。

## 1. 实现结果

新增`public_protocol_correction_v1`，实现方案与使用契约见`docs/HARNESS_V1.md`。Teacher和Student共用整批检查、显式反馈、最多2次通用纠正机会，保留原预算。A工具集保持固定，B只能下一回合发现新Schema。未开放API、非法参数/调用ID、已观察同应用token抄错、截断回复均在执行前拒绝，整批零执行；不替模型登录、改参数、补业务动作或猜答案。

最多2条提示性无进展消息；数值格式依旧仅允许紧接着的一次同值纠正。非法原始生成完整保存在request/response回执，后续合法消息中只追加显式harness反馈，拒绝回合不监督。回执审计重新解析拒绝原因并检查历史覆盖；LLM查看器分开展示真实工具结果与harness反馈。

HTTP状态安全记录，401/402/403立即停止，连续两次传输/worker故障停止；未知费用保留预留，batch停止传到campaign，未执行任务单列。没有自动重复付费请求。Student输入与回复共同受32768限制、回复最多4096；新Teacher本地JSON字节保护512KiB不替代32k训练检查。

配置`configs/p3a_harness_v1.json`默认B，原固定10任务清单保留作未来诊断，不按结果换任务。私有授权配置已准备在`.cache/harness_v1_authorized_20261022.json`，未启动采集。每轨迹40请求/16384输出/600秒/USD1，10任务批次400请求/163840输出/6000秒/USD10，工具数量不限。新源码/参数锁`configs/harness_v1_protocol_lock.json`；旧锁不改。

## 2. 软件、格式与真实请求离线检查

| 检查 | 本轮真实结果 | 能支持的结论 |
| --- | --- | --- |
| 完整软件测试 | 179 passed | 包括24项新增harness测试；含合成world/transport，不是模型成绩 |
| Ruff / 格式 | 通过，83个Python文件 | 软件静态质量 |
| 固定Qwen3-4B tokenizer | 1个合成纠正目标、24监督token、2161完整序列token | 拒绝回合不成为目标、前缀/padding屏蔽、Student解析回读通过；0权重更新 |
| 封存来源包复验 | 18个包通过 | 更新后的验证器仍能核验原始消息/回执/筛选/成本；不改原包 |
| 历史请求检查 | 118尝试、1235完整响应；7不可见工具、6截断、51含未观察token | 涉及17条历史失败；58条原成功没有被新检查误拒，不是修复后成功率 |

证据：`harness_software_checks_20261022.txt`、`harness_qwen_mask_20261022.json`、`harness_historical_packages_20261022.json`、`harness_historical_detection_20261022.json`。原25个缺响应请求不在1235完整响应中，不能重建缺失的HTTP状态。离线检查不发模型请求或执行工具。

## 3. AppWorld兼容修复与独立官方评价

独立`.venv-harness`使用SQLAlchemy2.0.52、SQLModel0.0.48、greenlet3.2.4，pip check通过；其余包复用原环境，完整新锁在`requirements/appworld-harness.lock.txt`。原`.venv`仍SQLAlchemy2.1.5，原依赖锁不变，未编辑AppWorld源码/验证器。

内存URI往返由失败变为通过。此前`c901732_2`播放失败轨迹的24次真实动作全部返回与原记录一致，关闭异常抑制的官方评价成功；另一次独立24动作重放保存2个model-hash条目，状态保存/恢复一致，在新进程官方评价成功。两次均为脚本历史动作诊断，**不计新Teacher示范，也不反写原失败分数**。

新采集使用独立评价进程，通过只读Python profile观察官方原TestTracker的异常类型，不修改断言或评分。普通AssertionError保留官方失败，非断言/顶层异常单列evaluation_error并暂停采集；这些异常仍需诊断，不能全部归因模型或环境。

证据：`harness_uri_probe_20261022.json`、`harness_playback_probe_20261022.json`、`harness_state_and_evaluation_20261022.json`、`harness_environment_20261022.json`。两条完整官方评价原文仅私有保存，公开报告仅包含状态、异常类型和来源哈希。

A/B工具边界真实探针也通过：两个隔离world中，包含“先发现、同批调用未开放API”的批次均零执行且状态不变；下一条合法发现调用各真实执行1次，B下一轮出现新Schema，A仍保持固定集合。这里只测试固定公开工具集的执行边界，没有调用API Predictor模型，不是完整A/B模型成绩。证据`harness_world_probe_20261022.json`。

## 4. 原95条完整成功示范复验

**95/95全部通过。** 固定原train/validation文件顺序共95条/75任务/29场景家族，先保存plan和来源SHA；每条用新进程从原始train初态重放原Teacher动作，逐条比对公开反馈、保存/恢复终态，再用另一个新进程官方评价。95条初态一致、全部公开反馈一致、快照恢复一致、model_hashes非空，全部独立官方成功，0重放错误/评价运行异常。遇到公开反馈差异即停止该条的规则未触发。

运行：`.cache/harness_revalidation_20261022_a/`；日志`.cache/harness_revalidation_20261022_a.log`。共3087次真实工具调用、3186.094秒（约53.10分钟）串行复验；0模型请求。`harness_revalidation_20261022.json`由全部回执重算，95份原轨迹SHA与封存来源相符，原包6616文件和归档SHA不变；新receipt/独立evaluation/terminal_manifest的SHA分别绑定。它是历史证据复验，不是新增Teacher采样或新的总体成功率。

## 5. 成本、阻塞与训练状态

本轮仅1次DeepSeek只读余额查询，HTTP200、`is_available=false`、无正余额；未发生成请求。配置/凭据/既有授权检查通过只表示参数就绪，不代表账户可调用。证据`harness_provider_preflight_20261022.json`与`harness_configuration_preflight_20261022.json`。已有费用授权继续有效，无需再次申请相同服务授权；需账户资金恢复后才能测真实Teacher纠正效果。

付费模型生成请求0、Teacher生成token0、模型生成费用0、新Teacher合格示范0、Student训练步0。真实工具调用共3137次：95条重放3087、两次播放诊断48、A/B边界探针2；全部为本地环境调用。95条串行复验3186.094秒，其他探针耗时未合并为总运行时。本机GPU为RTX5050/8151MiB，约15GiB系统内存；没有加载Student权重或租用远程算力。

仍未证明：新harness能提高真实Teacher成功率；Student真实4B后端已验证；远程96GB环境可用；任何SFT/GRPO参数更新或效果提升。当前正式数据规模仍是原95条/75任务/29家族，不把脚本重放凑入120条目标。

## 6. 已执行命令与复现入口

```bash
git -C /tmp/recoveragent-diagnosis-20261022 pull --ff-only origin codex/p0-p2-recovery
.venv/bin/python -m venv .venv-harness
.venv-harness/bin/python -m pip install --disable-pip-version-check --cache-dir .cache/pip SQLAlchemy==2.0.52 --no-deps
.venv-harness/bin/python -m pip install --disable-pip-version-check --cache-dir .cache/pip greenlet==3.2.4
.venv-harness/bin/python -m pip check
PYTHONPATH=src .venv-harness/bin/python scripts/probe_appworld_evaluator.py --output reports/harness_uri_probe_20261022.json
PYTHONPATH=src timeout 120 .venv-harness/bin/python scripts/probe_appworld_evaluator.py --source runs/base_sft_v1_20261021_b02/teacher_997b7a07036e/trajectory.json --namespace harness_compat_20261022_a --output reports/harness_playback_probe_20261022.json
PYTHONPATH=src timeout 180 .venv-harness/bin/python scripts/revalidate_teacher_environment.py --source runs/base_sft_v1_20261021_b02/teacher_997b7a07036e/trajectory.json --namespace harness_roundtrip_20261022_a --output .cache/harness_roundtrip_20261022_a
PYTHONPATH=src timeout 5400 .venv-harness/bin/python scripts/revalidate_teacher_environment.py --output .cache/harness_revalidation_20261022_a
PYTHONPATH=src timeout 180 .venv-harness/bin/python scripts/probe_harness.py world --namespace harness_protocol_20261022_a --output reports/harness_world_probe_20261022.json
PYTHONPATH=src .venv/bin/python scripts/audit_harness_release.py --revalidation .cache/harness_revalidation_20261022_a --output reports/harness_revalidation_20261022.json
PYTHONPATH=src .venv/bin/python scripts/audit_harness_historical_requests.py --output reports/harness_historical_detection_20261022.json
HF_HOME="$PWD/.cache/huggingface" HF_HUB_OFFLINE=1 PYTHONPATH=src .venv-inference/bin/python scripts/probe_harness.py mask --synthetic-run .cache/harness_synthetic_mask_20261022_a/run/teacher_synthetic --output reports/harness_qwen_mask_20261022.json
PYTHONPATH=src:. .venv/bin/pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
```

本机隔离环境额外通过`.venv-harness/lib/python3.14/site-packages/base_environment.pth`引用原`.venv`的绝对site-packages路径；新机器应按完整新锁安装，不复制此本机路径。初次沙箱下载因网络不可达失败，授权网络安装后成功；初次pip check发现缺greenlet，补齐后通过。开发测试曾发现改旧teacher.py会破坏v3源码锁，因此恢复原文件，采用新增捕获层读取已有安全HTTP状态文本；未改旧锁放行。最终结果以上述检查日志为准。

原包、原锁、P0–P2 17文件及v3五文件保持不变，v4/v5提示正文不变，见`harness_source_integrity_20261022.json`。更新了共享运行/数据检查模块，旧v5精确运行使用原commit；不得把新代码标成旧冻结实验。所有原始任务/轨迹/终态/官方隐藏评价/凭据均留在私有目录，公开仓库仅提交代码、配置和脱敏证据。

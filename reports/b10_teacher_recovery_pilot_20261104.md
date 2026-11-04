# B10 自然失败与 Teacher 恢复试点 — BLOCKED

2026-11-04；基线 `0b17a8ec3a805586b6deb2a8f602955a38a449f4`，分支 `codex/p0-p2-recovery`。用户本轮授权现有4B M1执行固定B前10项，并从每条有效自然失败最多两个检查点各尝试一次Teacher恢复，总上限20次；不训练、不重跑C244，不租机或续费。

**真实试点未完成。资源检查已实际执行，实验仅完成必要准备。** 现有 P0.1/P0.2 实现、所有旧源码锁和配置均保留，没有重新实现 Schema 兼容层。

## 实际资源检查

- `ssh.txt` 的目标在独立客户端配置下返回 `Connection refused`；12:28:34 UTC / 20:28:34 北京时间复核仍相同。未完成SSH握手，无法确认远端GPU、部署目录或M1服务状态。客户端原有系统配置权限错误通过此次命令的 `-F /dev/null` 处理，仍保留host-key检查；未修改系统文件，未开机、租机或续费。
- 既定官方 Teacher 的只读 `user/balance`、`models` 请求成功，账户返回可用，模型列表包含 `deepseek-flash`。凭据不输出、不提交；**没有发出生成请求**，服务可访问不等于本轮恢复能力已验证。
- 本地已验收M1的51份发布文件SHA、更新/重载回执匹配。身份为 `Qwen/Qwen3-4B`，基座revision `1cfa9a7208912126459214e8b04321603b3df60c`，adapter参数哈希 `3b9cf8f3aed4b36b9d55d0289d3a2f99faef400af80480f7179d09fcf3aa400c`。文件核验耗时1.68秒；没有加载权重或声称远端重载通过。

## 冻结计划与已准备内容

新名单 `configs/b10_recovery_plan_20261104.json` 直接取现有 `b_tasks()` 的前10项，顺序固定：

| 顺序 | Task | 官方来源 split |
| --- | --- | --- |
| 1 | 024c982_2 | test_normal |
| 2 | 042a9fc_2 | test_normal |
| 3 | 07b42fd_1 | train |
| 4 | 07bb666_2 | test_challenge |
| 5 | 099174d_2 | test_challenge |
| 6 | 09ac073_3 | test_challenge |
| 7 | 09b0ee6_1 | test_normal |
| 8 | 0a9d82a_1 | test_normal |
| 9 | 0d01c76_1 | test_normal |
| 10 | 0d22252_1 | test_challenge |

名单SHA：`aa01f3d237a9901210893bd7a4e69d0b7143c3eeecdbc26ac27d844440fa4b00`。独立配置为 `configs/b10_recovery_pilot_20261104.json`；独立准备锁为 `configs/b10_recovery_preparation_20261104_lock.json`，SHA `4a9b4ab9a7c8eb55c9556314705691fc3ccdca7dabdf65dc01ff6429c1aebe9c`。新锁通过未修改的P0.2锁继续校验实际Student执行依赖，没有重签旧锁。

Student沿用P0.2既定M1、non-thinking、execution_v6、nullable v2和40请求/16384生成/4096单回复/32768窗口/600秒预算，逐任务干净初态各一次。Teacher策略/价格估算来源绑定现有官方配置，每分支独立80请求/16384生成/4096单回复/600秒、不限制工具次数、不自动收费重试；继承每尝试171元的保守费用预留上限，估算不是账单。Student前缀消耗另列。名单不替换、不追加，A/B/C划分不改。

准备工具 `scripts/prepare_b10_recovery_pilot.py` 仅生成现有P3-C CLI的10条采集作业，不复制执行框架、不启动任务。其只读检查点选择器固定先post-error再late-failure：前者必须是已观察真实工具错误后的完整post_action边界，后者复用P3-C记录的最后可继续边界；不取pre-error。拒绝非M1、非B10、成功/未知/基础设施结果；检查封存文件和Schema身份；按历史/工具/逻辑状态/runtime去重，保留少取原因。耗尽数值纠正、未决生成或不可继续边界不纳入。

私有作业输出已生成于 `runs/b10_teacher_recovery_20261104/run_001/`，状态为 `PREPARED_NOT_EXECUTED`。资源探针、M1检查和测试日志保存在 `runs/b10_teacher_recovery_20261104_preparation/`；这些私有目录不提交。

## 实际实验结果与成本

| 项目 | 实际值 |
| --- | --- |
| 计划 / 实际Student任务 | 10 / **0** |
| 有效自然失败 / 实际恢复检查点 | **0 / 0** |
| Teacher恢复尝试 / 官方恢复成功 | **0 / 0** |
| 合格恢复后缀 / deferred | **0 / 0** |
| Student / Teacher生成token | **0 / 0** |
| Student / Teacher推理时间 | **0 / 0秒** |
| 模型生成请求 / 生成费用估算 | **0 / 0元** |

以上0表示未运行，不是成功率0%。没有本轮模型失败原因可分析；执行阻塞是远端SSH连接被拒绝。账户查询不算恢复尝试；云机器账单状态未核验。

## 软件验证及尚未完成内容

```bash
PYTHONPATH=src:. CUDA_VISIBLE_DEVICES='' .venv/bin/pytest -q tests/test_b10_recovery_preparation.py tests/test_nullable_recovery.py tests/test_p3c.py
.venv/bin/ruff check src scripts tests
.venv/bin/ruff format --check src scripts tests
```

**64 passed**（其中新增11项准备/选择测试）；Ruff check通过，180个Python文件format check通过。测试使用合成世界、结构化历史与快照，覆盖固定顺序、M1来源、边界去重、真实错误证据判定、非自然失败排除、不可覆盖输出及锁/文件篡改拒绝；不是真实AppWorld或Teacher验收。本轮未重复运行完整pytest。

**尚未实施本轮Teacher从批量Student快照接管的执行接线，以及恢复后缀编码/过滤导出接线。** 现有P3-A入口仍从正常初态采集，不能冒称已经可直接承担此次恢复。资源不可用后，按用户要求停止扩展执行工程；准备工具不是完整试点Runner。后续必须在真实采集可运行时补齐这些直接执行路径，继续使用现有恢复校验、Teacher执行和编码模块；只监督新Teacher Assistant后缀，超长完整保留为deferred。当前没有恢复示范或可供Recovery-SFT训练的新数据。

## 恢复工作时的命令

机器SSH及已验收M1后端恢复可用后，从同一提交工作目录继续。当前私有准备目录已存在，不应重复覆盖；其他checkout可先生成作业，并按实际部署位置使用 `--appworld-root`、`--checkpoint`、`--inference-url`：

```bash
PYTHONPATH=src:. .venv/bin/python scripts/prepare_b10_recovery_pilot.py prepare \
  --output runs/b10_teacher_recovery_20261104/run_001

# 第一项真实Student采集的启动命令；其余9项按student_jobs.json原顺序。
PYTHONPATH=src:. .venv/bin/python -m recoveragent.p3c episode \
  --config runs/b10_teacher_recovery_20261104/run_001/student_config.json \
  --task 024c982_2 \
  --output runs/b10_teacher_recovery_20261104/run_001/student/024c982_2
```

以上不是已执行命令，也不是完成Teacher恢复的命令。不得用原始基座、C244旧失败或脚本模拟替代缺失阶段；不重试已领取的收费恢复请求。完成公开准备提交后停止，等待资源恢复后继续真实试点。

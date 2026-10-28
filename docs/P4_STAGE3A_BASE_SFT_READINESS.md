# Stage 3A — Full732 / A244 全量重新采集 Teacher（替换旧 Stage 3A）

> **最新调度决定（2026-10-28）**：用户要求“并发错误的话就顺序进行”。两并发已停止派发并让在途任务完整收尾；共35项封存，余209项以`full732_a244_teacher_serial_20261028.json`单worker按原队列继续。旧尝试不重跑，错误与未知费用保留；并发是否造成错误尚未建立因果关系。任务/模型/Prompt/生成预算/过滤条件均不变，详见D065。

> **2026-10-28最新用户执行修订**：允许3个独立进程并行继续全部A244；已尝试的5题不重收。每项错误分类留证后继续未执行题，统一后续处理；全局认证/余额故障与连续服务不可用仍熔断。所有成功数据先去除Teacher reasoning字段，再以实际Qwen non-thinking序列长度验收；不能静默截断。每任务请求上限已明确改为80，金额无独立上限；其余16384生成/4096单回复/600秒保持。下文串行和40请求等旧建议以此用户修订为准，历史配置/回执不改。

> **用户最新决定：不检查、不复用、不筛选过去的 95 条 Teacher 数据；新实验从零采集 A244 的每一个任务。** 本文替代 2026-10-28 早先的「历史示范准入与补采集规划」版本。它是 Codex 实施规范，不是 Agent System Prompt。
>
> **本次仅实施 Stage 3A 的正常 Teacher 数据采集；不训练 Base-SFT、不触碰 B244 Recovery-SFT / GRPO、不运行 C244 统一验证。**

## 1. 本轮研究决议和目标

- 冻结的研究任务池：**732 Task / 244 Scenario**，每个 Scenario 3 个任务。既有 `configs/unified_task_split_full_pool_candidate.json` 的 **A244、B244、C244** 分配、seed、manifest SHA 均保持原样。**每个 A 任务都重新从干净初态采集一次；不根据历史成功情况跳过任务，不把 B/C 加入这轮采集。**
- A244 为本轮正常 Teacher → Base-SFT 新示范的 **244 个不同任务候选**；B244 留待新 M1 产生自然失败，再做 Recovery-SFT 和在线 GRPO；C244 是后续三模型共享的唯一端到端验证集。
- 不载入旧 95 条 Teacher 轨迹、不统计旧成功覆盖、不将旧轨迹接入新数据集，不把 Stage 2B 官方参考程序或旧 P3-C 轨迹当作新 Teacher 数据。可只读参考**工程代码、公开工具 schema、兼容环境锁、快照一致性诊断结论**，不可参考旧任务的模型示范进行采集选择。
- **244 是必须覆盖的尝试任务数，不是预设成功样本数。** 如果 Teacher 失败，保存真实失败及原因；不能伪造成功、套用官方参考解或替换任务。
- 这是用户明确选定的**独立自定义研究协议**，不是 AppWorld 官方开发/排行榜协议。原始官方 split 仅作来源与审计字段，不能把自定义训练/验证成绩称作官方 test 成绩。上游官方 train/dev/test 使用建议与原始数据许可证/访问权不是同一回事；必须诚实披露协议偏离和许可/访问依据，不破解受保护数据，也不向公开仓库分发原始任务/测试材料。官方 benchmark 模式的限制保持不变，新的独立研究模式另行版本化。

## 2. Codex 执行步骤（只限这一阶段）

### 3A-1：冻结 244 项采集清单（只读元数据）

1. 更新代码，阅读 `AGENTS.md`、`PROJECT_PLAN.md`、`docs/P4_STAGE_GATES.md`、本文件及已有 Teacher/Harness、evaluation 和 snapshot 实现。
2. 直接从固定 Full732 manifest **提取 role=base_sft 的全部 244 个 task_id**；断言 244 个不同 task、244 个不同 scenario、B/C 均不在内；保留 source official_split 与 manifest SHA。按固定 task_id/seed 排序，预先锁定尝试次序。不再调查历史 Teacher 轨迹。
3. 在新独立目录存放冻结 campaign 配置（如 `configs/full732_a244_teacher_fresh_v1.json`）和结果；旧 90 任务操作协议、既有数据/权重/报告/锁保持原字节。

### 3A-2：单独的研究协议与环境预检

1. 使用现有合法安装和可访问任务文件，核验数据版本、manifest、公开工具、环境独立性和实际任务可初始化性。环境必须使用已验证兼容的 **SQLAlchemy 2.0.52 / SQLModel 0.0.48**，启动时检验 DB URI roundtrip；不再使用旧 SQLAlchemy 2.1.5 采集新数据。
2. 新增最小的 **custom_full732_research** 采集准入路径，独立于原官方 benchmark 模式。严格依据已取得的实际数据访问和使用权限决定可执行性；**不要**简单把原 `permission_status=BLOCKED` 改为 READY、移除原有安全检查或编造许可证例外。若某项权利/数据来源确实不允许使用，单独标记并停止该项；不得将计划自动缩到 A30 又宣称完成 A244。
3. Agent 只能看到原任务公开指令和当轮公开工具；**不能看到任何测试评价程序、隐藏答案、参考解法、controller 证据或后验评分**。评价永远在控制侧执行。
4. Teacher 使用用户固定 `execution_v6` 完整 System Prompt + `dynamic_discovery`，不改原文字节、不拼接旧 Harness 规则。工具可见性、Schema 验证、同批依赖、AnswerGuard 和请求/Token预算按已有经验证的实际控制器执行。不能为了跑通任务悄悄改 Prompt。
5. 对整个 campaign 执行准备检查后，先在**A244 之内预定的一个允许任务**做真实端到端 smoke（计入本轮覆盖，不另采额外任务）；确认请求、控制侧官方评分、终态存储与回执无异常，再按照冻结清单继续全量任务。系统配置问题先修复后重试同一任务，不能按成功率换题。

### 3A-3：覆盖全部 A244 的新采集

1. 进行第一轮 **244 个任务各一次**正常 Teacher 独立采集：每个任务从原始干净 DB 开始；每次都有自己的 task/attempt/seed、独立目录、真实请求与工具结果，不能沿用其他任务历史状态。
2. 按固定小批次调度（建议每批 10–20 个不同任务）。从第1题到第244题，批次可以续跑，但**不能因早期任务成功/失败决定是否跳过未覆盖任务**。
3. 每次执行保存 model identity、Prompt SHA、实际工具 Schema、真实 API 调用、token usage、控制器反馈、终态 DB/snapshot、原始官方 tracker、异常与不确定费用。任务级业务失败继续下个任务；不可恢复基础设施/付费账户故障立即停止且记清未执行清单；未知收费请求不得自动重试。
4. 完成第一轮后，可以按**事先冻结、显式预算允许**的规则，对自然失败任务进行第二次 Teacher 尝试（最多额外一次），但要分开报告首轮与复试成功率及结果条件化选择。若尚无追加费用授权，停止在第一轮，不把未重试任务当作失败总数之外的成功。
5. 不修改官方 evaluator，也不能以 `supervisor.complete_task` 或 Teacher 自称完成替代可信官方评价。不能使用隐藏参考解作为新的 Teacher 示范。

### 3A-4：每条示范的资格验收

只有同时满足以下条件的**本轮新采集数据**可进入未来 Base-SFT：
- 原任务 A244 身份且数据使用/采集许可有效，用户原任务成功目标由原始严格评分真实通过；
- 实际完整批次，Assistant/Tool ID 对齐，已知工具参数在调用前可见，未观察到部分执行/未知副作用；
- Teacher 轨迹模型身份、原任务、Prompt、Schema、budget、哈希、费用和原始官方 tracker 完整；
- 使用同一原任务的新进程重新载入新保存终态，核验数据库内容、必要 **model_hashes / SQL 变更追踪元数据** 与控制侧官方评分可独立复现。Stage 2B 证明旧环境中可能丢失元数据，不是允许旧快照免检的理由；**空 model_hashes 本身不代表错误**，需结合真实写入和回放/评分证据判断；
- 后续打包仅监督新的真实 Teacher Assistant token，User/System/Tool/拒绝提示/历史其他轨迹和 padding 全屏蔽。这里只验证数据与 mask 可构造，**不训练权重**。

若本轮新 Teacher 成功但保存状态无法等价重评分，标记 `REPLAY_UNVERIFIED`，不能直接当成可靠正向训练样本；保留原始成功与重评结果，不删除证据。

## 3. 预算、权限及停止条件

用户已明确**选定全 A244 重新采集**；这不等于授权无限付费、开新 GPU 实例或解除第三方数据访问约束。

- 复用已有 Teacher 模型和定价配置，只从真实配置获取单价，绝不编造。**必须预先有可审计的本轮总费用上限、每任务/每请求上限和付费凭据**；没有明确额度时，生成 244 项待执行清单和所需费用字段，暂停在付费 dispatch 前，不回到“历史数据复用审计”。
- 保持现有单次生成上限4096、完整任务最多40请求/16384输出Token/600秒等已有冻结约束；若现有 Teacher 配置限额不同，列出差异并按可证明的预设上限运行，不暗改历史配置。
- 任何 401/402/403、超总预算、未知请求计费、环境/评价严重异常、任务数据使用依据未确定，都触发明确阻断，不自动跳过后假称完成244成功任务。
- checkpoint/resume 必须按 campaign + task + attempt 哈希去重；不能重复付费运行已完成的attempt或覆盖原始回执；失败和未执行任务全部保留在244总分母。
- 不启动新M1训练、任何Student推理、Recovery-SFT、GRPO、C244评估，也不修改 `execution_v6`、旧权重或上游 AppWorld。

## 4. 交付

- 新 campaign manifest/config、精简必要代码/测试、`reports/full732_a244_teacher_fresh_report.md` 和脱敏统计 JSON；私有真实轨迹及终态只保存在项目非公开数据目录，禁止提交正确答案/原始数据/API密钥到GitHub。
- 每个 A244 任务一行：`task_id,scenario_id,official_split,attempt_count,status,official_success,replay_verified,eligible_for_sft,input_tokens,output_tokens,cost_or_unknown,source_hash`。统计 `244 planned / attempted / succeeded / eligible / failed / unknown / blocked / not_run`，禁止结果有偏的分母替换；按原始来源与场景汇总。
- 优先复用现有 P3-A Teacher runner、正式数据质量/掩码检查和当前独立评分适配；不要另写一套完整 Runner、训练器或模型服务。
- 运行针对性 pytest、Ruff，记录所有真实成本和失败；提交本轮代码、脱敏报告到开发分支，**结束即停止**。不自动进入 Stage 3B（新 Base-SFT 训练）。

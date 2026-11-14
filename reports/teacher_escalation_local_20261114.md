# Teacher升级补采迁到本地，以及从S0重训接线

2026-11-14 13:23。本地32次登记尝试已结束：5条合格、7条有效失败、20条异常；不能写成32题均有效完成。新的S1尚未训练。远端全部相关作业已停止，产物回传并核验，用户可在平台控制台关机。实际停止计费由平台状态决定。下文先保留启动时记录，最终统计见文末。

## 本轮输入和已完成事实

原S0/S1 dev24为9/24与6/24，0基础设施异常；原S1有真实44步更新，但没有观察到成功率提高。旧模型和成绩不变。

旧63次检查点恢复已有18条合格后缀。随后冻结45个未合格train任务，从原始干净初态补采。远端停止时29题已结束、45次模型尝试（含最初7次）：

| 模型 | 尝试 | 官方成功 | 合格完整示范 | 异常/未评分 |
| --- | ---: | ---: | ---: | ---: |
| DeepSeek V4.1 Flash | 29 | 13 | 13 | 1 |
| GPT-6 Luna | 16 | 2 | 0 | 1 |

DeepSeek一条在评分阶段异常，GPT一条网络异常；原分类与评分证据保留。两条GPT官方成功被严格导出检查过滤：非thinking Responses回合没有调用`remember_response`，后续请求无法重放原始output，导出报`Actual Responses Teacher context mismatch`。新执行版本修复该接线，并增加真正经过P3-A循环的两回合合成回归；旧两条仍不算合格，不用放宽验证或改写历史来补数。

旧补采累计1,525请求、27,819,919输入、420,449生成、21,751,395缓存token；配置费率估价USD2.364364328，另有未知请求预留USD0.2127872。累计episode10,947.96秒是并发进程时间，不是墙钟，也不是订阅实扣账单。

## 本地继续的真实执行

使用同一网址与密钥的`deepseek-v4-pro`。实际两回合工具探针通过，852输入/88生成token；该探针不是任务成功。用户指定的`gpt-5.6-sol`实际请求返回HTTP400、`Model is unavailable.`，暂缓GPT升级，不替换成Luna。未改订阅、充值或启用额外计费。

32题由固定45题扣除13条已合格成功得到，包含16个已执行但未合格任务与16个尚未执行任务，顺序不变。每题一次Pro，从原始初态独立执行，最多32次；保持65,536上下文、80请求、16,384累计生成、4,096单回复、1,200秒。两CPU worker，原任务官方验收；新成功仅算完整Teacher示范，不冒充恢复后缀。超长保留deferred，不截断。

本地私有目录`runs/teacher_local_20261114_01/`：

- `source/`：冻结165个执行依赖文件，配置/锁`teacher_escalation_local_20261114{,_lock}.json`，锁SHA `a38ee91f67f207a1ba72ce07fe0c7961315bf864ed7bbb8e0d3cf630a50ed645`。此前仅准备、尚未执行的远端升级版本也保留，不重签历史锁。
- `worker_env/`：独立Python3.12 CPU环境；固定AppWorld revision与官方bundle，SQLModel0.0.24/SQLAlchemy2.0.52/Pydantic2.12.3/freezegun1.5.1。无需GPU权重或本地模型推理。
- `runs/pro_001/`：新采集名单、配置、claim、实际请求/回复、轨迹、评分与编码；`remote_followup_01/02`只读保留旧源文件，通过显式路径映射访问，不改原SHA。
- `launch.py`与systemd用户服务`recoveragent-teacher-pro-local-20261114`：12:46启动，控制器异常退出后接续；已有claim不会重新调用同一模型/任务slot。机器/WSL停止时进程也会停止，不承诺断电期间运行；接续仍读取原输出。

首题真实AppWorld初态哈希与远端原始轨迹一致；公开工具catalogue转换成功。3条旧成功示范按固定Qwen3.5-9B模板重新编码，共54个Assistant样本，与原编码逐项相同。上述迁移检查0模型请求；采集启动后实际API已返回，但当前没有32题完成结论。

12:48首题`eb5ad85_1`真实通过原任务官方验收并编码合格：9个Assistant样本、623监督token、最长完整序列20,709。sample SHA `2d2d3d9cb602feda328b2ce9dbde234afa871fe97c010e4bde799879aa85de1c`，examples SHA `bd9d1e76a6889a862568249a4ce97a9027aa4632c5c39d67197a9b3fc6d3324b`；独立SHA与Assistant-only mask检查通过。9请求、78,997输入/4,556 provider生成token、110.56秒，峰值费率估价USD0.083772392，非账单。当前1/32完成、1合格，另外任务继续；这是本地真实任务证据，不能推断整体成功率或训练收益。

## 训练入口及尚未执行内容

复用`teacher_followup`执行/导出、`v2_data`编码和`v2_training.train_sft`。新入口`scripts/run_teacher_escalation.py`提供`prepare`、`collect`、`prepare-training`、`train`。本地只执行采集，不自动派发GPU训练。

后续使用原18条恢复后缀、全部新合格完整示范以及原Base-SFT范围中24个train场景的完整正常示范。24场景按固定任务rank选择，与dev结果无关；不再让3条长示范占据全部正常回放。按完整示范保留全部token，恢复/正常两类目标采用监督token加权各50%；只监督Teacher对应Assistant范围。该混合是下一次实验选择，尚无效果收益证据。

训练严格核对已验收S0身份、658步回执和adapter哈希；短2步、最长1步和正式1epoch分别从同一S0加载可训练adapter，不从旧S1或烟测权重开始。出现OOM、非有限梯度、参数未变或重载失败即停止。新checkpoint为独立`S1_teacher_retrained`。迁到另一GPU时显式保存`training_deployment.json`与私有路径映射，原采集deployment和来源预期值不改写。

本地接续命令（原run已准备，不重做prepare）：

```bash
systemctl --user start recoveragent-teacher-pro-local-20261114.service
# 服务退出后也可直接接续；不要与服务同时启动第二个控制器。
runs/teacher_local_20261114_01/worker_env/bin/python runs/teacher_local_20261114_01/launch.py
```

GPU可用并搬运相同冻结源码/私有数据后，在冻结源码目录设置`PYTHONPATH=src:.`及原HF缓存，由对应Python运行：

```bash
python scripts/run_teacher_escalation.py prepare-training --output /private/pro_001 --deployment /private/training_deployment.json
python scripts/run_teacher_escalation.py train --output /private/pro_001 --deployment /private/training_deployment.json
```

训练deployment需包含原S1数据目录`original_training_output`、旧补采目录`previous_output`、已验收权重`s0`、`gpu_python`、`deadline_epoch`与来源路径映射。当前缺的是合格新数据的最终完整清单及可用GPU训练时段；新的参数更新、重载和dev结果均未发生。

## 验证和备份

- 相关测试：`.venv/bin/python -m pytest -q tests/test_teacher_escalation.py tests/test_teacher_followup.py tests/test_teacher_collection.py tests/test_v2_learning.py tests/test_v2_s1.py`：80 passed。
- 完整软件测试：`.venv/bin/python -m pytest -q`：1069 passed、1 skipped；3条上游Pydantic弃用警告。`.venv/bin/ruff check .`、`.venv/bin/ruff format --check .`：通过，224文件。
- 本地CPU环境`pip check`与无时区日期存储通过；真实AppWorld初态/公开Schema、3条真实示范重新分词通过。这些是迁移验证，不能写成Pro任务成功或模型效果提升。
- 原S1完整归档SHA `4b6de950134f0b35fb9e71d1cd0acf872ac00c1fe346f5fe4a8b95305ec051cd`及51,814文件通过；旧补采744文件、新补采11,105文件通过。最新补采归档51,139,370字节，SHA `70d28d203f45f3528d327b9220a280b957e976202aa0ed54e357b56f3172f790`。S0/S1独立权重备份也已验收。

本轮只提交代码、配置、测试和汇总；密钥、任务数据、权重、请求/回复及快照留在私有目录。原23:47:24截止保持，不自动租机或续费。新的S1与任何准确率改善仍待真实训练和同条件评估。

## 本地批次最终检查（13:28，UTC+8）

采集在12:46:54–13:23:26运行36.53分钟后正常退出。32次登记尝试各一次：5官方成功且合格，7有效失败（6声明完成但官方失败、1上下文耗尽），20异常，0超长暂缓。完成标记只表示全部尝试已关闭，不表示全部获得有效成绩。

14条异常是服务端HTTP429；现有诊断没有保留完整错误正文，不能确定是短时速率还是订阅额度限制。最早两条在执行中遇429，之后12题首请求即失败。controller未在服务连续拒绝时暂停领取新题，这是当前调度缺口；进程守护不等于服务级退避。没有把这些错误归因于Teacher推理能力，也未自动重复付费slot。

另6条异常均用原始请求/回复在CPU离线复现`Provider exceeded configured billing bound`。它们接近16,384累计生成上限，请求减少max_tokens，但provider返回的completion_tokens超过该值，严格校验因此中断；例如请求1,213、报告2,358，请求127、报告128。原始推理字段和用量保留，不能静默扩大预算或执行这些回复。当前未改变该校验，也没有新模型请求、重评分或重跑。

5条成功为`eb5ad85_1`、`166f4ff_1`、`21abae1_2`、`21abae1_3`、`83a7951_2`；共63个Assistant样本、5,554监督token，最长完整序列28,621 token。导出Manifest SHA `3ada228b1facd3db953b61339f23e778f79aa82b65c046c4d0c1d7c4531ba63f`。独立检查导出文件SHA、每条sample/examples SHA及Assistant-only掩码全部通过，回执为私有`completion_validation_20261114.json`，0新增模型请求。

与旧13条完整示范合并，现有18条新增完整示范、482个Assistant样本、52,140监督token；另有原18条恢复后缀、169样本、24,688监督token。两类合计36条，来源任务不重叠；尚未完成与24条正常回放的混合训练包，未发生新参数更新或效果验证。

本地批次652请求，已验证用量12,231,723输入、179,821生成、9,325,312缓存token，累计episode4,333.48秒（不是墙钟）。已验证部分按配置峰值费率估价USD4.958867408；异常请求另留USD27.92927676保守预留，**不是实际扣费**，不能将两者相加当账单。运行峰值内存2.8GB；没有远端GPU作业。后续优先修正429暂停/退避和输出预算契约，保留此次证据；GPT-Sol可用性仍未恢复确认。

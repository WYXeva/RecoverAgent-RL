# 追加式工具定义修复与短 GPU 验证（2026-11-18）

## 范围与版本

新协议 `student_append_tokens_v2`，新配置/源码锁 `v2_s2_append_docs_20261118`，实现提交 `ca203be`。继承 `f79c85f` 的真实 token 追加与合并 GRPO；原 v1 配置、源码锁和原始轨迹保留，不重签。正式 GRPO 仍暂停，原15步 actor/optimizer、未消费47/49组不动。Student/GPU阶段没有Teacher、dev/test或正式参数更新；随后按用户指示停止Student，增加下文的本地Teacher对照。

## 实际修改

- `student_tokens.py`：每次成功 `show_api_doc` 都按本次查询的应用/API名称，从 Adapter 现有可见定义中取完整兼容 Schema；重复查询不再仅返回 `parameter_schema_ref`。返回 `tool_definitions`，原样保留类型、required、nullable、default、约束；观察保留返回信息和额外公开说明，去掉重复参数正文与悬空引用。失败查询保持真实错误。
- `student.py`：初次构造时加入新版本固定 system 规则，替换公开输入中“去当前工具列表找参数”的旧 compact notice；后续不修改这个前缀。继续使用原解析、AppWorld公开目录和发现/执行权限，不新增 callable 注册系统。首次发现后须下一回复才能调用，同批原有严格检查保留。
- `v2_runtime.py`、`grpo_compare.py`、`probe_s2_append.py`：新版本进入正式运行绑定、训练读取和已有短验证入口；v1和v2都可读取，但同一轨迹不能切换身份。历史无新锁代码仍拒绝直接接续。
- Teacher的原生工具序列化、生成/Solver/Judge、模型采样参数、奖励与GRPO数学目标未改；固定原生9B chat template、原 `execution_v6` 常量也未覆盖。新 system 是基础规则加下列版本化固定说明。

新 system 的完整增补：

```text
TOOL DEFINITIONS AND AVAILABILITY
- The tools list at the beginning contains only startup tools and stays fixed.
- Every successful api_docs__show_api_doc result includes a complete function
  definition in tool_definitions, including on repeated queries. That function
  becomes callable from your NEXT reply, even though it is absent from the
  startup tools list. Use its exact function.name and the same Function Calling
  format shown above; do not wrap it in a different call format.
- For startup tools, read arguments from the initial definition. For APIs learned
  through show_api_doc, read arguments from tool_definitions in that query result:
  follow the required fields, types, nullable alternatives, defaults and constraints.
  The observation retains return information and supplementary public notes.
- Querying documentation retrieves a definition; it does not execute that API.
  Once its definition and required arguments are available, call the API itself
  to perform the needed operation. Re-query documentation only when needed.
- APIs without a previously obtained definition remain unavailable. Do not call
  an API in the same batch that first discovers it.
```

公开任务里的查找提示同步改为：搜索只返回名称/描述；通过 `show_api_doc` 返回的 `tool_definitions` 获取可执行参数，文档查询本身不执行业务。Teacher的历史 compact notice不被全局改写。原始消息和旧轨迹不改。

## 本地验证

合计 **254项相关测试通过**，分三批运行：

```bash
PYTHONPATH=src:. .venv-v2/bin/python -m pytest -q \
 tests/test_student_append_tokens.py tests/test_append_probe.py
# 25 passed，1.96s
PYTHONPATH=src:. .venv-v2/bin/python -m pytest -q \
 tests/test_grpo_efficiency.py tests/test_s2_segment.py \
 tests/test_tool_discovery_compact.py tests/test_student_batch.py \
 tests/test_schema_compatibility.py tests/test_completion_submission_v2.py
# 115 passed，9.95s
PYTHONPATH=src:. .venv-v2/bin/python -m pytest -q \
 tests/test_s2_training_cpu.py tests/test_v2_training_cpu.py \
 tests/test_student_backend.py tests/test_student_v6.py \
 tests/test_harness.py tests/test_harness_refactor.py tests/test_harness_review.py \
 tests/test_s2_experiment.py
# 114 passed，14.79s
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
# 通过，305文件格式检查
```

另以备份真实Qwen3.5-9B tokenizer运行 `tests/test_student_append_tokens.py`：**23 passed / 15.72s**。这里模型/网络为模拟，原生模板和输入构造是真实实现；不能写成真实模型效果。

覆盖：新旧版本多轮发现、重复完整文档、Schema精确相等、合法nullable参数、未发现及同批调用拒绝、错误反馈、多工具返回、固定header、P[t+1]逐token保留P[t]+Y[t]、vLLM/Transformers回执、新旧编码不可混接、全部目标与logprob一次覆盖、旧数据/原始system不被改写。CPU合并loss/梯度和真实小模型更新回归也通过。

## 先前 v1 GPU 实测：计算路径有效，工具调用表现有问题

私有目录 `runs/s2_append_gpu_20261118_01/`，远端同名 `recoveragent_append_probe_20261118_01`；源码 `f79c85f`。L40S 48GB，已验收15步actor SHA `114dbcb4e28a763674fcbda8d1e80055eb918edf6b90cd60f3642fbb503484ff`，optimizer SHA `47698eea5b6b79e6e2aa070fbb11f103f1c40b835746cae4ffa3fa7eca09b206`。

固定train100首题 `32616b5_2`，四种子42/142/242/342；每题80请求/16384生成/4096单回复/1200秒/64K。官方结果 **0/4**，均有效自然失败：反复查询API文档，实际业务API调用均为0；0协议拒绝。不能把“工具出现在可见表里”说成“模型已成功调用”。这是本次提示/文档修复的直接证据。

| 种子 | 原回复数→合并段 | 原累计序列token | 合并累计序列token | Assistant目标token |
|---|---:|---:|---:|---:|
|42|80→1|1,101,411|22,006|3,655|
|142|80→1|1,069,948|18,856|3,687|
|242|80→1|1,012,606|18,050|3,641|
|342|80→1|1,208,585|25,537|3,829|
|合计|320→4|4,392,550|84,449|14,812|

316/316相邻前缀检查通过，初始11工具不变，目标与logprob完整。累计处理序列token减少98.08%，这不是实测加速倍数。真实合并前向/反向 **76.24秒**，含加载/校验进程 **125.42秒**；峰值allocated **27.93 GiB**、reserved **33.03 GiB**，无OOM。4段是主体计算次数，不是4次optimizer更新；这组全0奖励，优势/损失/梯度均0，按诊断方式完成计算，**0次更新**，不可据此宣称GPU非零梯度数值等价或学习收益。

原逐回复参考测速在启动后511.66秒按用户指示中止，因此没有完整参考耗时、GPU梯度对照或可信实测加速比。没有为了对照重新跑长批次。

独立安装 `causal-conv1d==1.7.0` overlay，保留原Torch2.10.0+cu128、Transformers5.23.0.dev0、FLA0.6.0；实际causal-conv与gated-delta快速分派及BF16 CUDA有限非零梯度烟测通过。overlay仅进入训练比较，不注入vLLM环境。实际全目标概率比均值1.000079，同actor行为logprob抽查通过。

四题采样650.09秒，320次任务模型请求，4,377,738输入/14,812生成token；另有5次后端启动探针，131,094输入/10生成token。Teacher费用0；未取得平台实际GPU账单。私有1975文件已下载并逐SHA验证，归档SHA `26728e375902b03c5e4a849122fa5899d176ab9ab6cb03424e61c0affc5ebd8f`。

## 新 v2 真实调用验证

使用独立 `recoveragent_append_docs_probe_20261118_01`，同一15步actor、同一原始题及相同预算。**用户随后明确停止Student并改为一次Teacher验证，四条计划没有全部执行。**

| 种子 | 状态 | 已返回请求 | 实际业务API调用 | 结束原因 |
|---|---|---:|---:|---|
|42|官方失败|11|0|查文档后错误声称登录失败；没有实际调用login|
|142|官方失败|80|0|反复查询文档，预算耗尽|
|242|用户中止，奖励未知|68（另1个请求无响应回执）|0|用户停止；不能计为普通失败|
|342|未启动|0|—|用户停止|

已完成两条的79次成功文档查询逐一核对：全部含精确完整Schema，其中56次为重复查询；89/89相邻token前缀通过，header始终11工具，0协议/参数拒绝。两条共91回复→2合并片段，累计序列1,308,355→31,827，4,384目标token；本次没有训练测速或更新。不是完整四轨迹组，不计算组优势或回流失败反馈。

暂停前采样阶段383.40秒（含启动）；三条已返回159个响应，7,435生成token，另有一个未返回请求保留未知用量。前两条墙钟41.66/152.19秒。GPU服务已退出，显存0 MiB，原15步actor和optimizer哈希再次核验不变。

995个私有文件已备份到本地并逐SHA验证；归档SHA `c1bb067acc271e4ec10e1eaf6b8d84ab8c9ede308c7d4ed9cfc0da2a9543cfea`。派生审计单独纠正了目录扫描误将config/log文件列为未完成episode的问题，原始回执不改。

**结论：代码层的完整定义、固定system和执行权限已接通；本次真实Student仍未证明能调用新API，不能写成工具使用问题已经解决。**

## 启动命令

Student已按用户要求停止。下列是实现提交 `ca203be` 的独立checkout复现命令；后续Teacher探针另有新源码锁，不把历史锁重签。复用现有私有部署配置（模型、AppWorld、各Python环境路径），设置现有机器上可用的 `TRAIN_PY`、`DEPLOYMENT`、`ACTOR15`、`OPTIMIZER15`，从新源码目录启动：

```bash
export PYTHONPATH="$PWD/src:$PWD"
export HF_HUB_OFFLINE=1
# HF_HOME使用现有已缓存9B的目录，不重新下载权重。
"$TRAIN_PY" scripts/probe_s2_append.py --execution v2_s2_append_docs_20261118 \
 prepare --output "$NEW_RUN/probe" --actor "$ACTOR15" --optimizer "$OPTIMIZER15"
"$TRAIN_PY" scripts/probe_s2_append.py --execution v2_s2_append_docs_20261118 \
 sample --output "$NEW_RUN/probe" --deployment "$DEPLOYMENT" \
 --deadline-epoch "$AUTHORIZED_DEADLINE_EPOCH"
```

`NEW_RUN`必须为新的私有目录；截止时间须明确且距启动最多2小时。入口不租机/续费，不自动开始长训练；本次实测只运行上述prepare/sample。后续是否恢复训练另按用户安排。

## 用户追加的一次 Teacher 对照

实现提交 `38c4088`，新配置/锁 `v2_s2_append_teacher_probe_20261118`。`scripts/probe_append_teacher.py` 只调用原 `p3a.run_teacher_episode`；新增可选参数默认关闭，原Teacher生成/Solver/恢复路径不变。开启时复用同一完整定义函数及固定system规则，provider请求只传初始工具，执行校验仍采用原已发现工具。Teacher保留服务商原生Function Calling与reasoning历史，不伪称采用Qwen真实token追加编码，也不生成GRPO logprob。

增补Teacher固定头/重复查询/动态调用的模拟集成测试，并迁移当前测试引用至新锁。全部相关测试重新运行：**300 passed / 24.67s**（此前16文件及 `test_teacher_data.py`、`test_s2_teacher_context.py`）。

前两次本地启动在环境检查阶段退出（0模型请求、0工具调用、0费用）；随后复用 `runs/teacher_local_20261114_01/worker_env/bin/python` 的已验收Python3.12/SQLModel0.0.24环境，未改写第三方AppWorld或训练环境。第一条真实Teacher尝试为私有 `runs/s2_append_teacher_20261118/live_003`，后来按用户指示中止并换为一题有历史成功回执的任务。

以下原生Teacher命令仅用于 `38c4088` 独立checkout复现。当前改用下文文本Teacher入口。

```bash
PYTHONPATH=src:. \
RECOVERAGENT_TEACHER_TOKENIZER_DIR="$PWD/runs/s2_context_validation_20261117/teacher_tokenizer" \
timeout --signal=TERM 1300 \
 runs/teacher_local_20261114_01/worker_env/bin/python \
 scripts/probe_append_teacher.py --appworld-root data/appworld \
 --output "$NEW_PRIVATE_TEACHER_RUN"
```

固定原题、原初态、官方评分；80请求/16,384累计生成/4,096单次/1,200秒、$2价格估算上限（非实际账单）。Teacher独立1M窗口；实际服务仍为OpenCode Go `deepseek-v4.1-flash`，thinking enabled；配置temperature0.2在thinking请求中按原实现省略，沿用现有Solver设置，没有使用Judge high/8192配置。正式GRPO和GPU采样继续暂停。


用户后来要求换一道已成功的任务。第一题Teacher已停止，54个请求均有原始响应，53个执行调用，0业务API调用；39次成功文档返回均完整，其中30次重复。909,782输入/16,384输出token（包括服务返回的思考用量），867,456缓存输入，按公布价格估算$0.037563336，非实际账单。未完成官方终评，按用户中止/未知记录，不能记作正常任务失败。没有重启该题。

第二题固定为训练范围 `a3ba388_3`：给已有Gmail辞职草稿附上公开指定文件，并定时发送给经理。历史Go Flash原始Teacher记录 `runs/teacher_go_64k_20261111_04/attempts/teacher_go_64k_20261111_04_a3ba388_3` 的官方原任务验收9/9通过，25请求/25工具调用。历史结果仅用于控制侧选题和初态核对，未提供给本次Actor。独立私有目录为 `runs/s2_append_teacher_20261118/known_success_001`，同一个现有 `p3a.run_teacher_episode`，本地Python3.12；不改变训练池或原train100清单。


第二题原生Teacher最终官方失败：60请求、59工具、0业务API，16,384累计生成token耗尽；921,195输入token、431.41秒，项目估价$0.036132708。48次成功文档查询均完整，33次为重复。原生请求的tools仍只有初始工具，因此这**不是与Student文本XML匹配的验证**；不能由此判定追加式文本方案不可行，也不能断言服务商一定进行了硬约束解码。

历史成功的数据库逻辑初态哈希与本次初态不相等；只能确认同一任务及公开要求，不能把历史成功作为严格相同初态的配对对照。当前两种Teacher接口之间另行核对初态。

## Teacher 改用 Student 文本 XML（用户追加要求）

`fa0d343` 增加 `teacher_text_tools.py` 与 `p3a` 可选接线：从固定Qwen3.5-9B模板渲染相同初始system/工具头，服务请求不传 `tools`、`tool_choice` 或 `parallel_tool_calls`，只从 `message.content` 解析文本，复用 `v2_model.parse_native`、严格whole-batch Harness及AppWorld执行。reasoning另存，不转换为动作。已有Teacher生成、Solver、Judge默认路径不受影响。

原生模板SHA：`a4aee8afcf2e0711942cf848899be66016f8d14a889ff9ede07bca099c28f715`。完整渲染后的system与请求逐份保存在私有证据。Teacher仍由服务商渲染聊天消息，没有Qwen采样token IDs或behavior logprob，不能用于新协议GRPO；验证的是同一公开规则、XML动作格式及执行链，不是相同底层token序列或相同模型。

首轮 `text_known_001`：16请求全部解析拒绝，0工具执行，官方失败；56,316输入/4,001输出（含1,963 reasoning），126.57秒，项目估价$0.006718464。返回主要为DSML标签；纠错提示仍残留“JSON/unique call IDs”，且下一次请求没带回被拒绝文本。该结果未验证发现后调用能力，不能写成业务能力失败。原回执、原奖励保留，诊断轨迹不进入学习。

`8011092` 纠正上述验证入口，独立 `v2_s2_append_teacher_text_v2_20261118` / `teacher_student_xml_probe_v2`，不重签已运行锁：

- 初始system和XML模板不变；纠错请求准确说明 `<tool_call><function=...><parameter=...>`，不要求模型生成JSON调用外壳或call IDs。
- 被拒绝的原始Assistant文本和reasoning重新出现在下一次Teacher请求中，不伪造成已执行工具消息；执行审计仍保留原Harness拒绝回执。
- 不将DSML自动转成XML，不补函数名/参数，不从历史成功解注入答案。合法XML仍交同一Student解析器。
- 修正只在显式Teacher文本探针启用；当前Student/GPU暂停，未重启其运行，也未改变生产Teacher/Solver/Judge设置。

97项相关CPU测试通过（7.39秒）：`test_teacher_data.py`、`test_s2_teacher_context.py`、`test_student_append_tokens.py`、`test_tool_discovery_compact.py`、`test_s2_segment.py`、`test_grpo_efficiency.py`。包含网络/世界模拟下的拒绝→明确XML反馈→发现→重复完整文档→新增API调用→完成链，实际payload无原生tools、system不变、拒绝原文保留。Ruff check及307文件format通过；模拟不等于真实Teacher成功。

第二次启动 `text_known_002` 在AppWorld实验目录重名检查退出，0模型请求、0费用，保留错误记录。随后用唯一episode名 `teacher_text_known_003_a3ba388_3` 调用相同生产函数，配置/源码锁仍为8011092；私有薄包装及SHA随plan保存，未覆盖旧环境产物。


### 修正后真实结果

`text_known_003` 使用8011092，**官方原任务验收成功，9/9检查通过**。31请求、18个真实工具调用，其中7个业务调用：Gmail/文件系统login、读取草稿/目录、上传草稿附件、更新定时发送、读回草稿；最终合法complete_task无answer。不是Judge重判，也没有把历史成功答案提供给Actor。

7次show_api_doc均返回完整定义，新API不在固定初始header中，随后由Teacher以XML调用、经Student解析器和Harness实际执行。31份请求system完全一致，SHA `64469192341da670193137c2effb4d6e022b54584ebc1ec9f162483d415e2165`；均无服务商原生tools字段。此次无重复查询；重复完整定义已由先前实测与CPU覆盖。

| 当前接口对照（同一任务、相同本地初态） | 结果 | 请求 | 业务API调用 | 拒绝 | 墙钟 |
|---|---|---:|---:|---:|---:|
|固定原生tools＋追加文档|官方失败|60|0|1|431.41秒|
|文本XML，旧纠错/丢失拒绝历史|官方失败|16|0|16|126.57秒|
|文本XML，纠错/拒绝历史修正|官方成功，9/9|31|7|14|181.78秒|

三条当前尝试初态hash均为 `0826b2645896232baf7d180e64a11e4244b8eda90df4356085db966a2bde7b7c`。历史11月11日成功仅作为选题依据，其初态hash不同，未混入配对比较。

仍有14次拒绝：2次DSML被视为无合法动作，10次XML结构错误，1次参数错误，1次未发现工具。成功前格式适应成本明显，不能据一次成功宣称DeepSeek稳定遵循QwenXML，更不能推论9B调用问题已解决。未修复/翻译模型动作来放行，拒绝批次没有合法前缀被执行。

成功尝试270,972输入/4,458输出token（含1,816 reasoning），254,848缓存输入；30/31响应含reasoning。项目价格估算$0.011715888；接口list_cost合计$0.00585795、usage.cost合计0（Go订阅字段，不表示实际免费）。完整实时请求/响应、结构化轨迹、终态、官方评价和逐文件SHA保存在私有 `runs/s2_append_teacher_20261118/text_known_003/`。两次文本模型尝试合计327,288输入/8,459输出、308.35秒；其中重名启动0调用另记，未隐藏失败尝试。

### 当前入口与停止状态

最终入口使用独立 `v2_s2_append_teacher_text_final_20261118` 锁，要求显式 `--text-template`，并由输出目录派生唯一AppWorld实验名，修复固定 `teacher_episode` 导致的重名启动问题。该收尾只改启动命名与锁选择，Teacher的prompt、纠错、解析和采样设置与8011092成功运行相同；没有追加一次真实任务。历史原生入口在38c4088独立checkout复现，不在当前入口自动切回原生tools。

```bash
PYTHONPATH=src:. \
RECOVERAGENT_TEACHER_TOKENIZER_DIR="$PWD/runs/s2_context_validation_20261117/teacher_tokenizer" \
timeout --signal=TERM 1300 \
 runs/teacher_local_20261114_01/worker_env/bin/python -u \
 scripts/probe_append_teacher.py \
 --appworld-root data/appworld \
 --output runs/s2_append_teacher_20261118/text_future_001 \
 --task-id a3ba388_3 \
 --successful-reference runs/teacher_go_64k_20261111_04/attempts/teacher_go_64k_20261111_04_a3ba388_3 \
 --text-template runs/s2_effective_segment_20261118_01/remote_backup/run/evolve/updates/001/attempt_1795000273069974960/actor/chat_template.jinja
```

上式是后续复现命令，未额外执行；需新的私有output，不覆盖本轮原件。当前所有Teacher探针已结束，0新增GPU推理/训练/dev/test；正式GRPO继续暂停。保留15步有效actor、optimizer与未消费组。接下来若继续Student验证，应以独立新协议运行，重点处理其纠错提示的一致性和发现后的实际调用；Teacher文本成功不能替代9B验证。

最终入口收尾后同一6文件回归再次通过：97 passed / 7.55s，Ruff check/307文件format/diff check通过；未再调用模型。

# execution_v6 Student 初始化兼容修复 · 2026-10-28

本次仅修复Stage 1验收后已确认的初始化问题。基线已fetch/pull至`e359d537fd95ef6d818c8e729b337b1b20be7b74`；分支`codex/p0-p2-recovery`，包含用户指定祖先`8e4b9dd`。已阅读AGENTS、PROJECT_PLAN和P4_STAGE_GATES。没有实施Stage 2–6，没有启动模型、GPU、AppWorld任务或训练。

## 缺陷与修复前后

基线`student.py:157–178`用System Prompt是否包含旧HARNESS_RULES/ANSWER_RULES判断检查器启用。用户指定的v6没有这些段落，即使`protocol_harness=True, answer_guard=True`也会报`Student protocol harness must be declared in the prompt`。本轮实际复现并保存原源码、异常和SHA，未改变Prompt来迁就构造器。

| 情况 | 基线 | 本次修复 |
| --- | --- | --- |
| 精确v6+两种检查器+可信新协议 | 初始化被旧文本检查拒绝 | 初始化、完整请求构造通过 |
| v6缺协议、关检查器、错误Harness revision或固定A模式 | 无独立v6契约 | 拒绝初始化 |
| v6预算超约定、源码/配置变更 | 无独立v6契约 | 拒绝初始化；更小预算允许 |
| legacy v5 | 检查旧规则声明 | 保持原逻辑；缺任一声明仍拒绝 |
| Schema错误、不可见工具、同批发现后立即调用 | 原共享Harness执行 | 原实现继续拒绝整批，不执行合法前缀 |
| 数值答案格式错误 | 一次同值纠正 | 原检查器继续执行，不自动改答案 |

修改点：

- `src/recoveragent/student.py:35`：独立协议`student_execution_v6_20261028`及固定配置摘要；`:42`核验源码锁。`:214`区分v6和legacy，核验精确Prompt、真实启用状态、B模式及预算；`:245`保留旧Harness文本检查；`:250`共用真实AnswerGuard构造。输出目录额外保存控制侧`execution_protocol.json`，不进入Actor输入。
- `configs/student_v6_runtime_lock.json`：新增锁，绑定基线/父锁、新源码及`session_contract`，不是编辑旧锁。配置包含原32768上下文、4096回复、40请求、16384累计生成、600秒上限；代码固定其规范JSON摘要，不允许单独改配置关闭检查或扩大预算。
- `src/recoveragent/student_evaluation.py:46,210,280`、`src/recoveragent/p3c.py:48,193,504,631`：仅增加新协议的验锁、配置匹配与构造器传参。P3-C控制器现在也把v6路由到对应锁，不错误回退默认旧锁。未调用这些入口运行真实任务，未改评价/状态/Memory算法。
- `tests/test_student_v6.py`：28项参数化回归；`tests/test_remote_validation_setup.py`更新当前/历史锁的预期；`tests/test_full732_stage1.py`分开合成worker连线与真实历史锁拒绝，新增拒绝测试。生产Stage 1模块与配置保持原字节。
- `docs/STATUS.md`、`docs/DECISIONS.md`、`docs/STUDENT_PROTOCOL.md`、README及本报告更新当前状态/使用说明，不改历史实验报告。

没有新增Harness算法、纠错次数、业务补救、工具或Prompt补丁。Schema、工具可见性、整批检查、调用ID、反馈、原始回执保存和执行预算继续复用原实现。

## 验证

完整软件套件：**456 passed**（基线427，净新增29）。Ruff check通过；Ruff format check **129 files already formatted**。

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/ruff check --no-cache src tests scripts
.venv/bin/ruff format --check --no-cache src tests scripts
```

针对性范围：真实检查器实例；完整消息/tools/token IDs/non-thinking请求；缺app_name的精确字段；未知字段/类型错误；非法批次无前缀副作用；不可见工具与下一轮发现边界；真实模拟返回进入下一请求；数值一次同值纠正；两次通用纠错上限；纠错共享请求预算；v5缺声明仍失败；源码/配置篡改拒绝；原始拒绝回执保留。

额外在本地离线加载**真实Qwen/Qwen3-4B tokenizer**，未加载权重：revision `1cfa9a7208912126459214e8b04321603b3df60c`，模板SHA `a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8`。合成环境顺序执行缺app_name→合法目录调用→`"$368"`拦截→`368`同值纠正。4个合成生成回执，2次模拟工具执行，输入2996 token、合成输出94 token、最终复验耗时2.389秒；每个实际请求重新渲染验证input IDs和32768窗口，System Prompt始终精确不变，历史配对合法。

最终兼容性审阅过程中共运行3次相同离线探针，分别保留独立证据；以上为最终源码锁下的单次数据，三次合计12个合成回执。

上述4次是**软件回执**，不是4次真实模型请求；不计Teacher示范、不代表Student成功率或官方任务评价。真实模型调用、真实AppWorld任务、Teacher付费调用、GPU、SSH、训练步数全部为0。

离线探针命令（脚本及完整回执保留于私有证据目录）：

```bash
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 CUDA_VISIBLE_DEVICES='' \
HF_HUB_OFFLINE=1 HF_HOME="$PWD/.cache/huggingface" \
.venv-inference/bin/python runs/student_v6_compat_20261028/probe.py \
  runs/student_v6_compat_20261028/NEW_PROBE
```

开发过程一次锁生成脚本未设置PYTHONPATH，导致新锁尚未生成时25项测试报FileNotFoundError；补充`PYTHONPATH=src`生成**新锁**后针对性72项通过。没有修改旧锁或放宽校验以消除失败。

## 完整性与版本边界

| 保留对象 | 校验 |
| --- | --- |
| execution_v6整个源文件 | SHA `352d106b7b052cc1a2b5c249e19c6a512e989355fc60f6495034f32db24a4400`，字节不变 |
| execution_v6 System Prompt | SHA `5ef2bb774a258ba0a0e00d94284a29720bad3d2578c9a2a54ec94a594272dbba`，未追加文本 |
| 旧v6运行锁 | SHA `56c63b44619d9180514fc2298a345318154856630419e19c428580d942f8ba26`，不变 |
| 新Student运行锁 | SHA `8fdc382b64508f5f2440a8a099eb4b77225ad6972ad1a854f61cc647875fa3fa` |
| 固定session_contract规范摘要 | `d12bc386a3e9c9c0f577937667b8768debc0743a0941dea4d87d711522ecceed` |
| 原95条数据manifest | `5b297cf38d7b23cb6dc02c461a0bb030c69e58d938bffda9c7569a67ad44b24c`，全部6,616文件SHA通过 |
| 两版已发布checkpoint | 14文件SHA通过，无权重加载或修改 |
| 原Stage 1证据 | 13文件SHA通过；旧报告/入口/配置不改 |
| 原任务分配/官方评价/Teacher/Memory源码 | 对基线逐文件比较不变 |

原运行锁在本次源码下拒绝是预期行为，**未重算旧哈希**。历史Stage 1探针与原共享评估配置仍绑定旧锁，精确复现使用`e359d537`及其环境；不能因为初始化修好了就修改旧状态字段宣称整个Full732就绪。新Student调用必须显式使用新runtime和真实开启的检查器，例如在已有合法环境中构造会话时：

```python
from recoveragent.student import StudentSession, V6_RUNTIME
session = StudentSession(
    adapter, tokenizer, messages, tools,
    tool_mode="dynamic_discovery",
    execution_protocol=V6_RUNTIME,
    protocol_harness=True,
    answer_guard=True,
)
```

这里的messages首项必须为原样v6系统消息，第二项为含instruction的公开任务JSON；tools必须匹配公开目录。此例只说明API，不代表任何任务/资源授权。

## 证据及结论

私有证据：`runs/student_v6_compat_20261028/`中的`before.json`、`student_before.py`、`baseline_files.json`、`release/after.json`、`release/tokenizer_probe/requests/`、`integrity.json`、`release/integrity.json`和`software_checks_release.txt`（首次离线探针也保留在根目录，最终复验绑定最终源码锁）；不上传原始任务或权重。

| 私有证据摘要 | SHA-256 |
| --- | --- |
| `software_checks_release.txt` | `ee1d456eb36357dfa3ea24df0d0442860e914801ead2c1a3abded45a1829c2a9` |
| `release/after.json` | `1dddccc659ad40a7039f1e50ac591f5a9dc78d8260fa6648077172026ff1b576` |
| `release/integrity.json` | `b8928ab86dba5ba0058feb98375718d5723e2616aac565c37e9fdb8ddfcf4753` |

**单项兼容修复PASS（软件及离线真实分词）**。真实Qwen模型推理、真实AppWorld端到端结果本轮未验证；不从合成回执推断真实模型遵守Prompt或自纠正能力。Full732受限用途、评分未知及新M1门槛仍在，冻结历史入口未被迁移或放行。Stage 2–6未执行，提交推送后停止。

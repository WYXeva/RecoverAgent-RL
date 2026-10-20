# 工具选择模式：官方 Predictor 与动态发现

本功能属于 P3-A 正常 Teacher 采集和工具协议验证。正式 Student 仍为 Qwen/Qwen3-4B，当前执行模型是已授权的 DeepSeek Teacher；没有开始训练，也没有改动 P0–P2 Runner。

| 项目 | A：`api_predictor` | B：`dynamic_discovery` |
| --- | --- | --- |
| 执行前 | Predictor 读取公开任务 instruction 和全部非 api_docs 应用的 API 功能简介目录 | 无独立预测请求 |
| 执行模型最初可调用的工具 | 预测选中 API 的完整官方 schema，一次性开放 | api_docs 与 supervisor 的公开 schema（当前 10 个） |
| 后续新增工具 | 不新增；即使预测出文档查询工具，也不会扩展 schema | 成功调用 show_api_doc 后，下次请求加入相应 schema |
| 谁选择工具 | Predictor 模型选 API；Actor 决定具体执行、参数及顺序 | Actor 边执行边选择要查询和调用的 API |
| 漏选时 | Actor 不能调用未提供 schema 的 API；不自动替模型补选 | Actor 可以继续查询文档并新增工具 |
| 成本 | 预测请求加执行请求全部计入 | 文档查询及执行全部计入 |

Actor 能看到的始终是该轮 API request 的 `messages` 和 `tools`。A 的 Predictor 能看到完整**功能简介目录**，A 的执行 Actor 只收到选中的**完整调用定义**。二者不是“执行 Actor 看见所有工具”。没有隐藏 evaluator、标准答案或其他分支信息进入 Actor。

## 官方实现及差异

固定上游 commit `42b5bcf3cd334fee33f0c37c02070a9f5807add5`：

- [common/APIPredictor](https://github.com/StonyBrookNLP/appworld/blob/42b5bcf3cd334fee33f0c37c02070a9f5807add5/experiments/code/common/api_predictor.py)：实际调用它的 `build_messages` 和 `predicted_output_to_apis`，不是自行写选择 prompt 冒充官方。
- [官方提示](https://github.com/StonyBrookNLP/appworld/blob/42b5bcf3cd334fee33f0c37c02070a9f5807add5/experiments/prompts/api_predictor.txt)：每行输出一个 `app.api`，优先召回。
- [官方 Function Calling Agent](https://github.com/StonyBrookNLP/appworld/blob/42b5bcf3cd334fee33f0c37c02070a9f5807add5/experiments/code/simplified/function_calling_agent.py)：按预测名称过滤 `task.api_docs.function_calling()`；本项目保持相同目录顺序。

运行模式固定 `predicted`，`demo_task_ids=[]`，最多 20 个选中 API。官方解析器忽略不属于公开目录的行、去重、在缺少时加入 `supervisor.complete_task`、再截取前 20 个。不用 `ground_truth` 模式，不加载需要 `task.ground_truth.required_apis` 的官方示例。`include_cache_control=False` 避免零示例时上游对空列表取 `[-1]`；这是公开参数，不修改依赖源码。

这是**官方零示例 Predictor 组件 + 本项目共同执行流程**的比较。与完整官方实验仍有区别：本项目 Actor 提示、DeepSeek transport、non-thinking 设置、原生批量协议、串行执行和有限预算均保持一致；没有复用官方 gold few-shot、官方 Actor 全部提示或其无限制预算。因此不得称作完整论文基线复现。

源码和提示下载到忽略的 `.cache/appworld-reference/`，每次载入检查 SHA-256。哈希写在 `tool_modes.SOURCES` 及每条预测记录中。只额外安装固定 Jinja2/MarkupSafe；不安装完整上游 Agent 框架，不修改 AppWorld。

## 运行

```bash
.venv/bin/python -m pip install -r requirements/tool-modes.txt
PYTHONPATH=src .venv/bin/python -m recoveragent.tool_modes prepare

# 使用已有授权和核实费率的私有配置；不要把密钥写入配置或提交 Git。
APPWORLD_CACHE="$PWD/.cache/appworld" IPYTHONDIR="$PWD/.cache/ipython" \
PYTHONPATH=src .venv/bin/python -m recoveragent.p3a compare \
  --config .cache/tool_modes_20261020_a.json \
  --output runs/tool_modes_20261020_a --credentials-file .env

# 从完整分母重算对比结果，包括失败尝试。
PYTHONPATH=src .venv/bin/python -m recoveragent.compare_tools \
  runs/tool_modes_20261020_a
```

输出目录必须不存在。单独采集可在私有配置中指定 `tool_mode`，运行已有 `p3a collect`。默认配置的付费开关仍关闭，实际运行需要当前会话已有授权或新操作者的明确授权及费率配置。

本轮预先固定官方 train 列表前两个任务 `82e2fac_1`、`82e2fac_2`，不根据成功结果选任务；它们属于同一场景家族。四次按 `B1 → A1 → A2 → B2` 串行执行，独立进程、独立 world、原始任务初态，环境种子 42。相同 Teacher/temperature，跨请求核对 backend fingerprint。每次预留 **20 次总模型请求、8192 累计生成 token、40 工具调用、450 秒**；单回复最多 **4096 token**。A 的 Predictor 消耗这些额度，不额外赠送免费 token。四次累计不超过 80 次请求/32768 输出 token/1800 秒（单次退出允许最多 30 秒清理，受全局墙钟限制）。操作性费用上限 USD 5，沿用用户“费用不限”授权，无自动付费重试。

通用采集配置的单轨迹累计输出上限仍是 16384；本轮四次比较将全局额度均分为 8192/次。旧 A/B/C 试点的实际单响应上限 2048 不改写。达到预算先停止，再以官方验证决定奖励；`finish_reason=length` 是响应不完整，拒绝执行该批次，不能入 Clean Dataset。

## 证据、排错和数据

- `requests/000.request.json` / `000.response.json`：A 的实际 Predictor 完整目录输入和原始预测。
- `requests/001...`：执行 Actor 的真实上下文、schema 和响应，两个模式格式相同。
- `trajectory.json`：独立 `predictor` 控制证据、`tool_mode`、固定/动态 schema、总成本；选择请求不拼入 Actor 历史。
- `evaluation.json`：保存终态后调用官方评价，与原始任务参照比较；不向 Actor 反馈隐藏测试。
- `comparison.json`：全部尝试、初态配对、进程隔离、backend 一致性、两种模式的成功/失败和成本。`PASS` 表示比较基础设施通过，不等于某方法胜出。
- `clean_dataset/`：保留全部尝试证据和成本，仅导出通过官方与格式/重复循环筛选的成功 Teacher 样本；成功样本保留 tool_mode。Predictor 消息不作为 Base-SFT Assistant 目标。

查看 A 的完整 Predictor 输入与后续 Actor 请求：

```bash
PYTHONPATH=src .venv/bin/python -m recoveragent.inspect_teacher \
  runs/tool_modes_20261020_a/teacher_<实际ID> --all \
  --output outputs/tool_modes_view_<新ID>.md
```

`--turn 1` 仍指执行 Actor 第一次回复，Predictor 单独列出，避免把两种角色的上下文混为一谈。所有原始日志、派生任务内容、数据包和视图仅本地保留；公开报告只包含汇总和哈希。

## 解释限制

两任务、同一场景、每种模式每任务一次，只能检查接口和观察开销，不能估计总体优劣。A 若漏选会锁住后续能力；B 需花费发现调用。哪种更好必须以后在固定模型、更广任务族、多次采样、相同端到端预算下验证。缓存命中与执行顺序影响实际计费和时延；同时报告输入/输出 token、预测和执行分项，按公开费率估算与账户余额差额分别表述。

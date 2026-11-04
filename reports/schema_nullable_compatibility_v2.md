# P0.1 — Nullable Schema Compatibility v2

2026-10-31完成验收。**软件契约修复PASS；真实AppWorld任务验证未执行；模型效果验证未执行。** 2026-11-04用户授权提交到GitHub供GPT检查；发布前确认11份交付文件与原验收哈希一致，源码/测试未变，仅补充发布状态。本次提交仅包含P0.1修复及测试文档，等待审查。

## 版本与根因

目标分支`codex/p0-p2-recovery`，只读fetch核验的远端HEAD为`8c654b13c7957f72650484e9186b92b6dae2f14b`。当前工作目录是无有效`.git`元数据的源码副本，不能伪称在此读到了HEAD；已有独立检出本地HEAD仍为`ce0f98aced59f818fe786099180befb04db51064`，其远端跟踪引用为8c654b13。本次编辑前已逐一确认待改核心文件与8c654b13相同，审查diff也以此为基准。没有切换、重置或提交用户工作目录。

固定AppWorld revision：`42b5bcf3cd334fee33f0c37c02070a9f5807add5`。其`api_docs.py:536–540`删除原OpenAPI参数的`anyOf: [..., {"type":"null"}]`中的null分支，并保留`default: null`。例如公开`gmail.show_category_sizes`签名为`read: bool | None = Query(None, ...)`，原OpenAPI允许boolean/null，而上游Function Calling只剩boolean。`adapter.call_code`与Harness严格校验这个非空Schema，因此拒绝None。严格校验本身未被删除或绕过。

源代码只读检查说明FastAPI/Pydantic已经解析了公开Python签名，原始OpenAPI保留可靠的nullable信息，优先使用它；无需执行字符串annotation，也无需维护人工参数白名单。`default: null`和描述中的None不作为类型证据。

## 实现与边界

- 新模块`src/recoveragent/schema_compatibility.py`，独立revision **`schema_nullable_compatibility_v2`**。从固定安装来源读取公开类型，只匹配当前catalogue中的公开函数；admin、Private-API、No-API-Docs不会被加入可见工具。
- 原始公开类型必须明确允许null，并且其被固定上游转换器去掉null后的结构必须匹配当前参数Schema。仅对符合条件的参数添加null分支。非空分支的enum、minimum/maximum、exclusive bounds、array items、object required/additionalProperties等全部保留；注释字段放在外层一次，避免重复长描述。
- 顶层required不变。Nullable必填参数可以传null，缺失仍拒绝。上游合成的认证字段保持原非空字符串规则。未知类型、无法匹配的声明、未解析/外部/循环引用、未知revision等均明确停止，不猜测、不静默退回旧Schema。当前仅支持固定版本使用的已知typed/anyOf表示；不承诺任意未来OpenAPI或Python类型均兼容。
- 新Schema明确带`additionalProperties:false`，展示既有`call_code`一直执行的禁止多余参数规则。已有显式false及嵌套约束保留；若输入Schema与该规则冲突则拒绝初始化，不改写为更宽约束。测试捕获校验器收到的Schema，确认与模型请求中相同工具的Schema相等。
- `AppWorldAdapter.configure_schema_compatibility`为共享入口；Teacher `p3a.run_teacher_episode`和Student `student_evaluation.run_episode`都在初始工具选择前调用。继承该Adapter的动态发现路径继续从同一catalogue增加工具，无校验端私自宽松的第二份Schema。
- `harness.py`、`call_code`严格校验主体、认证来源规则、两次纠错预算及整批预校验逻辑均未更改；execution_v6 System Prompt字节不变。

新功能通过配置项显式选择，例：

```json
{"schema_compatibility_revision": "schema_nullable_compatibility_v2"}
```

本轮没有将它写入旧实验配置。缺省None保持历史Schema。未知值拒绝。历史运行源码锁保持不变，仍会拒绝当前已变更源码；本软件修复不授予真实运行权限，也不重签旧锁或发布绕过旧门槛的执行配置。

## 原始证据与SHA

启用时先创建控制侧`schema_compatibility.json`，落盘成功后才替换Adapter的catalogue。文件保存完整原始/转换后Schema、原始OpenAPI、每个变化参数的公开来源、安装revision/来源、API模块源码SHA、兼容实现SHA、整体及逐参数SHA。`adapter.versions`记录revision、前后SHA及审计文件内容SHA。已有审计路径拒绝覆盖；原始对象深复制保留，不改第三方安装文件或Teacher原包。

Schema哈希定义为`recoveragent.io.canonical`的UTF-8 JSON的SHA-256，文件字节SHA另行记录，不混用。

固定公开定义核对：**11应用、473工具定义、248个参数恢复null**，0 AppWorld实例、0任务加载。该检查是公开类型契约检查，未执行这些API。

- 原始完整catalogue SHA：`4d1e9d031272b7b2b44ab78cb0dbe8c3b8d5648d13cf1cc08581d812f0f5e441`。
- 转换后完整catalogue SHA：`57a6afdddfd2629da928c031660f482e7fb12f1b4fa42f3324419c7c01ce4fe1`。
- 合成/固定公开夹具：`tests/fixtures/appworld_nullable_contract_v2.json`，包含Gmail boolean、Venmo nullable enum、Supervisor初始工具及API文档发现接口；夹具绑定公开模块源文件SHA。
- 私有本地完整审计：`runs/schema_nullable_compatibility_v2/public_catalogue_audit.json`。公开报告不包含原始任务、凭据、数据库或隐藏答案。

## 必须测试的覆盖

| 要求 | 验证 |
|---|---|
| A 合法Nullable通过 | 原Gmail read:null先证明旧Schema拒绝，再确认新call_code与Harness均接受；另测nullable enum和初始工具answer |
| B 非Nullable仍拒绝null | 五类基础类型及认证字段；包括故意保留default:null的非Nullable合成字段 |
| C 必填字段 | 缺失access_token及必填nullable value仍拒绝；显式null与缺失分开 |
| D 类型错误 | boolean/string/integer/array/object错误、boolean冒充integer、非整数均拒绝 |
| E 原有约束 | enum、min/max、exclusive bounds、pattern、array items/maxItems、嵌套required/additionalProperties |
| F 两侧一致 | 合成Teacher完整入口和StudentSession捕获真实构造的请求，初始及发现后tools逐结构相等 |
| G 初始/动态工具 | 初始Supervisor工具已转换；发现后Gmail工具与catalogue一致；同一批发现不能立即使用新工具 |
| H 整批拒绝 | 合法nullable调用在前、非法参数在后，整批拒绝且执行调用数不增加 |
| I SHA可核验 | 原始、转换后、逐参数和来源SHA重新计算；输入不变、审计先保存再激活、拒绝覆盖 |

还覆盖未知类型/引用/版本拒绝、来源错配拒绝、旧配置不变、v6 Prompt及其他守卫不变；固定包全部473份公开工具定义通过转换检查，未创建World。

## 验收结果

| 检查 | 结果 |
|---|---|
| Nullable针对性pytest | **57 passed** |
| 相关Harness/Adapter/Student回归 | **133 passed** |
| 完整pytest | **690 passed**，3条固定上游Pydantic弃用警告 |
| Ruff check | **PASS** |
| Ruff format check | **PASS**，174个Python文件已符合格式 |
| 冻结引用 | 56份本轮前后核验项保持一致，包括Prompt、Harness、旧锁、数据Manifest及C244历史报告引用 |
| 真实AppWorld任务/工具执行 | **未执行，0** |
| 真实Teacher/Student模型调用、GPU、训练任务 | **未执行，均0** |
| 任务成功率提升 | **未验证，不作声明** |

执行命令（项目根目录）：

```bash
PYTHONPATH=src:. CUDA_VISIBLE_DEVICES='' .venv/bin/pytest -q tests/test_schema_compatibility.py
PYTHONPATH=src:. CUDA_VISIBLE_DEVICES='' .venv/bin/pytest -q tests/test_contracts.py tests/test_harness.py tests/test_harness_review.py tests/test_harness_refactor.py tests/test_student_batch.py tests/test_student_v6.py tests/test_evaluation_classification.py
PYTHONPATH=src:. CUDA_VISIBLE_DEVICES='' .venv/bin/pytest -q
.venv/bin/ruff check src scripts tests
.venv/bin/ruff format --check src scripts tests
```

最终日志及命令/退出码见`runs/schema_nullable_compatibility_v2/final_checks.json`、`nullable_tests.log`、`harness_adapter_regression.log`、`full_pytest.log`、`ruff_check.log`及`ruff_format_check.log`。静态Schema核对脚本保存在同目录`check_public_catalogue.py`。

开发过程中首先捕获并修复了仅default、无类型声明未拒绝的边界；一个Student合成测试缺少既有Harness声明，按原规则修正夹具。全套首轮20个失败来自旧测试临时清单/旧锁假设，调整仅测试用清单，并继续断言两个历史生产锁必须拒绝当前源码。随后一次全套暴露新增公开定义导入自动加载.env的测试隔离问题，已在该测试中禁用上游自动加载；失败日志脱敏保留。没有修改被测凭据规则或生产旧锁来放行。

## 修改清单与交付状态

新增兼容模块、57项针对性测试及固定公开API夹具；修改Adapter/Teacher/Student入口接线；调整`tests/conftest.py`与`tests/test_official_retry.py`的临时锁夹具及历史拒绝断言；更新`docs/STATUS.md`、`docs/DECISIONS.md`（D083）和本报告。

未修改第三方AppWorld文件、execution_v6、M1 checkpoint、原Teacher数据或C244结果；未采集/重跑任务、做Student推理、实现Context Manager或修改Recovery-SFT/GRPO。本次按2026-11-04用户授权提交至`codex/p0-p2-recovery`，供用户/GPT检查，停止于P0.1。

# 环境修复后的64k S0固定dev24复评

2026-11-13。用户授权修复环境、检查其他应用并重跑固定24题，逐题分析结果。本次使用已验收的256条示范Base-SFT S0，不更新模型、不补采Teacher、不进行其他学习阶段。当前运行已启动，完整结果和逐题结论待实际执行结束后补充；不将此前0/7复制为本次结果。

## 修复与实际环境检查

新建独立`eval_env_20261113`，固定SQLModel 0.0.24、SQLAlchemy 2.0.52、Pydantic 2.12.3、freezegun 1.5.1。解决固定AppWorld无时区datetime与SQLModel 0.0.48默认UTCDateTime冲突；没有修改第三方源码，原GPU训练环境和旧评估环境保留。新依赖锁为`requirements/appworld-evaluation-20261113.lock.txt`，项目AppWorld可选依赖同步固定相关版本。

`scripts/probe_appworld_environment.py`在独立train初态、无隐藏ground_truth的临时实验中，通过公开工具动态发现、严格Schema校验和真实API调用，检查9个业务应用。119次工具调用，0模型请求；所有应用通过，SQLite内存URI、无时区数据库写入读回、状态保存/恢复后逻辑哈希一致。

| 应用 | 已通过的代表性检查 |
| --- | --- |
| Amazon | 搜索商品、购物车、支付卡、下单；实际订单含1条正确明细，购物车清空 |
| Todoist | 新建项目与有到期日/时长的任务、修改完成状态、读回、删除 |
| SimpleNote | 创建、修改、读回、删除笔记；合法null参数 |
| FileSystem | 建目录、创建/修改/读取/删除文件 |
| Gmail | 创建/修改/读取/删除草稿；合法null参数 |
| Phone | 创建/修改/读取/删除闹钟 |
| Spotify | 创建/修改/读取/删除歌单，加入及移除歌曲 |
| Splitwise | 创建/修改/读取/删除分账组 |
| Venmo | 创建/修改/读取/删除模拟支付请求 |

这些是环境功能检查，既不是模型成功解，也没有评分或写入训练池；没有证明所有API组合均无问题。前两次探针的缺少train初态、脚本参数重名及空收藏测试条件已保留为失败记录，第三次完整通过，未伪装成首次全部通过。探针摘要SHA：`22f81f0429dd64693bb14ddf56190ec976c16bc56ce9687cdfc59b7b19f77558`。

新增`appworld_naive_datetime_20261113`执行环境契约。新评估启动前要求上述完整探针回执；每个实际worker核对依赖版本并验证无时区存储。识别工具错误包裹中的SQLAlchemy/SQLite、内存/导入及服务器5xx错误，保留真实返回、调用意图和中断状态，停止同批后续调用及评估批次，记为基础设施异常、success=null，不执行自动重试。业务4xx仍作为正常任务反馈，正常数据中出现异常单词不会触发。

## 软件验收与历史保留

- 新增及相关合成测试61 passed；最终完整`PYTHONPATH=src:. .venv/bin/python -m pytest -q`为1008 passed、1 skipped；跳过的CPU训练模块在`.venv-v2`独立进程运行，3 passed。
- `ruff check src scripts tests`及`ruff format --check src scripts tests`通过，209个文件格式符合要求。
- 测试覆盖真实入口接线、合法null、业务错误继续、数据库错误停止且不执行合法后缀、真实错误持久化、九应用探针准入、新锁校验与历史锁拒绝。
- 此前188份配置/锁文件字节保持不变；没有重签旧锁、修改execution_v6、模型权重、Teacher数据、旧评分或划分。历史协议的合成测试使用临时单元测试锁，生产旧锁继续拒绝当前源码。
- 本地含ML依赖的混合全测曾在pandas扩展GC处崩溃；原日志保留。上述全测与独立CPU三项是实际完成的验证，不把崩溃运行计为通过。远端推理/环境使用原Python 3.12栈。

## 本次固定运行

入口复用`scripts/evaluate_v2_checkpoint.py`，通过`--config configs/v2_base_dev24_envfix_20261113.json`明确选择新环境和源码锁。Student使用相同新锁，旧入口保持旧锁校验，没有绕过源码校验来加载修改后的执行依赖。

| 项目 | 配置/证据 |
| --- | --- |
| GPU | 既有A100-SXM4-80GB，无新增租机/续费 |
| 模型 | Qwen/Qwen3.5-9B，revision `c202236235762e1c871ad0ccb60c8ee5ba337b9a` |
| S0 | 原`runs/base_002/S0_base`，658步accepted checkpoint；只读加载 |
| LoRA tensor SHA | `5da6444aa979e2ce0957b3bba82bd239ba0f59fad40024a7ea0a35b7f4534966` |
| checkpoint回执SHA | `828251a14a515d2e8703d54c529abbdb1c5d1b05b4ae17d9566dc7aa92d1064b` |
| 数据Manifest SHA | `3e66b757b49cd874a412d5d440c6a2e7a0e47eb5098d593af0347646da4970df` |
| 任务 | 原冻结dev24及原顺序，全部从各自干净初态执行一次 |
| 单题预算 | 65,536上下文、80请求、累计生成16,384、单回复4,096、1,200秒 |
| 采样 | non-thinking、seed42、temperature0.8、top_p1，工具协议不变 |
| 本次作业上限 | 9小时容纳24份原单题预算及评分/加载开销；用户本轮明确授权完整24项复评 |
| 评分 | 原任务官方参照，dev轨迹不进入训练或生成器 |
| 源码锁SHA | `b1ba5a8e0fdc320ed9b5bbfcf1a155a2688dcc1682977b56af49e02e74665de8` |
| 部署归档SHA | `c02938d59ac24d42479424784d8b2cc3d09147db0e5914d9ef7c5ff72c6d2965` |

远端根：`/root/gpufree-data/recoveragent_base64_20261112_02/eval_fixed_20261113_01/`，结果`runs/dev24_001/`，控制日志和退出/归档回执`handoff/`。10:40:30（UTC+8）启动controller392、评估393；进程号仅为启动时记录。

本地私有目录：`runs/v2_dev24_envfix_20261113_01/`。独立监控保存逐次状态，结束后回传完整轨迹/官方评分/终态及探针记录，按归档和逐文件SHA核验，并fsync文件与目录。实例仍由用户控制台关机，GPU作业退出不代表停止平台计费。

## 完整结果与逐题分析

待本次24项真实执行结束、产物回传和核验后填入。环境修复前的0/7仅作历史故障证据，不作为完整配对基线。最终逐题区分官方成功、业务/推理失败、预算终止及基础设施异常，不用终止标签代替原因分析。

# A244持续监控：HTTP诊断续报

2026-10-28，基线3a332d55ef61c7a6dbd2b884e1b073daf32af760。用户要求持续监控直到冻结A244全部尝试结束；本报告是运行中工程记录，不是最终A244验收。

serial_relay已封存51项：20官方成功、17合格、3成功超长暂缓、7有效失败、24主链未知。最后一个在途任务3650990_1正常完成成功；新serial_diagnostics配置按51份SHA继承，继续剩余193项，workers=1，不重新调用旧任务。原始清单见[51项元数据](full732_a244_collection_checkpoint51_20261028.json)。

连续8个任务在第2–3请求出现HTTP400，前序实际响应均没有reasoning_content；旧代码没有保存错误体，仅凭相关性不能证明原因。[DeepSeek官方说明](https://api-docs.deepseek.com/guides/thinking_mode/)要求工具模式回传历史推理，缺失可能返回400。本轮两个独立合成传输探针（历史字段缺失/显式空字符串）均200，说明渠道或历史差异仍需真实证据；没有把空串冒充缺失的真实推理，也没有改写Teacher历史来“修复”旧数据。

两探针不是AppWorld任务或Teacher样本，共2请求、706输入token、31生成token，按原费率估价0.000173736；实际逐请求扣费未知。已加入新campaign的全局消耗，不重复旧收费请求；原4次接入诊断保留，因此额外诊断合计6请求、15305输入、515生成、5个AppWorld工具调用（新探针实际0工具）。

最小工程变更：TeacherTransportError可附带显式启用的诊断；仅读取最多65537字节的错误体，落盘前只保留SHA、长度、是否可能截断及固定关键词布尔值。没有保存错误原文、API Key、任务正文或任意异常文本。P3-A在已有provider_error下保留该证据；没有自动重试、没有改官方奖励。未启用此选项的旧行为保持不变，新源码与新锁绑定，历史锁不重签。

新运行的前两项365e0a3_2为502，36705ae_3为400且仅tool_messages关键词命中；关键词不能替代完整错误原因，未将它报告为已证实的reasoning根因。错误继续分类，未知费用保留，固定后续任务照常执行。响应模型名称依用户决定只审计，继续固定请求deepseek-v4.1-flash。

501 pytest PASS（12.24秒），Ruff check/140文件format PASS。新增测试保证安全摘要不含原文/secret、原HTTP状态不变、旧选项默认无诊断、诊断预算纳入全局。真实探针只验证接口可接受性，不是Teacher示范；真实成功仍需官方评分、跨进程终态一致与Qwen格式/长度/mask验收。

当前入口：`configs/full732_a244_teacher_serial_diagnostics_20261028.json`及同名_lock.json；运行目录`runs/full732_a244_serial_diagnostics_20261028/`；源包/探针与测试日志在私有`runs/full732_a244_parallel_20261028_setup/`。未读取历史95条、未启动GPU/Student/训练/B/C，保持execution_v6和Full732原划分不变。

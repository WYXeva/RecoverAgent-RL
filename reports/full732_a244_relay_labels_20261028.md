# A244中转站返回名称兼容续报

2026-10-28，用户明确要求将中转站各种返回模型名称仅作审计，不再据此中止本轮串行采集。固定请求模型仍为deepseek-v4.1-flash，服务地址不变；这是用户提供的渠道身份声明，不能当作上游权重独立验证。

原串行任务2c544f9_1第25请求返回ali/deepseek-v4.1-flash，导致旧显式名称规则停止于39/244。只读models目录只列原请求名，公开plaza未列出ali映射；原回执/UNKNOWN/费用预留不修改，也不重发该请求。截止该暂停点：17官方成功、14合格、3成功超长暂缓、7有效官方失败、15主链未知；后验自动保存状态评分与主链结果分别保留。见[39项封存清单](full732_a244_collection_checkpoint39_20261028.json)，205项尚未运行不当作失败。

按用户随后授权，新增`response_model_policy=user_asserted_relay_aliases_v1`，仅在显式relay + model_response_id_v1 + 用户声明来源时接受任意非空response model字符串，保持实际值不改写。请求model/endpoint仍由配置和原始请求核验；response ID/usage、预算、schema/工具可见性/纠错、官方成功与独立恢复门槛不变。原未启用策略的历史配置继续严格验证原名及显式别名，旧锁不能使用新源码冒充复现。

新配置`configs/full732_a244_teacher_serial_relay_20261028.json`及独立源码锁，配置SHA `02adcbc64bea1424d0c88a96bf1b068dcdf7e087f774efd4e393099091f84743`；39份不可变review按SHA继承，单worker仅执行剩余205项。实际dispatch已启动，实时目录`runs/full732_a244_serial_relay_20261028/`，不把“启动”报告为全量完成。

500 pytest PASS（12.26秒），Ruff check/140文件format PASS。新增用合成回执验证名称差异不改原响应、不影响请求模型，非法空名称/缺失ID/超额usage仍拒绝，旧配置不受新策略影响。该软件测试不计新增Teacher示范。真实任务结果仍需官方评分/跨进程恢复/格式和token长度全部验收。

没有重读历史95条Teacher、没有修改execution_v6、A/B/C划分、官方评价器、旧数据/锁/权重，也没有运行Student/GPU/训练。所有错误留待首轮结束后集中处理。最终全量/部分完成报告应以实际封存结果为准。

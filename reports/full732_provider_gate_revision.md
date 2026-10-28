# A244 Provider 预检修订

2026-10-28，基线 `77cf9e3a709afeabe82bc8f8916335ad88bc554c`。

用户明确要求撤掉上一轮新增的中转站条款拦截。本轮删除 `fresh_teacher.provider_preflight` 中按 `znbcode.com` 域名返回 `PROVIDER_TERMS_DISALLOW_TRAINING_DATA` 的硬编码分支。条款记录作为历史来源证据保留，不再作为运行时否决条件，也不被改写为已经取得服务方授权。

默认配置改为 `configs/full732_a244_teacher_fresh_v2.json`，对应新的 `full732_a244_teacher_fresh_v2_lock.json`。A244 顺序、任务级/总预算、Teacher 参数、execution_v6、Harness、AnswerGuard、数据准入和官方评价均不改变。v1 配置、锁、报告和全部历史数据保持原字节；旧锁在改动后的源码上拒绝执行，历史复现使用原提交。

验证：482 pytest PASS，Ruff check PASS，Ruff format 137 文件 PASS。合成测试证明同一域名在凭据、价格和访问状态满足时不再因条款被拒绝；另外验证旧锁拒绝新源码。测试没有发送真实模型请求，没有新的 AppWorld 任务执行、GPU 或训练。

使用显式 `read_credentials('.env')` 的当前离线预检确认 Key 存在，dotenv 地址/模型与配置一致。剩余原因仅为 `PRICE_SOURCE_REQUIRED`、`VERIFIED_PRICE_REQUIRED`、`PROVIDER_ACCESS_NOT_VERIFIED`。此前报告生成脚本用 `read_credentials(None)`，未加载文件，其最终预检中的 `TEACHER_CREDENTIAL_MISSING` 只代表该脚本没有传入文件，**不能解释为用户未提供 Key**；本轮保留旧回执，并提供显式路径的新回执纠正这一点。实际采集命令应带 `--credentials-file .env`。

价格尚未核实，前次 `/v1/models` HTTP 403 的技术原因尚未解决；本轮不声称重新验证过服务访问，也不把访问标记直接改为成功。没有发送模型请求，A244 仍为 0 尝试，不将其记为模型失败。此软件修订不等于认证接口或费用已验收。

验证命令：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/ruff check --no-cache .
.venv/bin/ruff format --check .
```

私有证据：`runs/full732_provider_gate_revision_20261028/` 下的 `pytest.log`、`ruff_check.log`、`ruff_format.log`、`preflight_with_dotenv.json` 和基线文件清单。凭据未写入这些回执或 Git。

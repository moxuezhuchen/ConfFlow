# 已知失败清单

只登记在干净基线上同样失败、与当前改动无关的测试；不阻塞无关卡片。每项写明仓库、基线提交、节点 ID 与原因，修复后删除。

## JobDesk（`jobdesk-v2`，基线 `5c90732`）

| 节点 ID | 现象 | 登记日期 |
|---|---|---|
| `tests/application/test_cards_library.py::test_library_modules_import_without_qt` | 在干净 `5c90732` 上失败，与 J1 补丁无关；另一类触发条件是环境问题：JobDesk 未可编辑安装时报 `No module named jobdesk_v2` | 2026-10-06 |
| `tests/application/test_p0_boundary.py::TestLiveProducerParity::test_vendored_fixture_matches_live_producer[boundary_protocol.json]` | 在干净 `5c90732` 上失败（live producer 与 vendored 夹具不一致），与 J1 补丁无关 | 2026-10-06 |
| `tests/test_rc_artifact.py::test_guard_reports_clean_verdict` | 环境类失败：JobDesk 未可编辑安装时报 `No module named jobdesk_v2`，与改动无关 | 2026-10-07 |
| `tests/test_rc_artifact.py::test_shipped_wheel_crcs` | 环境类失败：JobDesk 未可编辑安装时报 `No module named jobdesk_v2`，与改动无关 | 2026-10-07 |

## ConfFlow 跨仓测试的 JobDesk 检出要求

ConfFlow 的跨仓测试（`cross_repo` 标记，经 `tests/v4/jobdesk_integration.py`
解析）请显式设置 `JOBDESK_V2_SRC` 指向 JD master 的干净检出（例如
`/tmp/jd-n26/src` 这类与开发检出隔离的只读 pin），不要使用有未提交改动的
开发检出：开发检出的未提交改动会改变消费者侧解析/校验行为，使跨仓证据
不可复现。`JOBDESK_V2_ALLOW_ANY_SHA=1` 仅为开发期逃生口，不替代干净检出。

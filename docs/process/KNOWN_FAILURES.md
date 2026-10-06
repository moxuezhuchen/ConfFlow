# 已知失败清单

只登记在干净基线上同样失败、与当前改动无关的测试；不阻塞无关卡片。每项写明仓库、基线提交、节点 ID 与原因，修复后删除。

## JobDesk（`jobdesk-v2`，基线 `5c90732`）

| 节点 ID | 现象 | 登记日期 |
|---|---|---|
| `tests/application/test_cards_library.py::test_library_modules_import_without_qt` | 在干净 `5c90732` 上失败，与 J1 补丁无关 | 2026-10-06 |
| `tests/application/test_p0_boundary.py::TestLiveProducerParity::test_vendored_fixture_matches_live_producer[boundary_protocol.json]` | 在干净 `5c90732` 上失败（live producer 与 vendored 夹具不一致），与 J1 补丁无关 | 2026-10-06 |

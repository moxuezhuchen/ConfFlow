# 架构守卫清单（L0.4a 正式清单）

- 基准：CF 分支 `refactor/l0-policy-inventory` @ `db022bab71fbbbcd00b4e23998dfe131ed6599c6`（L0.3 正式提交）。六个来源文件（`tests/v4/test_architecture_boundaries.py`、`test_v42_debt.py`、`test_v46_debt.py`、`test_v46_production_gates.py`、`scripts/v4_arch_scan.py`、`scripts/architecture_metrics.py`）在 `ce0dd996` 与 `db022bab` 两基点**逐字节相同**（sha256 实测一致，见 L0.4a 执行日志），行号沿用原登记并经重定位确认有效。
- 性质：**L0.4a 正式清单，根对登记口径已确认，L0.4b/c 执行尚待派工**。本卡为文档登记，未实施任何 policy、未删除/修改任何测试。
- 类别缩写：`AST-IMP` 静态 AST 导入扫描；`AST-SYM` 静态 AST 符号扫描；`TXT-CODE` 源码文本（AST 去 docstring+注释）；`TXT-RAW` 原始全文（含 docstring/注释）；`DISK` 文件/目录存在性；`IMP` importlib 可导入性；`SUB` subprocess 导入隔离；`INPROC` 进程内 import/属性/身份断言；`META` 扫描器自检元测试；`METRIC` 度量定义（无断言）。
- 行号均指 `db022bab` 工作树实际内容（机械重定位说明：六个来源文件逐字节未变，`ce0dd996` 行号直接有效；本清单沿用原行号并抽样核对通过）。

---

## 1. tests/v4/test_architecture_boundaries.py（1912 行，V4-1 主 gate）

策略数据（常量，非断言本身）：`FORBIDDEN_IMPORT_PREFIXES`(42-60)、`FORBIDDEN_LEGACY_MODULES`(62-90)、`FORBIDDEN_SYMBOLS`(92-129)、扩展根定义(137-155)、`FORBIDDEN_LEGACY_RUNTIME_PREFIXES`(162-170)、`PRODUCER_CONFIG_AUTHORITY_IMPORTS`(186)、`EXTENDED_LEGACY_TOKEN_EXEMPTIONS`(193-195)、`KNOWN_PRODUCER_LEGACY_IMPORTS`(207)、`REMOVED_LEGACY_MODULES`(234-300)、`RETIRED_RUNTIME_MODULES`(307-326)、`PUBLIC_V3_WIRE_MODULES`(338-348)、`RETIRED_V3_SYMBOLS`(359-381)、`PUBLIC_V1_V2_WIRE_MODULES`(390-411)、`V2_MIGRATION_MODULES`(421)、`RETIRED_V1_V2_WIRE_TOKENS`(429-459)、权威路径/消费者/marker(467-507)。

| # | 位置（行号） | 规则含义 | 类别 | 重复关系 | L0.4b/c / 保留 |
|---|---|---|---|---|---|
| 1 | TestStaticImports::test_domain_has_no_repository_imports (580-586) | `confflow/domain` 禁止一切 `confflow.*` 导入（域层零仓库依赖） | AST-IMP；机制=AST 解析 Import/ImportFrom 节点 | 独立（最严封闭） | L0.4b 承接 |
| 2 | TestStaticImports::test_v4_and_execution_import_only_allowed_repository_modules (588-627) | v4/execution/persistence/programs/remote 仅可导入 domain/execution/workflow.v4/programs/persistence/remote；例外：`confgen_schema.py`、`confgen_executor.py` 允许指定 `science.confgen.*`（文件级） | AST-IMP；机制=AST 解析导入语句；文件级例外表 | 与 #3 在 persistence 上重叠（#3 更严）；与 #68 programs 上互补 | L0.4b 承接 |
| 3 | TestStaticImports::test_persistence_imports_only_domain_and_self (629-652) | persistence 仅 domain+persistence，含相对导入解析与越级检查 | AST-IMP；机制=AST 解析导入（含相对导入层级解析） | 与 #2 重叠但更严（禁 persistence→programs/remote） | L0.4b 承接 |
| 4 | TestStaticImports::test_forbidden_import_prefixes_absent_everywhere_in_v4_core (654-668) | 六根禁 `FORBIDDEN_IMPORT_PREFIXES`（config/core/shared/application/control/worker*/cli/main/contract/fixture_agent 等） | AST-IMP；机制=AST 解析导入语句 | 与 #2 部分重叠（allowlist 之外）；与 #69 表不同（含 shared/cli，无 config/calc） | L0.4b 承接 |
| 5 | TestStaticImports::test_no_legacy_runtime_import_in_v4_core (670-684) | 六根禁精确 `FORBIDDEN_LEGACY_MODULES`（28 个旧运行时/配置模块） | AST-IMP；机制=AST 解析导入语句，精确模块名比对 | 与 #72、#76 重复（同表不同根集） | L0.4b 承接 |
| 6 | TestStaticImports::test_no_forbidden_legacy_symbols_as_code (686-703) | 六根 AST 符号禁 `FORBIDDEN_SYMBOLS`（39 个旧词汇；排除本测试文件） | AST-SYM；机制=AST 收集 Name/Attribute/def/class 等符号集比对 | 与 #66（v42 子集表，未复用）、#71（v46 复用同表扩根）、#11（扩展根）重复 | L0.4b 承接 |
| 7 | TestStaticCodeVocabulary::test_domain_has_no_output_path_contract (723-729) | domain 代码文本禁 `output_path`/`input_xyz` | TXT-CODE；机制=AST 去 docstring/注释后子串匹配 | 与 #8、#9 重叠（子集） | L0.4b 承接 |
| 8 | TestStaticCodeVocabulary::test_v4_core_has_no_legacy_path_contracts (731-738) | execution/v4/persistence/programs/remote 代码文本禁 `output_path` | TXT-CODE；机制=AST 去 docstring/注释后子串匹配 | 与 #9 重叠（子集） | 同上 |
| 9 | TestStaticLegacyFilenameContracts::test_no_legacy_filename_contracts_as_code (741-773, tokens 749-756) | 六根代码文本禁 6 个旧文件名/词汇契约（含含点号的 result.xyz/failed.xyz，AST 符号法不可见） | TXT-CODE；机制=AST 去 docstring/注释后子串匹配 | 覆盖 #7/#8；与 #12、#14、#62(部分)、#67(部分)、#75(部分) 重叠 | L0.4b 承接 |
| 10 | TestExtendedV4ProductionRoots::test_extended_roots_have_no_legacy_runtime_imports (827-834; 判定 794-816) | 扩展根（producer/analysis/science.confgen + v4cli.py/v4_entry.py/v4_run.py）禁旧运行时导入；producer 仅允许 `confflow.config.contract_schemas`；workflow.v4 恒允许 | AST-IMP；机制=AST 导入解析 + `_is_forbidden_legacy_runtime_import` 判定表 | 独立表 `FORBIDDEN_LEGACY_RUNTIME_PREFIXES`/`EXTENDED_FORBIDDEN_LEGACY_MODULES`（与 #4/#5 差异：core.exceptions 豁免、config 仅 producer 限单模块） | L0.4b 承接 |
| 11 | 同类::test_extended_roots_have_no_forbidden_symbols (836-845) | 扩展根 AST 符号禁 `FORBIDDEN_SYMBOLS`；`v4_entry.py` 豁免 `input_xyz`（formal_v4_runner 兼容参数） | AST-SYM；机制=AST 符号集比对 + 文件级豁免表 | 与 #6 同表扩根 | L0.4b 承接 |
| 12 | 同类::test_extended_roots_have_no_legacy_filename_contracts (847-856) | 扩展根代码文本禁 LEGACY_FILENAME_TOKENS（同豁免） | TXT-CODE；机制=AST 去 docstring/注释后子串匹配 + 豁免表 | 与 #9 同表扩根 | L0.4b 承接 |
| 13 | 同类::test_formal_service_and_control_paths_have_no_legacy_runtime_imports (858-872) | import-only 根（application/execution、control_worker.py）仅导入门；不做符号/文件名扫描（兼容签名携带 input_xyz 参数名） | AST-IMP；机制=AST 导入解析（同一判定函数，import-only 根集） | 与 #10 同判定函数、不同根集（签名豁免所以范围收窄） | L0.4b 承接 |
| 14 | TestRemoteBoundary::test_remote_has_zero_legacy_code_tokens (878-902) | remote 代码文本禁 13 个旧 token | TXT-CODE；机制=AST 去 docstring/注释后子串匹配 | 与 #9 大量重叠（remote 在六根内）——重复 | L0.4b 承接 |
| 15 | 同类::test_remote_has_no_compiler_or_yaml_imports (904-922) | remote 禁 compiler/yaml 导入（含相对导入解析） | AST-IMP；机制=AST 导入解析（相对导入归一）+ 段名匹配 | 独特规则 | L0.4b 承接 |
| 16 | 同类::test_remote_package_does_not_export_retired_helpers (924-930) | `confflow.remote` 包导出面无 lease/supervision/schema | INPROC；机制=进程内 import 包并检查 `__all__`/`hasattr` 导出面 | 与 #19 互补（导出面 vs 加载面） | L0.4c 承接 |
| 17 | TestRuntimeIsolation::test_importing_domain_does_not_import_legacy_packages (945-954) | subprocess：import domain 不加载 core/config/calc/workflow | SUB；机制=subprocess -c 导入后检查 sys.modules | 与 #1 互证（动态视角） | L0.4c 承接 |
| 18 | 同类::test_importing_v4_core_does_not_import_v3_runtime (956-967) | subprocess：v4+execution 不加载非 v4 的 workflow.*、config、calc | SUB；机制=subprocess -c 导入后检查 sys.modules | 与 #65/#18 型同；独立根 | L0.4c 承接 |
| 19 | 同类::test_remote_seams_do_not_load_retired_helpers (969-980) | subprocess：remote 四 seam 不加载 retired remote 三模块 | SUB；机制=subprocess -c 导入后检查 sys.modules | 与 #21/#16 相关（加载面） | L0.4c 承接 |
| 20 | 同类::test_v4_runtime_entries_do_not_load_dev_fixtures (982-994) | subprocess：四正式入口不加载 memory/synthetic_producer/fixture_agent 开发夹具 | SUB；机制=subprocess -c 导入后检查 sys.modules | 独特（fixture 隔离） | L0.4c 承接 |
| 21 | TestRetiredRuntimeBoundary::test_retired_modules_are_not_importable (1004-1015) | `RETIRED_RUNTIME_MODULES`（18 模块）importlib 不可导入 | IMP；机制=进程内 importlib.import_module 尝试（ModuleNotFoundError 即通过） | 与 scripts/v4_arch_scan `RETIRED_RUNTIME_MODULES`(101-120) 逐字重复（双份维护） | L0.4c 承接 |
| 22 | 同类::test_retired_modules_are_absent_from_disk (1017-1019) | 同表磁盘不存在 | DISK；机制=文件路径存在性走查（.py / __init__.py） | 与 scanner `_retired_module_hits`(354-370) 重复 | 同上 |
| 23 | 同类::test_retired_modules_are_not_imported_by_v4_sources (1021-1042) | 九根（含 producer/analysis/science）AST 导入禁 retired（含子模块前缀） | AST-IMP；机制=AST 导入解析 + 模块前缀匹配 | 与 #5 重叠（同集合多数、不同匹配语义=前缀） | L0.4b 承接 |
| 24 | TestV3PublicWireRetired::test_public_v3_wire_modules_are_absent_from_disk (1055-1057) | V3 公共线 9 模块磁盘不存在 | DISK；机制=文件路径存在性走查 | 与 scanner `RETIRED_V3_WIRE_MODULES`(127-137) 双份维护 | L0.4b 承接 |
| 25 | 同类::test_public_v3_wire_modules_are_not_importable (1059-1070) | 同表不可导入 | IMP；机制=进程内 importlib.import_module 尝试 | 同上 | 同上 |
| 26 | 同类::test_no_production_module_imports_a_retired_v3_module (1072-1082) | 整个 confflow 包 AST 导入禁 V3 wire（含子模块） | AST-IMP；机制=AST 导入解析 + 模块前缀匹配（全 confflow 包） | 独特（全包扫描） | L0.4b 承接 |
| 27 | 同类::test_no_internal_v3_migration_kernel_is_retained (1084-1089) | 内部 V3 迁移内核恒空 | META(常量守卫)；机制=进程内常量相等比较 + 路径存在性 | 独特（决策钉定） | L0.4b 承接 |
| 28 | 同类::test_v4_roots_reference_no_retired_v3_symbol (1091-1110) | 十根 AST 符号禁 19 个 `RETIRED_V3_SYMBOLS` | AST-SYM；机制=AST 符号集比对 | 独特表 | L0.4b 承接 |
| 29 | TestV1V2PublicWireRetired::test_public_v1_v2_wire_modules_are_absent_from_disk (1126-1128) | V1/V2 wire 20 模块磁盘不存在 | DISK；机制=文件路径存在性走查 | 与 scanner `RETIRED_V1_V2_WIRE_MODULES`(145-166) 双份维护 | L0.4b 承接 |
| 30 | 同类::test_public_v1_v2_wire_package_directory_is_gone (1130-1134) | `confflow/config/canonical` 目录不存在（namespace 包防御） | DISK；机制=目录存在性检查 | 独特 | L0.4b 承接 |
| 31 | 同类::test_public_v1_v2_wire_modules_are_not_importable (1136-1147) | 同表不可导入 | IMP；机制=进程内 importlib.import_module 尝试 | 同 #29 | L0.4c 承接 |
| 32 | 同类::test_no_production_module_imports_a_retired_v1_v2_module (1149-1159) | 全包 AST 导入禁 V1/V2 wire | AST-IMP；机制=AST 导入解析 + 模块前缀匹配（全 confflow 包） | 与 #26 同型 | L0.4b 承接 |
| 33 | 同类::test_no_v1_migration_kernel_is_retained (1161-1169) | V1 内核恒空；V2 内核恰为 v2_adapter；磁盘不存在 | META(常量守卫)+DISK；机制=进程内常量相等比较 + 路径存在性 | 独特 | L0.4b 承接 |
| 34 | 同类::test_v4_roots_reference_no_retired_v1_v2_token (1171-1191) | 十根**原始全文**（含 docstring/注释——本文件罕见）禁 28 个 `RETIRED_V1_V2_WIRE_TOKENS` | TXT-RAW；机制=原始全文（含 docstring/注释）子串匹配 | 与 scanner `RETIRED_V1_V2_WIRE_TOKENS`(174-202) 双份维护且**表内容有差异**（test 版多 `confflow.core.types`、`confflow.workflow.plan`；见待确认 U3） | L0.4b 承接 |
| 35 | 同类::test_current_protocol_majors_are_not_swept_up (1193-1220) | 当前 6 个协议 id 不得进入 retired token 表（防误伤元守卫） | INPROC；机制=进程内常量表成员检查（policy 表健全性） | 独特（表健全性） | L0.4b 承接 |
| 36 | TestConsolidatedHelperAuthorities::test_no_local_atomic_helper_copies_regrow (1233-1244) | 七 scope 原始全文禁 5 个 `_atomic_write_bytes/_fsync_*` 本地复制 marker（fsatomic 权威外） | TXT-RAW；机制=原始全文（含 docstring/注释）子串匹配 | 与 #38 互补（定义面 vs 身份面） | L0.4b 承接 |
| 37 | 同类::test_job_name_sanitizer_is_not_redefined_in_programs (1246-1254) | programs 内禁 `sanitize_job_name` 重定义（_naming 权威外） | TXT-RAW；机制=原始全文（含 docstring/注释）子串匹配 | 与 #39 互补 | L0.4b 承接 |
| 38 | 同类::test_publish_consumers_share_the_single_authority (1256-1264) | 6 个 publish 消费者 + 2 个 fsync 消费者身份 `is` 权威对象 | INPROC；机制=进程内 import + 对象身份 `is` 断言 | 与 #36 互补 | L0.4c 承接 |
| 39 | 同类::test_both_renderers_reexport_the_single_sanitizer (1266-1272) | gaussian/orca renderer 身份同一性 | INPROC；机制=进程内 import + 对象身份 `is` 断言 | 与 #37 互补 | L0.4c 承接 |
| 40 | TestProducerImportIsolation::test_producer_legacy_import_debt_is_empty (1297-1317) | subprocess：import producer 的遗留依赖集合 == 空（`KNOWN_PRODUCER_LEGACY_IMPORTS`） | SUB；机制=subprocess -c 导入后枚举 sys.modules 过滤比对 | 与 #41/#42 互补（白名单基线 vs 黑名单） | L0.4c 承接 |
| 41 | 同类::test_producer_does_not_load_canonical_config_tree (1319-1338) | subprocess 两种 producer 入口均不加载 canonical 树/config.models | SUB；机制=subprocess -c 导入后检查 sys.modules | 与 #42 部分重叠 | L0.4c 承接 |
| 42 | 同类::test_producer_does_not_pull_legacy_runtime_packages (1340-1352) | subprocess 禁 calc/blocks/confts/engine/v3_runtime/v3_dataflow/binding_v2/canonical | SUB；机制=subprocess -c 导入后检查 sys.modules | 与 #5/#23 部分重叠（运行时视角） | L0.4c 承接 |
| 43 | 同类::test_schema_authority_is_single_source_for_every_consumer (1354-1377) | 三个 schema id 值钉定 + 四消费者 `is` 权威对象 | INPROC；机制=进程内 import + 常量值/对象身份断言 | 与 #44 互补 | L0.4c 承接 |
| 44 | 同类::test_config_package_has_no_v1_v2_wire_surface (1379-1403) | `confflow.config` 对 5 个旧名 raise AttributeError；contract_schemas 可导入 | INPROC；机制=进程内 import + AttributeError 面检查（pytest.raises 循环） | 独特（fail-closed facade 面） | L0.4c 承接 |
| 45 | TestFacadeLazyIsolation::test_core_package_stays_lazy (1423-1433) | subprocess：import core 不加载 config/core.models/core.types/core.validation | SUB；机制=subprocess -c 导入后检查 sys.modules | 独特（lazy facade） | L0.4c 承接 |
| 46 | 同类::test_application_packages_stay_lazy (1435-1448) | subprocess：application(+execution) 包 lazy，5 个子模块不加载 | SUB；机制=subprocess -c 导入后检查 sys.modules | 独特 | L0.4c 承接 |
| 47 | 同类::test_v4_runtime_entries_do_not_load_canonical_config (1450-1466) | subprocess：v4cli/v4_entry/control_worker 不加载 canonical/core.models/shared.config_validation | SUB；机制=subprocess -c 导入后检查 sys.modules | 与 #20、#41 部分重叠（同一组入口、不同禁集合） | L0.4c 承接 |
| 48 | 同类::test_core_public_surface_still_importable (1468-1477) | core 兼容公共名仍可导入（保留性，非禁令） | INPROC；机制=进程内 import 公共名（兼容面保留性） | —— | 非架构规则（兼容面存在性），原样保留 |
| 49 | 同类::test_application_public_surface_still_importable (1479-1495) | application 兼容公共名仍可导入（保留性） | INPROC；机制=进程内 import 公共名（兼容面保留性） | —— | 非架构规则（兼容面存在性），原样保留 |
| 50 | TestPackaging::test_packages_are_discoverable (1501-1522) | 六包可被路径走查发现（setuptools 等价规则） | DISK；机制=包目录 `__init__.py` 路径走查 | —— | 非架构规则（打包），原样保留 |
| 51 | TestPackaging::test_no_namespace_package_gaps (1524-1526) | 四根 `__init__.py` 存在 | DISK；机制=文件存在性检查 | 与 #30 相关（防 namespace gap） | L0.4b 承接 |
| 52 | TestSchemaHasNoHiddenCleanup::test_schema_text_has_no_cleanup_vocabulary (1532-1537) | V4 JSON schema 文本禁 auto_clean/delete_work_dir/clean_opts/ibkout | INPROC(生成文本)；机制=进程内生成 schema 后文本匹配 | 与 #53 同类 | 原样保留（产品契约面）或 L0.4c 归入 schema 冻结组 |
| 53 | 同类::test_calculation_model_exposes_only_the_frozen_blocks (1539-1553) | CalculationModel 字段集合恰为 10 个冻结键 | INPROC；机制=进程内 pydantic model_fields 检查 | —— | 非架构规则（schema 冻结契约），原样保留 |
| 54 | test_legacy_module_inventory_is_intentional (1556-1572, 参数化) | `FORBIDDEN_LEGACY_MODULES ∪ REMOVED_LEGACY_MODULES` 每模块：removed→必须磁盘不存在；未 removed→必须仍存在（allowlist 恒空） | DISK；机制=文件路径存在性走查（参数化双分支） | 与 #22/#29 等部分重叠（存在性总账）；独特在"必须仍存在"半边 | L0.4b 承接 |
| 55 | TestV45ModuleCoverage::test_v45_modules_exist (1608-1611) | 13 个 V4-5 模块存在 | DISK；机制=文件存在性检查 | 覆盖性守卫 | L0.4b 承接 |
| 56 | 同类::test_v45_modules_are_covered_by_scan_roots (1613-1616) | V4-5 模块均在扫描根内 | DISK；机制=路径包含关系走查 | 同型 #86 | L0.4b 承接 |
| 57 | 同类::test_v45_modules_face_no_legacy_imports (1618-1626) | V4-5 模块禁 FORBIDDEN_IMPORT_PREFIXES | AST-IMP；机制=AST 解析导入语句 | 与 #4 重复（子根重扫） | L0.4b 承接 |
| 58 | TestNoTaskDispatch::test_scanner_flags_task_enum_dispatch (1735-1738) | 元测试：task-dispatch 扫描器必须命中 fixture | META；机制=扫描器对固定 fixture 的纯函数自检（元测试） | 与 #60 配套 | L0.4b 承接 |
| 59 | 同类::test_scanner_ignores_scientific_strings (1740-1741) | 元测试：科学字符串常量不命中 | META；机制=扫描器对固定 fixture 的纯函数自检（元测试） | 配套 | 同上 |
| 60 | 同类::test_no_task_enum_dispatch_in_tree (1743-1749) | execution+programs：task enum 成员（IRC/QST/NEB/GOAT）禁用于 if/while/assert/match 判定 | AST-SYM(自定义)；机制=AST 自定义扫描：Enum 成员 + 判定语句引用 | 独特规则（按名字派发） | L0.4b 承接 |
| 61 | TestNoFilenameOrdinalPairing::test_scanners_flag_pairing_idioms (1850-1854) | 元测试：filename/ordinal 扫描器命中 fixture | META；机制=双扫描器对固定 fixture 的纯函数自检（元测试） | 与 #62/#63 配套 | L0.4b 承接 |
| 62 | 同类::test_no_filename_idioms_in_tree (1856-1862) | v4+execution 禁 basename/splitext/stem/suffix（属性与名字） | AST-SYM(自定义)；机制=AST 自定义扫描：属性/名字 token | 与 #78 重复（v46 扩根） | L0.4b 承接 |
| 63 | 同类::test_no_range_ordinal_pairing_in_tree (1864-1875) | v4+execution 禁 range 序数跨集合下标配对（atom_mapping.py 显式豁免） | AST-SYM(自定义)；机制=AST 自定义扫描：range 序数下标跨集合配对 | 与 #79 重复（v46 扩根） | L0.4b 承接 |
| 64 | 同类::test_ordinal_within_item_usage_is_confined (1877-1886) | member_index/point_ordinal 词汇限于 4 个白名单文件 | TXT-CODE；机制=AST 去 docstring/注释后词汇 confinement | 与 #80 重复（v46 扩根） | L0.4b 承接 |
| 65 | TestV45Packaging::test_module_imports_without_legacy (1901-1913, 参数化) | V4-5 每模块 subprocess 导入隔离 | SUB；机制=subprocess -c 导入后检查 sys.modules（参数化） | 与 #18 同型、不同根 | L0.4c 承接 |

## 2. tests/v4/test_v42_debt.py（190 行，V4-2 扩展 gate；**未复用**主 gate，helper 与符号表独立维护）

| # | 位置 | 规则含义 | 类别 | 重复关系 | L0.4b/c / 保留 |
|---|---|---|---|---|---|
| 66 | TestV42LegacySymbols::test_no_forbidden_symbols (145-153; 表 33-47) | domain/execution/v4/programs AST 符号禁 14 个旧符号 | AST-SYM；机制=AST 符号集比对 | 与 #6 大量重叠：表是 `FORBIDDEN_SYMBOLS` 子集、根少 persistence/remote；**独立维护的重复表** | L0.4b 承接 |
| 67 | TestV42LegacyTokens::test_no_forbidden_tokens (159-167; 表 49-56) | 同根代码文本禁 6 token（input_xyz/output_path/total_memory/max_parallel_jobs/auto_clean/chk_from_step） | TXT-CODE；机制=AST 去 docstring/注释后子串匹配 | 前两 token 与 #9 重叠；`total_memory/max_parallel_jobs/auto_clean/chk_from_step` 是 #9 没有的——**部分独特** | L0.4b 承接 |
| 68 | TestProgramsImports::test_programs_import_boundary (173-182) | programs 仅 domain/execution/programs+外部 | AST-IMP；机制=AST 解析导入语句 | 与 #2 在 programs 上重叠但**更严**（禁 programs→workflow.v4/persistence/remote） | L0.4b 承接 |
| 69 | TestProgramsImports::test_no_legacy_package_imports (184-190; 表 64-78) | programs 禁 13 个前缀（含 config/calc/blocks） | AST-IMP；机制=AST 解析导入语句 | 与 #4 表不同：此处有 config/calc/blocks，#4 无——互补禁表 | L0.4b 承接 |

## 3. tests/v4/test_v46_debt.py（486 行，V4-6 扩展 gate；**显式 import 复用**主 gate）

| # | 位置 | 规则含义 | 类别 | 重复关系 | L0.4b/c / 保留 |
|---|---|---|---|---|---|
| 70 | TestV46BoundaryReuse::test_new_symbols_are_disjoint_from_existing (205-207) | `iprog` 与既有符号表不相交 | META(常量守卫)；机制=进程内常量集合交集检查 | 独特 | L0.4b 承接 |
| 71 | 同类::test_existing_symbols_still_absent_from_extended_roots (209-217) | `_v46_strict_roots`（八根，analysis/producer 现已存在）重跑 `FORBIDDEN_SYMBOLS` | AST-SYM；机制=AST 符号集比对 | 与 #6 重复（扩根重扫；producer/analysis 与 #11 重叠） | L0.4b 承接 |
| 72 | 同类::test_existing_legacy_modules_still_unimported (219-226) | 同根重跑 `FORBIDDEN_LEGACY_MODULES` | AST-IMP；机制=AST 导入解析 + 精确模块名比对 | 与 #5 重复 | L0.4b 承接 |
| 73 | TestV46NewSymbols::test_no_iprog_symbol (232-240) | strict roots 禁 `iprog` 符号 | AST-SYM；机制=AST 符号集比对 | 独特新词 | L0.4b 承接 |
| 74 | 同类::test_no_numeric_program_dispatch (242-248) | strict roots 禁 6 个基名的整常量下标派发（programs[0] 等） | AST-SYM(自定义)；机制=AST 自定义扫描：Subscript 整常量下标 | 独特 | L0.4b 承接 |
| 75 | TestV46ProducerLegacyTruth::test_no_legacy_truth_tokens_as_code (254-262; 表 72-78) | strict roots 代码文本禁 result.xyz/failed.xyz/workflow_stats/output_path/min_xyz | TXT-CODE；机制=AST 去 docstring/注释后子串匹配 | 与 #9 重叠（result.xyz/failed.xyz/output_path）；`workflow_stats`/`min_xyz` 独特 | L0.4b 承接 |
| 76 | TestV46NoOldRuntimeFallback::test_no_engine_import_in_strict_roots (268-275) | strict roots 禁 engine 导入 | AST-IMP；机制=AST 导入解析 + 精确匹配 | 与 #5 重复（engine 在 FORBIDDEN_LEGACY_MODULES 内） | L0.4b 承接 |
| 77 | 同类::test_engine_imports_pinned_to_legacy_entrypoints (277-286) | cli.py+application 的 engine 导入集合 == 空白名单（4b97ba7 后清空） | AST-IMP；机制=AST 导入解析 + 精确匹配 == 空白名单 | 独特（钉定式 allowlist 语义） | L0.4b 承接 |
| 78 | TestV46NoFilenameOrdinalPairing::test_no_filename_idioms (296-302) | 复用 #62 扫描器，根=v4/execution/analysis/producer | AST-SYM；机制=AST 自定义扫描（复用主 gate 扫描器） | 与 #62 重复（扩根） | L0.4b 承接 |
| 79 | 同类::test_no_range_ordinal_pairing (304-315) | 复用 #63 扫描器扩根 | AST-SYM；机制=AST 自定义扫描（复用主 gate 扫描器） | 与 #63 重复 | 同上 |
| 80 | 同类::test_ordinal_within_item_usage_is_confined (317-326) | 复用 #64 扩根 | TXT-CODE；机制=AST 去 docstring/注释后词汇 confinement | 与 #64 重复 | 同上 |
| 81 | TestV46JobdeskDoublesHaveNoConfflowImports::test_doubles_are_confflow_free (362-372) | 跨仓 double（tests/v4/test_v46_cross_repo.py + JD e2e 测试文件）内含 jobdesk 名字的类/函数禁 confflow 导入/属性引用 | AST-IMP(自定义)；机制=AST 解析指定文件内 confflow 导入/属性引用 | 与 #82 同规则不同文件；**引用 JD 仓库绝对路径 /opt/jobdesk-v2-v4**（见待确认 U4） | L0.4b 承接 |
| 82 | TestV46E2EJobdeskDoublesHaveNoConfflowImports::test_e2e_doubles_are_confflow_free (410-420) | e2e double 文件同规则 | AST-IMP；机制=AST 解析指定文件内 confflow 导入/属性引用 | 同 #81 | L0.4b 承接 |
| 83 | TestV46NewSymbolsInApplicationCli::test_no_iprog_symbol_in_application_cli (441-448) | application+cli.py 禁 iprog（legacy 符号在入口合法，故只禁新词） | AST-SYM；机制=AST 符号集比对 | 与 #73 同词不同根 | L0.4b 承接 |
| 84 | 同类::test_no_numeric_program_dispatch_in_application_cli (450-455) | 同根数值派发禁 | AST-SYM；机制=AST 自定义扫描：Subscript 整常量下标 | 与 #74 同规则不同根 | L0.4b 承接 |
| 85 | TestV46ModuleCoverage::test_v46_modules_exist (461-466) | 13 个 V4-6 模块存在 | DISK；机制=文件存在性检查 | 与 #55 同型 | 保留（清单式守卫） |
| 86 | 同类::test_v46_modules_are_covered_by_scan_roots (468-474) | V4-6 模块均在 strict roots 内 | DISK；机制=路径包含关系走查 | 与 #56 同型 | 保留（清单式守卫） |
| 87 | 同类::test_v46_modules_face_no_exact_legacy_imports (476-486) | V4-6 模块禁精确 FORBIDDEN_LEGACY_MODULES（注：仅精确匹配，producer 的 config.canonical.* 合法子导入不触发） | AST-IMP；机制=AST 导入解析 + 精确匹配 | 与 #5 部分重叠（精确匹配语义、子根） | L0.4b 承接 |

## 4. scripts/v4_arch_scan.py（411 行，CLI 扫描器；由 #88 钉定 clean；非 pytest 断言，登记为规则数据）

| # | 位置 | 规则含义 | 类别 | 重复关系 | L0.4b/c / 保留 |
|---|---|---|---|---|---|
| 88 | `SCOPE`(55-65) + `_import_hits`(312-334) | 九个正式入口路径（v4cli/application 三件/execution/control.py/producer/remote/analysis）AST 导入禁 24 个 `FORBIDDEN_IMPORT_PREFIXES` | AST-IMP；机制=AST 解析导入 + 前缀匹配（scanner 独立表） | 与 #4/#10 表**三份独立维护**，内容互有出入（scanner 无 confflow.config 总前缀、含具体 engine/state 模块；test #4 含 application/control_worker） | L0.4b 承接 |
| 89 | `_import_hits`(326-333) | analysis 包禁 `confflow.persistence` 导入（persistence bypass） | AST-IMP；机制=AST 解析导入 + analysis 专属前缀禁令 | 独特规则 | L0.4b 承接 |
| 90 | `PATTERNS`(204-223) | 15 个正则模式：TaskRunner/CalcStepRunner/ResultsDB/WorkflowState*/iprog/itask+get_itask/chk_from_step/result.xyz| failed.xyz；机制=tokenize+AST 剥注释/docstring 后逐行正则（15 项，见附录 B） |output_xyz/output_path/orca-fallback/first-match/FAKE_MODE 等假夹具标记/StepResult(/duplicate-authority(register_executor 等)/silent-fallback | TXT-CODE(tokenize 剥注释/docstring) | 与 AST-SYM/TXT-CODE gates 大量重叠（TaskRunner 等）；**部分 scanner 独有**：orca-fallback、first-match、fake-marker、stepresult-shortcut、duplicate-authority、silent-fallback、iprog 正则版 | L0.4b：重叠部分收敛；scanner 独有的运行路径模式保留 |
| 91 | `_retired_module_hits`(354-370) | RETIRED_RUNTIME/V3_WIRE/V1V2_WIRE 三表磁盘存在性 | DISK；机制=文件路径存在性走查 | 与 #21/#22/#24/#25/#29/#31 双份维护 | L0.4b 承接 |
| 92 | `_retired_v1_v2_token_hits`(373-381) | scoped 文件全文（剥注释/docstring）禁 V1/V2 token 表 | TXT-CODE；机制=剥注释/docstring 后子串匹配 | 与 #34 同源双份，**表内容有差异**（scanner 版无 core.types/workflow.plan） | L0.4b 承接 |
| 93 | `_strip_comments_and_docstrings`(272-297) | docstring/注释剥离机制（tokenize+AST spans） | 机制；机制=机制本身（tokenize+AST docstring spans） | 与测试侧 `_code_text_only` 等价机制（三种实现：boundaries/v42/v46 各一份） | L0.4b 承接 |

## 5. scripts/architecture_metrics.py（201 行，度量定义，无断言）

| # | 位置 | 规则含义 | 类别 | 重复关系 | L0.4b/c / 保留 |
|---|---|---|---|---|---|
| 94 | `V4_ROOTS`(46-51) | V4 可达闭包四入口钉定：v4cli、application.v4_entry、application.execution.workflow_adapter、control_worker | METRIC；机制=度量口径定义（U8：口径不得变更） | 独特（单一度量口径；docstring 明言"固定定义永不更改"） | 保留：L0.4b/c 不得随 policy 重构改变口径；如需变更为显式决策 |
| 95 | `collect`(157-175) | PHYSICAL/V4_REACHABLE/LEGACY_OR_NON_V4_REACHABLE 三度量（模块数+LOC） | METRIC；机制=纯 AST 闭包/LOC 计算（无断言） | 独特 | L0.4b 承接 |

## 6. tests/v4/test_v46_production_gates.py（958 行；本任务只登记调用扫描器的相关测试）

| # | 位置 | 规则含义 | 类别 | 重复关系 | L0.4b/c / 保留 |
|---|---|---|---|---|---|
| 96 | TestArchitectureScannerGate::test_architecture_scanner_gate (942-958) | `v4_arch_scan.scan()==[]`（进程内）+ CLI 子进程退出码 0 且 stdout 含 "clean"（scope 入口零命中被钉定） | INPROC+SUB；机制=进程内 scanner.scan() + subprocess CLI 退出码/stdout | 将 #88-#92 的总结果钉为 clean；与所有 pytest gates 互补（不同技术栈的平行防线） | L0.4c 承接 |
| — | TestLegacyEntryFailClosed::test_legacy_entry_fail_closed (919-936)【邻接，非扫描器调用】 | legacy workflow 输入（global.iprog）被 v4cli 拒绝执行（退出码 1 + `legacy_workflow_not_executable`）；未盖生产章的 ScientificResult 无 result_id 且 fail-closed | SUB+INPROC | 与 #73/#74（iprog 词禁）配套的**运行时**面 | 原样保留：运行时 fail-closed 契约，非静态架构规则 |

---

## 覆盖统计（修订）

- **编号条目 96 条**（#1–#96），另有**邻接非编号 1 条**（TestLegacyEntryFailClosed，运行时 fail-closed，非扫描器调用）——两者区分，不合并表述。
- 源码 assert 对账（独立 AST 复核，与根 `root-assert-index.json` 逐条一致）：主 gate `test_architecture_boundaries.py` **88** 条 assert + **1** 处 `pytest.raises(AttributeError)` 位点（1396 行，循环覆盖 5 个旧名）＝ 89 个断言位点；`test_v42_debt.py` **4**；`test_v46_debt.py` **20**。三文件合计 **112 条 assert + 1 处 raises 位点 = 113**。源码 assert 数只是覆盖核对口径，**不是测试节点数**（循环/参数化在运行时放大：如 #54 参数化覆盖约 57 个模块、#21 循环 18 模块、#51 单语句循环 4 根）。
- 112 条 assert 已逐条映射到编号条目（附录 A），无遗漏、无未归属。
- b/c 归属（修订后）：静态扫描/数据/存在性/口径（AST-IMP、AST-SYM、TXT-*、DISK、元测试、常量钉定、度量）归 **L0.4b**；subprocess 导入隔离、进程内 importlib 可导入性、导出面/身份/加载面归 **L0.4c**（机制已逐条注明，不以"import"字样误判）。

## G13 未来需求（**planned，现不存在**，不伪称已实现）

| P# | 需求 | 状态 |
|---|---|---|
| G13-P1 | `ConfgenStateKey` 类体例外（在符号/词汇类扫描中豁免该类体） | planned，L0.4b/c 设计时纳入；现无对应代码或豁免表条目 |
| G13-P2 | 禁止 kernel 三属性访问（新增 AST 属性访问禁令） | planned；现 `FORBIDDEN_SYMBOLS`/属性扫描均无此规则 |
| G13-P3 | 仅 `engine` 与 `confgen` 包的模块级 `__getattr__` 对指定三个 torsion 兼容名有受限 import 例外 | planned；现为全量禁令（#45/#5 等），无该例外结构 |

## 附录 A：逐断言登记（112 条 assert + 1 处 pytest.raises 位点）

来源：根 `root-assert-index.json`（file/scope/line/source）+ 本清单条目映射 + 机制与归属。"断言源码"超长处截断（`…`），完整源码以根索引与基准行号为准。同一函数多条 assert 全部单列；未发现测试函数之外的 helper 断言（`_task_dispatch_offenders` 等纯函数无 assert；`_is_forbidden_legacy_runtime_import` 等判定 helper 无 assert）。

| 子编号 | 文件 | 类 | 函数 | 源行 | 断言源码（短） | 归属条目 | 机制/归属+标注 |
|---|---|---|---|---|---|---|---|
| A-001 | test_architecture_boundaries.py | TestStaticImports | test_domain_has_no_repository_imports | 586 | `assert offenders == []` | #1 | L0.4b |
| A-002 | test_architecture_boundaries.py | TestStaticImports | test_v4_and_execution_import_only_allowed_repository_modules | 627 | `assert offenders == []` | #2 | L0.4b |
| A-003 | test_architecture_boundaries.py | TestStaticImports | test_persistence_imports_only_domain_and_self | 652 | `assert offenders == []` | #3 | L0.4b |
| A-004 | test_architecture_boundaries.py | TestStaticImports | test_forbidden_import_prefixes_absent_everywhere_in_v4_core | 668 | `assert offenders == []` | #4 | L0.4b |
| A-005 | test_architecture_boundaries.py | TestStaticImports | test_no_legacy_runtime_import_in_v4_core | 684 | `assert offenders == []` | #5 | L0.4b |
| A-006 | test_architecture_boundaries.py | TestStaticImports | test_no_forbidden_legacy_symbols_as_code | 703 | `assert offenders == []` | #6 | L0.4b |
| A-007 | test_architecture_boundaries.py | TestStaticCodeVocabulary | test_domain_has_no_output_path_contract | 729 | `assert offenders == []` | #7 | L0.4b |
| A-008 | test_architecture_boundaries.py | TestStaticCodeVocabulary | test_v4_core_has_no_legacy_path_contracts | 738 | `assert offenders == []` | #8 | L0.4b |
| A-009 | test_architecture_boundaries.py | TestStaticLegacyFilenameContracts | test_no_legacy_filename_contracts_as_code | 773 | `assert offenders == []` | #9 | L0.4b |
| A-010 | test_architecture_boundaries.py | TestExtendedV4ProductionRoots | test_extended_roots_have_no_legacy_runtime_imports | 834 | `assert offenders == []` | #10 | L0.4b |
| A-011 | test_architecture_boundaries.py | TestExtendedV4ProductionRoots | test_extended_roots_have_no_forbidden_symbols | 845 | `assert offenders == []` | #11 | L0.4b |
| A-012 | test_architecture_boundaries.py | TestExtendedV4ProductionRoots | test_extended_roots_have_no_legacy_filename_contracts | 856 | `assert offenders == []` | #12 | L0.4b |
| A-013 | test_architecture_boundaries.py | TestExtendedV4ProductionRoots | test_formal_service_and_control_paths_have_no_legacy_runtime_imports | 872 | `assert offenders == []` | #13 | L0.4b |
| A-014 | test_architecture_boundaries.py | TestRemoteBoundary | test_remote_has_zero_legacy_code_tokens | 902 | `assert offenders == []` | #14 | L0.4b |
| A-015 | test_architecture_boundaries.py | TestRemoteBoundary | test_remote_has_no_compiler_or_yaml_imports | 922 | `assert offenders == []` | #15 | L0.4b |
| A-016 | test_architecture_boundaries.py | TestRemoteBoundary | test_remote_package_does_not_export_retired_helpers | 929 | `assert [name for name in retired if name in remote.__all__] == []` | #16 | L0.4c |
| A-017 | test_architecture_boundaries.py | TestRemoteBoundary | test_remote_package_does_not_export_retired_helpers | 930 | `assert [name for name in retired if hasattr(remote, name)] == []` | #16 | L0.4c |
| A-018 | test_architecture_boundaries.py | TestRuntimeIsolation | test_importing_domain_does_not_import_legacy_packages | 954 | `assert result.returncode == 0, result.stderr` | #17 | L0.4c |
| A-019 | test_architecture_boundaries.py | TestRuntimeIsolation | test_importing_v4_core_does_not_import_v3_runtime | 967 | `assert result.returncode == 0, result.stderr` | #18 | L0.4c |
| A-020 | test_architecture_boundaries.py | TestRuntimeIsolation | test_remote_seams_do_not_load_retired_helpers | 980 | `assert result.returncode == 0, result.stderr` | #19 | L0.4c |
| A-021 | test_architecture_boundaries.py | TestRuntimeIsolation | test_v4_runtime_entries_do_not_load_dev_fixtures | 994 | `assert result.returncode == 0, result.stderr` | #20 | L0.4c |
| A-022 | test_architecture_boundaries.py | TestRetiredRuntimeBoundary | test_retired_modules_are_not_importable | 1015 | `assert still_present == []` | #21 | L0.4c |
| A-023 | test_architecture_boundaries.py | TestRetiredRuntimeBoundary | test_retired_modules_are_absent_from_disk | 1019 | `assert present == []` | #22 | L0.4b |
| A-024 | test_architecture_boundaries.py | TestRetiredRuntimeBoundary | test_retired_modules_are_not_imported_by_v4_sources | 1042 | `assert offenders == []` | #23 | L0.4b |
| A-025 | test_architecture_boundaries.py | TestV3PublicWireRetired | test_public_v3_wire_modules_are_absent_from_disk | 1057 | `assert present == []` | #24 | L0.4b |
| A-026 | test_architecture_boundaries.py | TestV3PublicWireRetired | test_public_v3_wire_modules_are_not_importable | 1070 | `assert still_present == []` | #25 | L0.4c |
| A-027 | test_architecture_boundaries.py | TestV3PublicWireRetired | test_no_production_module_imports_a_retired_v3_module | 1082 | `assert offenders == []` | #26 | L0.4b |
| A-028 | test_architecture_boundaries.py | TestV3PublicWireRetired | test_no_internal_v3_migration_kernel_is_retained | 1085 | `assert INTERNAL_ONLY_V3_MIGRATION_MODULES == ()` | #27 | L0.4b；常量钉定/policy 数据（保留） |
| A-029 | test_architecture_boundaries.py | TestV3PublicWireRetired | test_no_internal_v3_migration_kernel_is_retained | 1089 | `assert present == []` | #27 | L0.4b；常量钉定/policy 数据（保留） |
| A-030 | test_architecture_boundaries.py | TestV3PublicWireRetired | test_v4_roots_reference_no_retired_v3_symbol | 1110 | `assert offenders == []` | #28 | L0.4b |
| A-031 | test_architecture_boundaries.py | TestV1V2PublicWireRetired | test_public_v1_v2_wire_modules_are_absent_from_disk | 1128 | `assert present == []` | #29 | L0.4b |
| A-032 | test_architecture_boundaries.py | TestV1V2PublicWireRetired | test_public_v1_v2_wire_package_directory_is_gone | 1134 | `assert not (PACKAGE_ROOT / "config" / "canonical").exists()` | #30 | L0.4b |
| A-033 | test_architecture_boundaries.py | TestV1V2PublicWireRetired | test_public_v1_v2_wire_modules_are_not_importable | 1147 | `assert still_present == []` | #31 | L0.4c |
| A-034 | test_architecture_boundaries.py | TestV1V2PublicWireRetired | test_no_production_module_imports_a_retired_v1_v2_module | 1159 | `assert offenders == []` | #32 | L0.4b |
| A-035 | test_architecture_boundaries.py | TestV1V2PublicWireRetired | test_no_v1_migration_kernel_is_retained | 1162 | `assert V1_MIGRATION_MODULES == ()` | #33 | L0.4b；常量钉定/policy 数据（保留） |
| A-036 | test_architecture_boundaries.py | TestV1V2PublicWireRetired | test_no_v1_migration_kernel_is_retained | 1163 | `assert V2_MIGRATION_MODULES == ("confflow.config.canonical.v2_adapter",)` | #33 | L0.4b；常量钉定/policy 数据（保留） |
| A-037 | test_architecture_boundaries.py | TestV1V2PublicWireRetired | test_no_v1_migration_kernel_is_retained | 1169 | `assert present == []` | #33 | L0.4b |
| A-038 | test_architecture_boundaries.py | TestV1V2PublicWireRetired | test_v4_roots_reference_no_retired_v1_v2_token | 1191 | `assert offenders == []` | #34 | L0.4b |
| A-039 | test_architecture_boundaries.py | TestV1V2PublicWireRetired | test_current_protocol_majors_are_not_swept_up | 1220 | `assert banned == []` | #35 | L0.4b；常量钉定/policy 数据（保留） |
| A-040 | test_architecture_boundaries.py | TestConsolidatedHelperAuthorities | test_no_local_atomic_helper_copies_regrow | 1244 | `assert offenders == []` | #36 | L0.4b |
| A-041 | test_architecture_boundaries.py | TestConsolidatedHelperAuthorities | test_job_name_sanitizer_is_not_redefined_in_programs | 1254 | `assert offenders == []` | #37 | L0.4b |
| A-042 | test_architecture_boundaries.py | TestConsolidatedHelperAuthorities | test_publish_consumers_share_the_single_authority | 1261 | `assert module.publish_bytes is fsatomic.publish_bytes, module_name` | #38 | L0.4c |
| A-043 | test_architecture_boundaries.py | TestConsolidatedHelperAuthorities | test_publish_consumers_share_the_single_authority | 1264 | `assert module.fsync_directory is fsatomic.fsync_directory, module_name` | #38 | L0.4c |
| A-044 | test_architecture_boundaries.py | TestConsolidatedHelperAuthorities | test_both_renderers_reexport_the_single_sanitizer | 1271 | `assert gaussian.sanitize_job_name is _naming.sanitize_job_name` | #39 | L0.4c |
| A-045 | test_architecture_boundaries.py | TestConsolidatedHelperAuthorities | test_both_renderers_reexport_the_single_sanitizer | 1272 | `assert orca.sanitize_job_name is _naming.sanitize_job_name` | #39 | L0.4c |
| A-046 | test_architecture_boundaries.py | TestProducerImportIsolation | test_producer_legacy_import_debt_is_empty | 1303 | `assert result.returncode == 0, result.stderr` | #40 | L0.4c |
| A-047 | test_architecture_boundaries.py | TestProducerImportIsolation | test_producer_legacy_import_debt_is_empty | 1311 | `assert observed == KNOWN_PRODUCER_LEGACY_IMPORTS, (
            "prod…` | #40 | L0.4c |
| A-048 | test_architecture_boundaries.py | TestProducerImportIsolation | test_producer_does_not_load_canonical_config_tree | 1338 | `assert result.returncode == 0, f"{entry}: {result.stderr}"` | #41 | L0.4c |
| A-049 | test_architecture_boundaries.py | TestProducerImportIsolation | test_producer_does_not_pull_legacy_runtime_packages | 1352 | `assert result.returncode == 0, result.stderr` | #42 | L0.4c |
| A-050 | test_architecture_boundaries.py | TestProducerImportIsolation | test_schema_authority_is_single_source_for_every_consumer | 1362 | `assert contract_schemas.CONFIGURATION_VALIDATION_SCHEMA == (
        …` | #43 | L0.4c |
| A-051 | test_architecture_boundaries.py | TestProducerImportIsolation | test_schema_authority_is_single_source_for_every_consumer | 1365 | `assert contract_schemas.EDITOR_MANIFEST_SCHEMA == "confflow.editor-ma…` | #43 | L0.4c |
| A-052 | test_architecture_boundaries.py | TestProducerImportIsolation | test_schema_authority_is_single_source_for_every_consumer | 1366 | `assert contract_schemas.RECIPE_CATALOG_SCHEMA == "confflow.recipe-cat…` | #43 | L0.4c |
| A-053 | test_architecture_boundaries.py | TestProducerImportIsolation | test_schema_authority_is_single_source_for_every_consumer | 1368 | `assert (
            producer_contract.CONFIGURATION_VALIDATION_SCHEM…` | #43 | L0.4c |
| A-054 | test_architecture_boundaries.py | TestProducerImportIsolation | test_schema_authority_is_single_source_for_every_consumer | 1372 | `assert (
            producer_validation.VALIDATION_RESPONSE_SCHEMA
 …` | #43 | L0.4c |
| A-055 | test_architecture_boundaries.py | TestProducerImportIsolation | test_schema_authority_is_single_source_for_every_consumer | 1376 | `assert producer_manifest.EDITOR_MANIFEST_SCHEMA is contract_schemas.E…` | #43 | L0.4c |
| A-056 | test_architecture_boundaries.py | TestProducerImportIsolation | test_schema_authority_is_single_source_for_every_consumer | 1377 | `assert producer_recipes.RECIPE_CATALOG_SCHEMA is contract_schemas.REC…` | #43 | L0.4c |
| A-057 | test_architecture_boundaries.py | TestProducerImportIsolation | test_config_package_has_no_v1_v2_wire_surface | 1401 | `assert contract_schemas.CONFIGURATION_VALIDATION_SCHEMA == (
        …` | #44 | L0.4c |
| A-058 | test_architecture_boundaries.py | TestFacadeLazyIsolation | test_core_package_stays_lazy | 1433 | `assert result.returncode == 0, result.stderr` | #45 | L0.4c |
| A-059 | test_architecture_boundaries.py | TestFacadeLazyIsolation | test_application_packages_stay_lazy | 1448 | `assert result.returncode == 0, result.stderr` | #46 | L0.4c |
| A-060 | test_architecture_boundaries.py | TestFacadeLazyIsolation | test_v4_runtime_entries_do_not_load_canonical_config | 1466 | `assert result.returncode == 0, f"{entry}: {result.stderr}"` | #47 | L0.4c |
| A-061 | test_architecture_boundaries.py | TestFacadeLazyIsolation | test_core_public_surface_still_importable | 1475 | `assert callable(get_atomic_number)` | #48 | L0.4c；保留性断言 |
| A-062 | test_architecture_boundaries.py | TestFacadeLazyIsolation | test_core_public_surface_still_importable | 1476 | `assert len(PERIODIC_SYMBOLS) > 0` | #48 | L0.4c；保留性断言 |
| A-063 | test_architecture_boundaries.py | TestFacadeLazyIsolation | test_core_public_surface_still_importable | 1477 | `assert isinstance(HARTREE_TO_KCALMOL, float)` | #48 | L0.4c；保留性断言 |
| A-064 | test_architecture_boundaries.py | TestFacadeLazyIsolation | test_application_public_surface_still_importable | 1490 | `assert ExecutionService is AppExecutionService` | #49 | L0.4c；保留性断言 |
| A-065 | test_architecture_boundaries.py | TestFacadeLazyIsolation | test_application_public_surface_still_importable | 1491 | `assert RunState.__name__ == "RunState"` | #49 | L0.4c；保留性断言 |
| A-066 | test_architecture_boundaries.py | TestFacadeLazyIsolation | test_application_public_surface_still_importable | 1492 | `assert RunPaths.__name__ == "RunPaths"` | #49 | L0.4c；保留性断言 |
| A-067 | test_architecture_boundaries.py | TestFacadeLazyIsolation | test_application_public_surface_still_importable | 1493 | `assert InMemoryExecutionRepository.__name__ == "InMemoryExecutionRepo…` | #49 | L0.4c；保留性断言 |
| A-068 | test_architecture_boundaries.py | TestFacadeLazyIsolation | test_application_public_surface_still_importable | 1494 | `assert SyntheticProducerExecutor.__name__ == "SyntheticProducerExecutor"` | #49 | L0.4c；保留性断言 |
| A-069 | test_architecture_boundaries.py | TestFacadeLazyIsolation | test_application_public_surface_still_importable | 1495 | `assert callable(build_workflow_service)` | #49 | L0.4c；保留性断言 |
| A-070 | test_architecture_boundaries.py | TestPackaging | test_packages_are_discoverable | 1522 | `assert expected in packages, expected` | #50 | L0.4b |
| A-071 | test_architecture_boundaries.py | TestPackaging | test_no_namespace_package_gaps | 1526 | `assert (path / "__init__.py").is_file(), path` | #51 | L0.4b |
| A-072 | test_architecture_boundaries.py | TestSchemaHasNoHiddenCleanup | test_schema_text_has_no_cleanup_vocabulary | 1537 | `assert forbidden not in text, forbidden` | #52 | L0.4c |
| A-073 | test_architecture_boundaries.py | TestSchemaHasNoHiddenCleanup | test_calculation_model_exposes_only_the_frozen_blocks | 1542 | `assert set(CalculationModel.model_fields) == {
            "program",…` | #53 | L0.4c |
| A-074 | test_architecture_boundaries.py | （模块级，参数化） | test_legacy_module_inventory_is_intentional | 1568 | `assert not _legacy_module_exists(module), f"{module} was removed and …` | #54 | L0.4b |
| A-075 | test_architecture_boundaries.py | （模块级，参数化） | test_legacy_module_inventory_is_intentional | 1572 | `assert _legacy_module_exists(module), module` | #54 | L0.4b；保留性断言 |
| A-076 | test_architecture_boundaries.py | TestV45ModuleCoverage | test_v45_modules_exist | 1611 | `assert base.with_suffix(".py").is_file(), module` | #55 | L0.4b |
| A-077 | test_architecture_boundaries.py | TestV45ModuleCoverage | test_v45_modules_are_covered_by_scan_roots | 1616 | `assert any(path == root or root in path.parents for root in _V45_SCAN…` | #56 | L0.4b |
| A-078 | test_architecture_boundaries.py | TestV45ModuleCoverage | test_v45_modules_face_no_legacy_imports | 1626 | `assert offenders == []` | #57 | L0.4b |
| A-079 | test_architecture_boundaries.py | TestNoTaskDispatch | test_scanner_flags_task_enum_dispatch | 1737 | `assert offenders, "scanner must flag `if task == TaskName.IRC`"` | #58 | L0.4b；元检查（扫描器自检） |
| A-080 | test_architecture_boundaries.py | TestNoTaskDispatch | test_scanner_flags_task_enum_dispatch | 1738 | `assert offenders[0][1] == "IRC"` | #58 | L0.4b；元检查（扫描器自检） |
| A-081 | test_architecture_boundaries.py | TestNoTaskDispatch | test_scanner_ignores_scientific_strings | 1741 | `assert _task_dispatch_offenders(_FIXTURE_SCIENTIFIC_STRINGS) == []` | #59 | L0.4b；元检查（扫描器自检） |
| A-082 | test_architecture_boundaries.py | TestNoTaskDispatch | test_no_task_enum_dispatch_in_tree | 1749 | `assert offenders == []` | #60 | L0.4b |
| A-083 | test_architecture_boundaries.py | TestNoFilenameOrdinalPairing | test_scanners_flag_pairing_idioms | 1851 | `assert _filename_pairing_offenders(_FIXTURE_FILENAME_PAIRING), "basen…` | #61 | L0.4b；元检查（扫描器自检） |
| A-084 | test_architecture_boundaries.py | TestNoFilenameOrdinalPairing | test_scanners_flag_pairing_idioms | 1852 | `assert _range_ordinal_pairing_offenders(
            _FIXTURE_ORDINAL…` | #61 | L0.4b；元检查（扫描器自检） |
| A-085 | test_architecture_boundaries.py | TestNoFilenameOrdinalPairing | test_no_filename_idioms_in_tree | 1862 | `assert offenders == []` | #62 | L0.4b |
| A-086 | test_architecture_boundaries.py | TestNoFilenameOrdinalPairing | test_no_range_ordinal_pairing_in_tree | 1875 | `assert offenders == []` | #63 | L0.4b |
| A-087 | test_architecture_boundaries.py | TestNoFilenameOrdinalPairing | test_ordinal_within_item_usage_is_confined | 1886 | `assert offenders == []` | #64 | L0.4b |
| A-088 | test_architecture_boundaries.py | TestV45Packaging | test_module_imports_without_legacy | 1912 | `assert result.returncode == 0, result.stderr` | #65 | L0.4c |
| A-089 | test_v42_debt.py | TestV42LegacySymbols | test_no_forbidden_symbols | 153 | `assert offenders == []` | #66 | L0.4b |
| A-090 | test_v42_debt.py | TestV42LegacyTokens | test_no_forbidden_tokens | 167 | `assert offenders == []` | #67 | L0.4b |
| A-091 | test_v42_debt.py | TestProgramsImports | test_programs_import_boundary | 182 | `assert offenders == []` | #68 | L0.4b |
| A-092 | test_v42_debt.py | TestProgramsImports | test_no_legacy_package_imports | 190 | `assert offenders == []` | #69 | L0.4b |
| A-093 | test_v46_debt.py | TestV46BoundaryReuse | test_new_symbols_are_disjoint_from_existing | 207 | `assert overlap == set()` | #70 | L0.4b；常量钉定/policy 数据（保留） |
| A-094 | test_v46_debt.py | TestV46BoundaryReuse | test_existing_symbols_still_absent_from_extended_roots | 217 | `assert offenders == []` | #71 | L0.4b |
| A-095 | test_v46_debt.py | TestV46BoundaryReuse | test_existing_legacy_modules_still_unimported | 226 | `assert offenders == []` | #72 | L0.4b |
| A-096 | test_v46_debt.py | TestV46NewSymbols | test_no_iprog_symbol | 240 | `assert offenders == []` | #73 | L0.4b |
| A-097 | test_v46_debt.py | TestV46NewSymbols | test_no_numeric_program_dispatch | 248 | `assert offenders == []` | #74 | L0.4b |
| A-098 | test_v46_debt.py | TestV46ProducerLegacyTruth | test_no_legacy_truth_tokens_as_code | 262 | `assert offenders == []` | #75 | L0.4b |
| A-099 | test_v46_debt.py | TestV46NoOldRuntimeFallback | test_no_engine_import_in_strict_roots | 275 | `assert offenders == []` | #76 | L0.4b |
| A-100 | test_v46_debt.py | TestV46NoOldRuntimeFallback | test_engine_imports_pinned_to_legacy_entrypoints | 286 | `assert found == set(ENGINE_IMPORT_ALLOWLIST), found` | #77 | L0.4b |
| A-101 | test_v46_debt.py | TestV46NoFilenameOrdinalPairing | test_no_filename_idioms | 302 | `assert offenders == []` | #78 | L0.4b |
| A-102 | test_v46_debt.py | TestV46NoFilenameOrdinalPairing | test_no_range_ordinal_pairing | 315 | `assert offenders == []` | #79 | L0.4b |
| A-103 | test_v46_debt.py | TestV46NoFilenameOrdinalPairing | test_ordinal_within_item_usage_is_confined | 326 | `assert offenders == []` | #80 | L0.4b |
| A-104 | test_v46_debt.py | TestV46JobdeskDoublesHaveNoConfflowImports | test_doubles_are_confflow_free | 371 | `assert scanned >= 1, "expected at least the ConfFlow cross-repo file …` | #81 | L0.4b |
| A-105 | test_v46_debt.py | TestV46JobdeskDoublesHaveNoConfflowImports | test_doubles_are_confflow_free | 372 | `assert offenders == []` | #81 | L0.4b |
| A-106 | test_v46_debt.py | TestV46E2EJobdeskDoublesHaveNoConfflowImports | test_e2e_doubles_are_confflow_free | 419 | `assert scanned >= 1, "expected the workstream-E cross-repo E2E file t…` | #82 | L0.4b |
| A-107 | test_v46_debt.py | TestV46E2EJobdeskDoublesHaveNoConfflowImports | test_e2e_doubles_are_confflow_free | 420 | `assert offenders == []` | #82 | L0.4b |
| A-108 | test_v46_debt.py | TestV46NewSymbolsInApplicationCli | test_no_iprog_symbol_in_application_cli | 448 | `assert offenders == []` | #83 | L0.4b |
| A-109 | test_v46_debt.py | TestV46NewSymbolsInApplicationCli | test_no_numeric_program_dispatch_in_application_cli | 455 | `assert offenders == []` | #84 | L0.4b |
| A-110 | test_v46_debt.py | TestV46ModuleCoverage | test_v46_modules_exist | 466 | `assert path.is_file() or is_pkg, module` | #85 | L0.4b |
| A-111 | test_v46_debt.py | TestV46ModuleCoverage | test_v46_modules_are_covered_by_scan_roots | 474 | `assert any(path == root or root in path.parents for root in roots), m…` | #86 | L0.4b |
| A-112 | test_v46_debt.py | TestV46ModuleCoverage | test_v46_modules_face_no_exact_legacy_imports | 486 | `assert offenders == []` | #87 | L0.4b |
| A-113 | test_architecture_boundaries.py | TestProducerImportIsolation | test_config_package_has_no_v1_v2_wire_surface | 1396 | `with pytest.raises(AttributeError): getattr(config, name)`（循环 5 名） | #44 | L0.4c；pytest.raises 位点（根索引 112 条之外补记，主 gate 断言位点共 89） |

## 附录 B（§4a）：scripts/v4_arch_scan.py PATTERNS 15 项逐条

扫描 scope：`SCOPE`(55-65) 九入口路径（v4cli.py、application/__init__+v4_entry+v4_run+execution、control.py、producer、remote、analysis）。统一豁免：tokenize+AST 剥离注释与 docstring（`_strip_comments_and_docstrings`）；无按模式的豁免表；`input_xyz` 控制服务字段显式不 flag（docstring 40-41 行记录）。

| 子编号 | 检查名 | 正则 | 匹配对象 | 重复/独有 | b/c |
|---|---|---|---|---|---|
| S-01 | legacy-TaskRunner | `\bTaskRunner\b` | 旧执行器符号 | 与 AST-SYM `FORBIDDEN_SYMBOLS`（#6/#11/#71、v42 #66）同词重复（文本视角） | L0.4b |
| S-02 | legacy-CalcStepRunner | `\bCalcStepRunner\b` | 旧计算步执行器 | 同上重复 | L0.4b |
| S-03 | legacy-ResultsDB | `\bResultsDB\b` | 旧结果数据库门面 | 同上重复 | L0.4b |
| S-04 | legacy-WorkflowState | `\bWorkflowState\w*\b` | 旧工作流状态类（V1/V2/V3） | 较 `FORBIDDEN_SYMBOLS` 三个枚举项更宽的正则 | L0.4b |
| S-05 | legacy-iprog | `\biprog\b` | 旧整数程序派发 id | 与 v46 #73/#83（AST 符号）同词重复（文本视角） | L0.4b |
| S-06 | legacy-itask | `\bitask\b\|\bget_itask\b` | 旧任务枚举访问 | 与 `FORBIDDEN_SYMBOLS` 两项重复 | L0.4b |
| S-07 | legacy-chk-from-step | `\bchk_from_step\b` | 旧 checkpoint 词汇 | 与 #9/#67 重复 | L0.4b |
| S-08 | legacy-result-xyz | `result\.xyz\|failed\.xyz\|\boutput_xyz\b` | 旧结果文件契约（含点号） | 与 #9/#12/#14/#75 重复 | L0.4b |
| S-09 | legacy-output-path | `\boutput_path\b` | 旧输出路径契约 | 与 #7/#8/#9/#12/#67/#75 重复 | L0.4b |
| S-10 | legacy-orca-fallback | `(?i)default[_\s-]*orca\|orca[_\s-]*default` | ORCA 默认后回退语义 | **scanner 独有**（U9：全部保留） | L0.4b |
| S-11 | legacy-first-match | `first-match\|first_match` | 首个匹配派发语义 | **scanner 独有**（U9 保留） | L0.4b |
| S-12 | legacy-fake-marker | `FAKE_MODE\|fake_native\|native_marker\|CONFFLOW_FAKE` | 假后端/夹具标记 | **scanner 独有**（U9 保留） | L0.4b |
| S-13 | legacy-stepresult-shortcut | `\bStepResult\s*\(` | 旧 StepResult 快捷构造 | **scanner 独有**（U9 保留） | L0.4b |
| S-14 | legacy-duplicate-authority | `register_executor\|register_program\|build_default_registry\|\bExecutionRegistry\s*\(` | 重复执行器权威注册 | **scanner 独有**（U9 保留） | L0.4b |
| S-15 | legacy-silent-fallback | `silent.*fallback\|fallback.*silent\|\bor\s+["']orca["']` | 静默回退到 orca | **scanner 独有**（U9 保留） | L0.4b |

三份禁导入前缀表（#4/#10/#88）**按原 scope 分别保留**，不合并为更松或更严的全局表（U3 裁决遵循）。

## 待确认清单（修订）

- **U2（重复表收敛优先级）**：RETIRED_* 三表双份维护（test + scripts）是否由 L0.4b 统一为单一权威数据模块？收敛时以哪份为准（建议以 test_architecture_boundaries.py 为准，scanner 差异项单独裁决）。
- **U3（范围差异保持）**：三份禁导入前缀表与两份 V1/V2 token 表的差异**按原 scope 分别保留**，不合并为更松或更严的全局表（已按根裁决写入附录 B）。
- **U4（跨仓绝对路径）**：`test_v46_debt.py:97` 硬编码 `/opt/jobdesk-v2-v4/...`——**现行为保留**（文件缺失跳过但要求至少扫描一个文件），不改成可选跳过；仅登记事实。
- **U5（全文 vs 代码文本）**：#34/#36 用 TXT-RAW（含 docstring/注释）——**保留明确全文属性**，不因 AST 默认排注释而悄悄削弱；scanner 侧 tokenize 剥离口径同样保留。是否新增 TXT-CODE 版平行规则由 L0.4b 设计时提案。
- **U6（v42 gate 去留）**：**不授权整文件退役**；只能在 L0.4d 逐条覆盖确认后决定（其 4 个独有 token 与更严 programs 边界 #68 在收敛时必须先行保序迁移）。
- **U7（v46 注释过时）**：`test_v46_debt.py:56-57` 注释"absent today"已过时（目录已存在、行为正确）。L0.4b/c 顺带修正与否，待定。
- **U8（metrics 口径）**：`V4_ROOTS` 四入口口径**不变**；变更须显式决策（已写入 #94）。
- **U9（scanner 独有模式）**：S-10~S-15 六项**全部保留**（已逐条登记附录 B）；是否补 pytest 级等价 gate 由 L0.4b/c 提案。

### 事实记录（非待裁决）

- **F1**：两仓均无 `AGENTS.md`（git 与磁盘皆无）——仅记录事实，不作为阻碍或待裁决项。

## L0.4a 机械定位与组合注意（本次新增）

- **机械定位说明**：本清单由 L0 准备任务 B 的登记件按上述状态/基准修订而成；六个来源文件在 `ce0dd996` 与 `db022bab` 间逐字节一致（sha256 实测），故全部行号与位点无需换算，直接有效。
- **与 L0.7 的组合注意**：L0.7（测试按行为重组）若搬走共享扫描 helper/常量（例如主 gate 的 `FORBIDDEN_*` 表、scanner 的 `PATTERNS`），**最终集成时须以 `SOURCE-ANCHOR-MAP` 重新定位**相关位点后再核对清单；现在不猜新行号、不改 policy 设计。
- **切基点门槛**：L0.4b/c 在切换基点（或 L0.7 落地后）执行前，仍需根确认清单与基点的完整对应（重新走一遍位点对账），不得默认沿用。

# tools/_onetime · 一次性变更脚本（存档，不要重跑）

本目录存放**已经执行完毕的一次性脚本**。它们记录了本次改造每一步是怎么改的，
以及当时踩过的坑，属于可追溯的变更史料。

> ⚠️ **不要重新执行**：这些脚本按「改前字符串 → 改后字符串」做替换，
> 而目标文件早已变过，重跑会全部 `MISS` 或产生错误结果。
> 需要了解改动内容，请看 `CHANGELOG.md` 与 `docs/v2.1.1-前端细节修复说明.md`。

## 为什么保留而不是删除

1. **可追溯**：审计时能回答「某个字符串是被哪一步改掉的」；
2. **可复现**：同类批改可以照抄写法（尤其是「先备份再改」的骨架）；
3. **有教训**：这批脚本的存在本身说明了一个问题 ——
   在某个环境里编辑工具出现过「报成功但文件没变」，
   所以才需要 `tools/check_consistency.py` 这种逐项断言来兜底。

## 目录内容

| 文件 | 当时的用途 |
| --- | --- |
| `patch_ui.py` | 早期界面收尾（挂载角色栏、给按钮加 data-cap） |
| `patch_http.py` / `patch_render*.py` | 修验证脚本自身的缺陷（重定向跟随、探针命中脚本源码、手机端截图） |
| `patch_v201_backend.py` / `patch_v201_front1.py` | v2.0.1：新增 /api/twin、统计卡下钻、顶栏精简 |
| `patch_assistant_*.py` / `patch_pet_mount.py` / `fix_assistant1.py` | v2.1：助手接口接线、引导选择器修正、挂载到 6 个页面 |
| `fix_front1.py` / `fix_front2.py` | v2.1.1：顶栏换行回归、统计卡提示位置、助手不再主动弹窗 |
| `patch_version.py` | 消除各脚本里硬编码的版本号（改为从 app.__version__ 读） |
| `probe_http_login.py` / `probe_twin.py` | 一次性诊断探针（已被 audit_* 与 probe_topbar 取代） |

### 追加：被新工具取代的 v1.0 期脚本

| 文件 | 原用途 | 取代者 |
| --- | --- | --- |
| `verify_acceptance.py` | 把验收自检结果落盘 | `tools/run_selftest.py` |
| `capture_screens.py` | 无头截图 + 粗粒度 DOM 校验 | `tools/verify_render.py`（探针更严，且会剥掉脚本源码再断言） |
| `verify_startup.py` | 跑 `run.py` 确认服务能起 | `tools/audit_frontend.py`（真开页面并量几何） |

这三者功能与新工具重合，且写于 v1.0 时期（未考虑 v2.0 的登录态），保留仅作历史参考。

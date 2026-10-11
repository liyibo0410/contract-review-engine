# contract-review v1.0 · 版本冻结与归档说明

| 项目 | 内容 |
| --- | --- |
| 版本号 | **v1.0.0** |
| 版本状态 | **FROZEN（已冻结）** |
| 归档时间 | 2026-09-18 22:30（GMT+8） |
| 归档依据 | 第二次会议决议 D-13／D-17 |
| 冻结人 | 启明 |
| 源目录 | `D:\workbuddy work\2026-09-18-20-32-30\contract-review-1.0` |
| 归档目录 | `D:\workbuddy work\2026-09-18-22-26-26\03-版本归档\contract-review-v1.0` |
| 归档规模 | **78 个文件 / 5,666,539 字节（5.40 MB）** |
| 校验清单 | `MANIFEST-v1.0-sha256.txt`（逐文件 SHA256） |

---

## 一、冻结声明

自本说明签署之时起，`contract-review-v1.0` **进入冻结状态**：

1. **归档副本只读**：归档目录内的任何文件不再修改。后续所有变更一律进入 v2.0 开发线，不回写 v1.0。
2. **源目录继续可用**：源目录 `2026-09-18-20-32-30\contract-review-1.0` 保留原样，作为 v2.0 的开发起点；
   两者内容在冻结时刻**逐字节一致**（以 SHA256 清单为准）。
3. **校验方式**：如需确认归档副本未被篡改，重新计算 SHA256 并与 `MANIFEST-v1.0-sha256.txt` 比对即可：

   ```bash
   # Windows PowerShell 示例（逐行比对，或用任意哈希工具批量校验）
   Get-ChildItem -Recurse -File | Get-FileHash -Algorithm SHA256 | Format-Table Hash, Path
   ```

4. **线上实例对应关系**：当前线上 `http://39.96.200.45:8160/` 运行的就是本冻结版本（部署时间 2026-09-18 21:40）。

---

## 二、归档范围

### 2.1 已纳入归档（78 个文件）

| 分类 | 内容 | 文件数 |
| --- | --- | --- |
| 可运行程序 | `run.py`、`selftest.py`、`app/`（11 个模块） | 13 |
| 前端界面 | `ui/index.html`、`ui/workbench.html`、`ui/rules.html`、`ui/assets/app.css`、`ui/assets/app.js` | 5 |
| 模拟审批系统 | `mock/feishu.py`、`mock/__init__.py` | 2 |
| 规则与配置 | `config/app.json`、`samples.json`、`clause_types.json`、`clauses/clauses.json`、`rules/{purchase,sales,service,labor}.json` | 8 |
| 示例合同集 | `samples/`（DOCX / PDF / 扫描件 × 标准 / 风险 / 受阻） | 8 |
| 交付文档 | `README.md`（运行说明）、`PRD.md`、`SPEC.md`、`docs/`（交付物总览、验收对照表、演示脚本） | 6 |
| 需求与参考资产 | `docs/reference/`（需求基线、架构图、项目导航、老师标注、串讲稿、OCR 文本） | 9 |
| 测试截图 | `docs/screenshots/` | 5 |
| 构建与验证工具 | `tools/` | 9 |
| 部署脚本 | `deploy/deploy_to_server.py`、`deploy/verify_remote.py` | 2 |
| 验证报告 | `verify/`（自检、HTTP 冒烟、截图、启动、线上巡检、规模统计） | 11 |
| **合计** | | **78** |

### 2.2 明确排除项（不属交付物）

| 排除内容 | 原因 |
| --- | --- |
| `__pycache__/`（约 20 个 `.pyc`） | Python 字节码缓存，非源码，与解释器版本绑定 |
| `.idea/`（8 个 IDE 配置） | 个人 IDE 元数据，与项目无关 |
| `data/`（`review.db`、`uploads/`、`reports/`、`parsed/`） | 运行时数据；需求明确要求交付物「不含依赖、不含 venv」，运行时库同理不入档 |
| `verify/remote_deploy.log` | 部署过程临时日志 |
| `.venv/` | 本项目本就无虚拟环境（零依赖），仅其他工作区存在 |

> 排除项在源目录中完整保留，未被删除。

---

## 三、版本验证证据（冻结时刻快照）

| 验证项 | 覆盖 | 结果 | 证据文件 |
| --- | --- | --- | --- |
| 验收自检 | 33 项（真解析、真跑规则、真调接口、真核对锚点偏移） | **33 / 33 通过**（7,870 ms） | `verify/selftest.txt` / `selftest.json` |
| HTTP 全链路冒烟 | 5 页面 + 21 接口 + 幂等 / 去重 / 故障注入 / PDF 生成 | **45 / 45 通过** | `verify/smoke_http.txt` |
| 启动入口验证 | `python run.py` 真实启动并访问三页面 | **通过** | `verify/startup.txt` |
| 页面截图与渲染校验 | 5 个页面真实截图 + 渲染后 DOM 关键内容校验 | **5 / 5 通过** | `verify/screenshots.txt`、`docs/screenshots/` |
| 线上实例巡检 | 已部署实例健康状态 + 前端依赖的全部接口契约 | **11 / 11 通过** | `verify/remote_inspect.txt` |
| 规模统计 | 交付物与规则库规模 | 53 文件 / 9,785 行 / 1,362 KB | `verify/stats.txt` |

**需求 2.4.10 八条验收标准：8 / 8 满足**（逐条对照见 `docs/验收对照表.md`）。

---

## 四、线上部署状态

| 项 | 值 |
| --- | --- |
| 访问地址 | `http://39.96.200.45:8160/` |
| 运行环境 | Alibaba Cloud Linux 3 ｜ `/usr/bin/python3.11`（Python 3.11.13） |
| 代码目录 | `/opt/contract-review`（5.8 MB） |
| 托管方式 | systemd 服务 `contract-review`（`Restart=always` + 开机自启） |
| 日志 | `/var/log/contract-review.log` |
| 运行期依赖 | **零 pip 包**；OCR 复用系统 `tesseract 4.1.1 + chi_sim` |
| 已知降级 | 服务器无 chromium → 服务端直出 PDF 不可用，降级为返回排版 HTML 并唤起浏览器打印（另存 PDF 一致） |

---

## 五、随版本冻结的遗留事项（已全部裁决）

v1.0 中识别的 3 项遗留事项，已于第二次会议闭会后由奕波裁决完毕，**不再挂起**：

| # | 遗留事项 | 裁决结果 | 决议 |
| --- | --- | --- | --- |
| 1 | `验收标准.png` 内容实为 2.11 智能问询笔录平台的验收标准，与 2.4 不匹配 | 确认为**截图取错页**；**本项目验收一律按需求基线 2.4.10 的 8 条执行**，不另行细化 | **D-24** |
| 2 | 提交文件夹命名 | 本系统为**李奕波个人作业（无组名）**；目录名定为 **`合同审批系统-李奕波`** | **D-25** |
| 3 | 服务器端 PDF 直出（chromium）未启用 | **暂不安装** chromium；保持现有降级方案（输出结果一致），改由 v2.0 优化项 **P1-03** 从代码侧解决 | **D-26** |

> 另：废弃的 uv/FastAPI 骨架工程已标注 `DEPRECATED.md`，**不物理删除**（决议 D-26）。

**本次全部产物与资产的投放位置**：`E:\桌面\合同审批系统-李奕波\`
（按「会议纪要 / 技术方案 / 演示与验收 / 版本归档 / 可运行系统 / 原始资产」六层组织，导航见该目录下 `00-交付总览.md`）

---

**归档完成时间**：2026-09-18
**冻结状态**：FROZEN
**下一步**：按《合同审批审查系统-技术方案-V2.0-20260918.md》进入 v2.0 迭代（里程碑 M7~M11）

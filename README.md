# AI Job Agent

一个以“**简历事实为唯一依据**”的安全求职助手。它把简历解析、岗位发现、混合匹配、申请队列、表单识别和投递记录放在同一个 Streamlit 应用中，并在登录、验证码、敏感问题或最终提交前把控制权交还给用户。

> 当前版本：`v0.2 MVP`。默认启用 Demo Mode，不需要 API Key，也不会向招聘网站提交任何资料。

## 产品流程

```text
上传 PDF / DOCX
        ↓
提取文字并生成 CandidateProfile
        ↓
读取国内企业 · 校园招聘 / 外企 / 国际企业 · 校招与初级岗位 / Demo / 指定企业官网与公共 ATS
        ↓
固定权重匹配 + 可选 AI 小幅复核
        ↓
选择岗位并加入 Application Queue
        ↓
识别表单、填写安全字段、处理暂停项
        ↓
Application Review
        ↓
用户在官网最终确认并提交 / Demo 模拟提交
        ↓
SQLite 保存投递记录并防止重复投递
```

首页只保留一条主路径：**上传简历 → 核对目标 → 找到岗位 → 一键投递并预填官网 → 本人补全并提交**。岗位来源按企业属性分为“国内企业 · 校园招聘”和“外企 / 国际企业 · 校招与初级岗位”；两个分区找到并选中的岗位都会进入同一个安全投递队列。原有 Greenhouse 搜索、JD 定制简历、投递追踪和面试日程保留在后面的高级标签中。

## 当前已实现

### 简历与候选人档案

- 上传并解析不超过 10 MB 的 PDF、DOCX 简历
- PDF 使用 PyMuPDF，DOCX 支持正文、表格、页眉页脚和文本框文字
- 使用 Pydantic 定义 `CandidateProfile`、教育、经历、项目、证书等奖项模型
- 无 API Key 时使用保守的本地规则提取姓名、联系方式、教育和技能
- 配置 OpenAI 后使用官方 Python SDK 和 Responses API 进行结构化提取
- AI 提取结果会再次与简历原文核对；缺少原文证据的字段会被丢弃
- GPA、身份信息、薪资、签证和工作许可等未知信息保持为空，不进行猜测
- 上传文件使用清理后的文件名和内容哈希保存在本机 `uploads/`

### 岗位发现与匹配

- Demo Mode 提供完全本地的候选人和 4 个固定岗位样例
- 首页可在 Demo Mode 中只读搜索真实公开岗位，投递动作仍保持本地模拟
- 受控目录包含 46 个经官方 API 验证的 Greenhouse board；首页国际分区默认检查其中 29 个国际职位来源
- 国际职位来源按企业属性保守分类：24 个归入“外企 / 国际企业”，5 个企业主体口径不清的来源保留为“未分类”，不会混入外企结果
- 新增 Lever、Ashby、SmartRecruiters 公共职位连接器，自动从职位板 URL 识别公司标识
- 支持用户指定企业官方招聘页：优先读取 `JobPosting` JSON-LD 和岗位卡片；JavaScript 页面回退为单页只读 DOM 渲染
- 内置小米、字节跳动、华为、腾讯、阿里、美团、京东、蔚来、小鹏、理想和西门子等官方入口快捷选择
- “国内企业 · 校园招聘”收录 54 家经核验的官方入口，默认并行尝试 24 家；阿里巴巴、蔚来、海尔、理想汽车、中兴通讯、TCL、小红书、中国电信已取得真实岗位结果，其余动态站按“尽力读取”或“官网直达”如实标注
- “外企 / 国际企业 · 校招与初级岗位”提供西门子、西门子医疗、博世、施耐德电气 4 家外企中国校招官网直达，并从上述国际职位来源中筛选校招、毕业生、实习和初级岗位
- “真实岗位工具 → 2. 选择岗位来源”支持打开 BOSS直聘/猎聘官方搜索页，并把用户本人复制的完整 JD，或保存的 JSON/HTML/TXT 文件本地解析为岗位；地点为必填，导入时会校验官方详情域名、去除追踪参数并按链接防重
- 应届生搜索默认隐藏明确资深或要求 3 年以上经验的岗位，同时保留校招页面中未在标题重复写“应届”的普通岗位
- 自建官网读取限定同源、最多 8 页/100 个岗位；遵守 robots/no-follow，登录、CAPTCHA、429 和网站限制会停止
- 支持“宽泛推荐”或严格岗位名称搜索，以及目标城市、中国大陆+香港+新加坡、不限城市三档地域范围
- 展示读取公司数、官网岗位池、进入匹配数、缓存数和单公司失败信息
- 结果页支持企业类型、目标岗位、就业地点、公司、岗位类型和最低匹配度筛选；企业类型按经审核的公司属性显示为“国内企业”“外企 / 国际企业”或“未分类”，最低匹配度只影响显示，不改变搜索来源
- 展示公司、岗位、地点、类型、完整 JD、优势、缺失技能和同类岗位
- Hybrid Matching 使用固定权重，不把评分完全交给大模型：

| 维度 | 权重 |
|---|---:|
| 技能 | 35% |
| 经历 | 25% |
| 专业 / 学历 | 15% |
| 项目 | 10% |
| 地点 | 5% |
| 岗位方向 | 10% |

如启用 OpenAI，AI 只能在规则基础分上做 `-5` 到 `+5` 的有限复核；API 不可用时自动回退为纯规则评分。

### 申请队列与记录

- 一次可选择最多 20 个岗位并加入申请队列
- 队列状态：`pending`、`opening`、`filling`、`waiting_user`、`ready_to_submit`、`submitted`、`failed`、`skipped`
- `jobs`、`applications` 和 `application_queue` 使用事务化 SQLite 持久化
- 按规范化岗位 URL 以及 `source + job_id` 双重防重
- 已提交岗位无法再次进入投递流程
- 保存安全的 Review JSON、待用户确认字段、错误原因、截图位置和重试次数
- 失败任务可以重试；达到重试上限后停止
- 首页展示岗位总数、推荐岗位、待投递、已投递、失败和待本人补充的数量
- Real Mode 点击“一键投递：自动打开官网并预填”后立即启动可见浏览器；多选岗位会按顺序逐个打开，完成当前官网并关闭窗口后再打开下一个
- 程序会连续跟随高置信度的申请入口，适配阿里“加入意向单 → 开始投递”等多步骤页面；登录后继续监听后续表单
- 自动填写姓名、联系方式、地点、学校、学历、专业、毕业时间、可明确映射的技能/教育/实习/项目摘要，并上传已选择的简历；已有值不会覆盖
- 只有页面实际发现的未知、敏感或无法安全识别字段才会保持空白并在队列中列出具体项目，不再把每个岗位笼统标为“需要人工处理”
- 国内企业与外企分区选中的岗位使用同一套 Application Queue、Review、人工暂停和防重复机制，不会因来源分区降低安全门槛
- 用户在官网亲自补全空白、核对并点击最终 Submit 后，回到 App 勾选确认并保存投递记录；程序不会把未观察到的提交伪记为成功

### 表单理解与浏览器安全

- `FieldDetector` 综合使用 `label`、`placeholder`、`name`、`id`、`aria-label`、`aria-labelledby`、`autocomplete`、输入类型和附近表单标签判断字段
- 高置信度的公开字段才进入自动填写计划
- 姓名、邮箱、电话、地点、学校、学历、专业、毕业时间、简历、LinkedIn 和作品集属于公开字段白名单；只有简历中有值且页面映射足够可靠时才会填写
- 工作许可、签证、期望薪资、出生日期、身份证、性别等字段始终进入人工确认
- 自动识别 CAPTCHA、安全验证和登录页；存在阻塞时不会继续填写
- 日志与错误信息会移除邮箱、手机号、查询参数、简历路径等隐私
- Generic Adapter 会校验 HTTPS、公网域名、跨域跳转和危险 URL
- 可见浏览器只会点击精确识别的 `Apply / 立即申请 / 加入意向单 / 开始投递` 导航控件；`Submit Application / 提交申请` 明确排除
- 登录或 CAPTCHA 会在可见浏览器中暂停，用户处理完成后继续识别当前申请页
- 登录会话按招聘站点写入不含公司名的哈希目录，后续岗位可复用；程序不保存账号密码，该本地会话文件应像浏览器 Cookie 一样妥善保护
- Demo 表单由仅绑定 loopback 的本地 HTTP 服务提供，并用真实 Playwright Chromium 验证填写与提交门禁；不会访问或提交外部网站

### 现有 Greenhouse 高级工具

“真实岗位工具”标签保留了已经可用的单岗位 Greenhouse 流程：

- 搜索多个经过审核的 Greenhouse board token，或读取用户指定的 board
- 6 小时本地缓存、请求节流、单公司失败隔离和旧缓存回退
- 展示 JD、薪资判断、匹配理由及同类岗位
- 为外企或英文岗位选择英文简历
- 依据 JD 重排已有简历内容并生成单独 DOCX，不新增不存在的经历
- 在可见 Chromium 中填写姓名、邮箱、电话、地点、学校、学历、专业和毕业时间，并上传简历
- 登录或验证码由本人在可见官网处理；未知必填题保持空白，程序不会点击最终 Submit
- 记录卡点、提交证据、招聘阶段和后续面试日程

## 安全边界

本项目不是无人监管的海投机器人。MVP 有以下强制约束：

1. **不编造信息**：简历中没有的事实不会自动补全。
2. **真实站点不自动提交**：`AUTO_SUBMIT=false` 是默认值；真实网站的 Submit 不会被后台静默点击。
3. **Demo 只在本机模拟**：即使打开 Demo 的提交开关，目标也必须是 loopback 地址，并且必须先确认 Review。
4. **不绕过验证码**：CAPTCHA、滑块、短信、邮箱验证和二维码登录都必须由本人完成。
5. **敏感问题暂停**：身份、政治面貌、婚姻、工作许可、签证、薪资、竞业、出差和亲属任职等答案不猜测。
6. **不保存密码**：账号密码不写入代码、数据库或日志。
7. **不规避网站限制**：不绕过反爬、频控、登录墙或服务条款限制。
8. **不做骚扰式海投**：队列有明确上限，真实流程保持逐岗位检查和确认。

BOSS直聘和猎聘目前均限制第三方爬虫、拟人程序及规避技术措施，因此主流程不接管两站 Cookie、不隐藏 `navigator.webdriver`，也不后台实时抓取。用户可在官网正常浏览后，把本人正在查看的岗位资料导入本机；取得平台书面授权或官方 API 后，才会增加受授权的实时适配器。

## 产品截图

仓库暂不包含带真实个人资料的截图，避免误提交隐私。发布版本可按以下位置补充脱敏截图：

| 页面 | 建议文件位置 |
|---|---|
| AI Job Agent 首页与 Dashboard | `docs/screenshots/01-dashboard.png` |
| 简历解析与 CandidateProfile | `docs/screenshots/02-profile.png` |
| 岗位匹配列表 | `docs/screenshots/03-job-matches.png` |
| 投递队列与 Review | `docs/screenshots/04-application-queue.png` |
| 可见浏览器人工确认页 | `docs/screenshots/05-browser-review.png` |

截图必须使用 Demo 数据，并遮盖邮箱、电话、本机路径、查询参数和浏览器会话信息。

## 技术架构

```text
Streamlit UI (app.py + ai_job_agent/ui.py)
  ├─ Resume Parser ── PDF / DOCX
  ├─ Profile Builder ── Local Rules / OpenAI Responses API
  ├─ Job Searcher ── Demo / Greenhouse / Lever / Ashby / SmartRecruiters / official sites
  ├─ Job Matcher ── Fixed Weights + optional bounded AI review
  ├─ Application Service ── queue state machine / review / retry
  ├─ Browser Layer ── field detection / blocker detection / safe plan
  ├─ Source Layer ── ATS APIs / JSON-LD / bounded rendered DOM fallback
  ├─ Adapter Layer ── Generic form adapter + visible Playwright runner
  └─ SQLite ── jobs / applications / application_queue
```

主要技术：Python 3.11+、Streamlit、Pydantic 2、SQLite、OpenAI Python SDK、Playwright、PyMuPDF、python-docx、BeautifulSoup。

## 项目结构

```text
cxh简历自动投递程序/
├─ app.py                         # Streamlit 统一入口
├─ streamlit_app.py               # Community Cloud 安全入口
├─ access_control.py              # 局域网访问密码门禁
├─ scraper.py                     # BOSS/猎聘官方入口与用户提供 JD 的安全本地导入
├─ config.py                      # .env 配置与本地目录
├─ requirements.txt
├─ .env.example
├─ run.ps1                        # Windows 一键安装并启动
├─ run-lan.ps1                    # 可信局域网访问（0.0.0.0:8765）
├─ ai_job_agent/
│  ├─ ui.py                       # 简化版 AI Job Agent 首页
│  ├─ models.py                   # Pydantic 领域模型
│  ├─ database.py                 # SQLite、迁移、防重与队列状态机
│  ├─ demo_data.py                # 本地 Demo 档案与岗位
│  ├─ services/
│  │  ├─ resume_parser.py         # PDF / DOCX 文本提取
│  │  ├─ profile_builder.py       # 本地 / OpenAI 档案构建
│  │  ├─ job_searcher.py          # Demo / Greenhouse 岗位发现
│  │  ├─ official_source_searcher.py # 非 Greenhouse ATS / 企业官网编排
│  │  ├─ job_matcher.py           # Hybrid Matching
│  │  ├─ answer_generator.py      # 基于事实的开放问题回答
│  │  └─ application_service.py   # Application Queue 编排
│  ├─ browser/
│  │  ├─ field_detector.py        # 多信号字段识别
│  │  ├─ blockers.py              # CAPTCHA / 登录检测
│  │  ├─ planner.py               # 安全填写计划
│  │  ├─ agent.py                 # Playwright 打开、检查、填写与错误截图
│  │  ├─ filler.py                # 白名单字段执行器
│  │  ├─ review.py                # 最终提交门禁
│  │  ├─ redaction.py             # 日志脱敏
│  │  ├─ demo.py                  # 确定性 Demo Review
│  │  └─ demo_server.py           # loopback-only 测试招聘页
│  ├─ sources/                     # Lever / Ashby / SmartRecruiters / 官网发现
│  └─ adapters/
│     ├─ base.py                  # Adapter 抽象接口
│     └─ generic.py               # 通用 HTTPS 页面计划器
├─ job_assistant/                 # 已验证的 Greenhouse 高级工具
├─ examples/                      # 离线简历、条件和岗位数据
├─ tests/                         # pytest 测试
├─ data/                          # SQLite、缓存和本地资料
├─ uploads/                       # AI Job Agent 上传文件
├─ logs/                          # 脱敏错误与截图
├─ output/                        # 导出的投递清单
├─ docs/                          # 设计与外部项目审查
└─ vendor/JobHuntBot/             # 只读参考项目
```

## 环境要求

- Python 3.11 或更高版本
- Windows 10/11、macOS 或 Linux
- 安装依赖时需要网络
- 使用可见官网填写时需要 Playwright Chromium

扫描图片型 PDF 暂不包含 OCR，请先用可信工具完成 OCR 后再上传。

## 快速开始

### Windows 一键启动

在项目目录中运行：

```powershell
.\run.ps1
```

脚本会创建 `.venv`、安装 Python 依赖、安装 Chromium 并启动 Streamlit。

### 手动安装

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m streamlit run app.py
```

macOS / Linux：

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python -m playwright install chromium
cp .env.example .env
./.venv/bin/python -m streamlit run app.py
```

默认地址为 [http://localhost:8501](http://localhost:8501)。如果端口被占用，可增加 `--server.port 8765`。

## 环境变量

复制 `.env.example` 为 `.env`，不要提交真实 `.env`。

| 变量 | 默认值 | 作用 |
|---|---|---|
| `DEMO_MODE` | `true` | 使用本地示例岗位和模拟投递，不访问真实招聘站提交数据 |
| `AUTO_SUBMIT` | `false` | 安全默认；当前真实站点仍要求人工最终确认 |
| `CLOUD_DEPLOYMENT` | `false` | 云端安全开关；本机无需修改，云入口会强制开启 |
| `OPENAI_API_KEY` | 空 | 可选，只从环境变量读取；为空时使用本地解析和规则评分 |
| `OPENAI_MODEL` | `gpt-5-mini` | OpenAI 结构化提取、有限匹配复核和回答润色使用的模型 |
| `AI_JOB_AGENT_DB` | `data/ai_job_agent.db` | 新版 jobs / applications / queue 数据库 |
| `AI_JOB_AGENT_UPLOADS` | `uploads` | 简历上传目录 |
| `AI_JOB_AGENT_LOGS` | `logs` | 脱敏日志和失败截图目录 |
| `APP_ACCESS_PASSWORD` | 空 | 局域网访问密码；使用 `run-lan.ps1` 前必须设置至少 12 个字符 |

示例：

```dotenv
DEMO_MODE=true
AUTO_SUBMIT=false
CLOUD_DEPLOYMENT=false
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5-mini
AI_JOB_AGENT_DB=data/ai_job_agent.db
AI_JOB_AGENT_UPLOADS=uploads
AI_JOB_AGENT_LOGS=logs
APP_ACCESS_PASSWORD=
```

## 从手机或另一台电脑访问

应用包含真实简历、联系方式和投递记录，因此默认的 `run.ps1` 只监听本机
`127.0.0.1:8765`。只在可信家庭或办公局域网中使用以下方式：

1. 在 `.env` 设置至少 12 个字符的随机 `APP_ACCESS_PASSWORD`。
2. 运行 `run-lan.ps1`。
3. 将脚本显示的地址（例如 `http://192.168.10.143:8765`）输入同一 Wi-Fi 下的手机或电脑。
4. 输入访问密码。每个浏览器会话可随时从侧边栏“锁定当前页面”。

局域网模式保留 Streamlit 的 CORS 与 XSRF 防护，但仍是普通 HTTP。不要在公共 Wi-Fi 使用，
也不要在路由器中把 8765 端口映射到公网。如果需要从外网访问，应使用带 HTTPS 和身份策略的
私有网络或反向代理（例如 Tailscale 或 Cloudflare Access），不能直接暴露本端口。

## Streamlit Community Cloud

仓库提供独立入口 `streamlit_app.py`。它会强制启用 **Cloud Safe Mode**：支持上传并解析
简历、搜索公开岗位、计算匹配度、生成投递清单和打开官网，但不会启动 Playwright、控制
访问者电脑上的浏览器、上传简历到招聘网站或提交申请。本机 `app.py` 的可见浏览器预填
功能不受影响。

为了避免泄露简历和登录状态，建议使用 **私有 GitHub 仓库**。`.env`、SQLite、上传简历、
浏览器 Cookie、日志和缓存均已从 Git 提交范围排除。不要把这些文件手工上传到 GitHub。

部署步骤：

1. 将此目录的已跟踪文件推送到一个私有 GitHub 仓库。
2. 登录 [Streamlit Community Cloud](https://share.streamlit.io/)，选择该仓库和 `main` 分支。
3. Main file path 填写 `streamlit_app.py`，Python 选择 `3.12`。
4. 在 **Advanced settings → Secrets** 粘贴以下内容，并换成新的随机密码：

```toml
APP_ACCESS_PASSWORD = "replace-with-at-least-12-random-characters"
```

如需 AI 解析，可再由本人添加 `OPENAI_API_KEY = "..."`；不要把 Key 写进仓库。部署后得到
`https://你的应用名.streamlit.app` 地址。云端运行时生成的 SQLite、简历和清单不保证在
实例休眠或重建后保留，因此它目前只适合作为单用户、受密码保护的临时匹配界面，不应
作为长期投递记录库。

云端版与本机版的边界：

| 能力 | Cloud Safe Mode | 本机版 |
|---|---|---|
| PDF / DOCX 解析、岗位匹配 | 支持 | 支持 |
| 公开 ATS / 静态官网读取 | 支持 | 支持 |
| JavaScript 动态页面 Playwright 回退 | 禁用 | 支持 |
| 可见浏览器自动预填 | 禁用 | 支持 |
| 登录 / CAPTCHA 人工接管 | 不适用 | 支持 |
| SQLite / 上传文件持久保存 | 不保证 | 保存在本机 |

官方参考：[部署应用](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)、
[Secrets 管理](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)。

## Demo Mode

Demo Mode 是首次验收的推荐方式：

1. 保持 `.env` 中 `DEMO_MODE=true`、`AUTO_SUBMIT=false`、`OPENAI_API_KEY=`。
2. 启动应用并打开第一个“AI 求职 Agent”标签。
3. 点击“使用示例简历”。
4. 核对目标岗位和地点；选择“本地 Demo（4 个样例）”后点击加载按钮。
5. 勾选岗位并点击“一键准备投递”。
6. 检查申请队列，点击“确认模拟提交”。
7. 查看 Dashboard 和本机 SQLite 中更新后的记录。

这一流程会临时启动仅监听本机回环地址的 Demo 页面，并由真实 Playwright 填写；它不会启动外部招聘网站，也不会发送简历。若 Demo Mode 下仍配置了 `OPENAI_API_KEY`，上传真实简历时 AI 解析可能调用 OpenAI；需要完全本地体验时请保持 Key 为空。

## Real Mode

编辑 `.env`：

```dotenv
DEMO_MODE=false
AUTO_SUBMIT=false
OPENAI_API_KEY=你的_API_Key
```

重启应用后：

1. 上传真实 PDF / DOCX 简历并核对解析结果。
2. 补充目标岗位和就业地点。
3. 按目标选择“国内企业 · 校园招聘”或“外企 / 国际企业 · 校招与初级岗位”；也可选择“指定企业官网 / 其他 ATS”。
4. 检查 JD、匹配依据和风险，选择需要申请的岗位。
5. 点击“一键投递：自动打开官网并预填”。程序会立即打开第一个岗位的可见官网；如果选择了多个岗位，会在当前窗口关闭后按顺序打开下一个。
6. 在可见官网中本人完成登录或验证码，并填写程序保持为空的未知、低置信度或敏感问题；队列会列出实际需要补充的具体项目。
7. 检查全部字段及附件后，由本人点击最终 Submit；随后回到 App，勾选“我已在官网补全空白、核对并亲自提交”并保存投递记录。

`OPENAI_API_KEY` 不是运行 Real Mode 的硬性要求。Key 为空时，简历解析、评分和安全问答会使用本地规则；配置 Key 后，提取的简历文字或受限的岗位匹配上下文会发送给 OpenAI API。请根据自己的隐私要求决定是否启用。

## Playwright 与可见浏览器

安装 Chromium：

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

Demo Mode 会使用无界面 Chromium 访问临时 loopback 页面，以真实验证字段填写、验证码/登录暂停和只允许本地模拟提交的门禁。当前真实自动填写采用可见浏览器，而不是无界面后台提交。Greenhouse、Lever、Ashby、SmartRecruiters 和用户指定官网的可见流程只允许填写：

- 名 / 姓
- 邮箱
- 电话
- 当前地点
- 学校
- 学历
- 专业
- 毕业时间
- 已明确选择的 PDF / DOC / DOCX 简历

它不会勾选隐私协议，不会选择签证或工作资格答案，不会猜测未知自定义问题，也不会点击最终提交按钮。程序不知道答案或无法安全映射的字段会原样留空，并把实际字段列入对应队列项目。页面要求登录或出现 CAPTCHA 时会在可见官网中等待本人处理；完成验证后继续当前流程。最终 Submit 始终由本人在官网点击，提交结果也必须回到 App 勾选确认后才会保存。

Playwright 启动失败不会令整个 Streamlit 应用崩溃：任务会保留失败信息，可在修复浏览器组件或网站状态后重试。

## Adapter 架构与网站支持

| 能力 | 当前状态 |
|---|---|
| Demo 本地表单 | 已实现 loopback HTTP + Playwright 端到端验收完整队列与 Review 门禁 |
| Greenhouse 公共岗位发现 | 已实现；46 个受控 board，首页国际分区检查 29 个国际职位来源，并按保守企业属性过滤 |
| Greenhouse 基本资料与简历填写 | 已实现；最终提交由本人完成 |
| Lever 公共岗位发现 | 已实现官方 Postings API；可见浏览器预填 |
| Ashby 公共岗位发现 | 已实现官方 Job Postings API；可见浏览器预填 |
| SmartRecruiters 公共岗位发现 | 已实现官方 Posting API + 详情补全；可见浏览器预填 |
| 自建企业官网 | 已实现 JSON-LD、岗位卡片、同域有界抓取和单页动态 DOM fallback |
| 国内企业校园招聘 | 已收录 54 家核验入口；每轮最多并行尝试 24 家，8 家已取得真实岗位结果，其他渠道尽力读取或提供官网直达 |
| 外企 / 国际企业校招与初级岗位 | 提供 4 家外企中国校招官网直达；29 个国际职位来源中 24 个归入外企，另 5 个保守保留为未分类 |
| 小米 / 字节 / 蔚来等动态站 | 已提供官方入口与通用动态岗位读取；复杂登录/重复表单仍需逐站增强 |
| Siemens Avature | 官方岗位页可读取并进入通用预填流程 |
| Workday | 没有通用匿名官方 API；仅允许可见浏览器打开/填写，遇到限制即暂停 |
| BOSS直聘 / 猎聘 | 已实现官方搜索入口、复制 JD 与保存文件的本地解析、匹配和防重；未启用第三方实时爬虫 |

`BaseCareerAdapter` 负责定义 URL 校验、Apply 入口发现和表单计划接口。专用 Adapter 失败时，设计上应回退到 `GenericAdapter`，但回退不会降低安全门槛，也不会尝试绕过站点限制。

公共职位读取依据各厂商公开文档实现：[Lever Postings API](https://github.com/lever/postings-api/blob/master/README.md)、[Ashby Public Job Postings API](https://developers.ashbyhq.com/docs/public-job-posting-api)、[SmartRecruiters Posting API](https://developers.smartrecruiters.com/docs/endpoints)。这些接口仅用于读取已发布岗位；程序不会调用需要招聘企业密钥的候选人写入 API。

## OpenAI 使用说明

项目使用当前依赖范围内的官方 OpenAI Python SDK，通过 Responses API 的结构化输出完成可选能力：

- 将简历文字整理为 `CandidateProfile`
- 在固定权重得分上做最多 ±5 分的复核
- 在已有事实和草稿范围内润色开放问题回答

Key 只从 `OPENAI_API_KEY` 读取，代码和示例中不包含真实 Key。API、网络或模型返回异常时会安全回退到本地规则，不阻断 Demo Mode。

开放问题生成器不会回答简历中不存在的个人决定；无法回答时返回 `needs_user_confirmation=true`。该服务层已经可以独立测试，但不同招聘网站的真实开放题填写仍取决于对应 Adapter 和页面验证。

## 本地数据与隐私

默认会在本机产生：

- `data/ai_job_agent.db`：新版岗位、申请和队列
- `data/applications.db`：保留的 Greenhouse 高级工具记录
- `uploads/`：新版首页上传的简历
- `data/private_resumes/`：高级工具的中英文及岗位版简历
- `data/cache/greenhouse/`：公开岗位缓存
- `data/browser_profiles/`：按站点隔离的本机登录会话（不含账号密码）
- `logs/`：脱敏错误信息和失败截图
- `output/`：用户主动导出的 CSV / JSON

这些路径及 `.env` 已加入 `.gitignore`。不要把包含真实个人资料的数据库、简历、截图、浏览器上下文或导出文件提交到 Git。

## 运行测试

执行全部测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

只运行新版核心测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q `
  tests\test_ai_resume_parser.py `
  tests\test_ai_job_searcher.py `
  tests\test_ai_job_agent_database.py `
  tests\test_ai_job_agent_browser.py `
  tests\test_ai_job_agent_playwright_e2e.py
```

测试覆盖简历解析、Pydantic 数据模型、岗位发现、Hybrid Matching、字段映射、CAPTCHA / 登录检测、URL 安全、日志脱敏、SQLite 迁移、防重、队列状态转换、Review 门禁和失败重试。README 不固定测试数量；请以当前机器上实际运行的 pytest 输出为准。

## 高级命令行工具

离线生成匹配清单：

```powershell
.\.venv\Scripts\python.exe -m job_assistant.cli `
  --resume examples\resume.txt `
  --criteria examples\criteria.json `
  --jobs examples\jobs.json `
  --output output\application_plan.csv
```

读取指定 Greenhouse board：

```powershell
.\.venv\Scripts\python.exe -m job_assistant.cli `
  --resume examples\resume.txt `
  --criteria examples\criteria.json `
  --board https://job-boards.greenhouse.io/公司token `
  --output output\application_plan.csv
```

Greenhouse 的 board token 通常是官方职位页 URL 中代表公司的最后一段。程序不会猜测 token 或通过搜索引擎无限爬取公司站点。

## 常见问题

### 没有 OpenAI API Key 能运行吗？

可以。简历使用本地规则解析，匹配使用固定权重，Demo 岗位和模拟申请均可正常体验。

### 为什么扫描版 PDF 解析不到内容？

当前只提取 PDF 已有文本层，不内置 OCR。请先完成 OCR，或上传 DOCX 版本。

### 为什么某个字段没有自动填写？

常见原因是字段置信度不足、值不在简历中、字段属于敏感信息，或网站使用了尚未适配的自定义控件。系统会保留该官网字段为空，并在对应队列项目中列出需要你填写的具体内容，而不是猜测。

### 遇到验证码或登录怎么办？

自动流程会在当前可见官网中等待。请本人完成验证或登录，程序随后继续识别和填写当前申请页；项目不会尝试破解验证码。

### Windows 提示浏览器并行配置错误怎么办？

程序会先启动 Playwright 自带 Chromium；如果 Windows 因 SideBySide / `spawn UNKNOWN` 无法启动它，会自动改用本机 Microsoft Edge，填写范围和提交前人工确认规则保持不变。若两个浏览器都不可用，投递队列会显示失败发生的具体阶段，且不会提交申请。

### 为什么不能直接无人值守提交？

真实招聘表单可能包含法律声明、工作资格、签证、薪资和隐私授权。MVP 强制保留 Review 与最终人工确认，避免错误陈述和误投。

### 如何避免重复投递？

系统同时规范化岗位 URL，并记录来源与岗位 ID。相同岗位已存在时复用原队列；已提交后禁止再次创建申请。

### 为什么找不到某家公司？

请选择“指定企业官网 / 其他 ATS”。Lever、Ashby 和 SmartRecruiters 会自动识别；自研系统会读取用户指定的官方页面。JavaScript 重、要求登录或禁止自动读取的网站可能只能发现部分岗位，此时可粘贴具体岗位页并使用可见浏览器预填。

### 为什么 BOSS直聘 / 猎聘不是后台实时抓取？

两家平台的现行协议及 robots 规则限制第三方爬虫、拟人程序和规避技术措施。项目因此不加入隐藏 webdriver、模拟真人滚动或自动处理验证码。请在“真实岗位工具”的“2. 选择岗位来源”中打开官网，复制完整 JD，或上传本人保存的岗位详情文件；App 会完成匹配、排序、去重并保留原岗位页入口，这两类导入岗位当前不会启动自动填写。

### `AUTO_SUBMIT=true` 会自动提交真实岗位吗？

不会。当前真实站点提交门禁仍然拒绝后台自动提交；该变量只用于受控本地 Demo 的测试场景，并且仍要求显式确认 Review。

## 当前限制

- 真实岗位发现包括 54 家国内企业校招入口（每轮最多 24 家）、4 家外企中国校招直达、46 个受控 Greenhouse 来源、三类公共 ATS 和用户指定官网，但不是抓取全互联网的搜索引擎
- 企业类型来自经审核的公司目录，不根据岗位地点、语言或网址自动猜测；国际职位来源中仍有 5 个主体口径不清的来源显示为“未分类”
- Generic Adapter 与动态 DOM fallback 已支持常见多步骤表单，但仍不能保证所有企业自定义下拉、Shadow DOM 或 iframe 控件都能识别
- 新版首页已连接任意受支持来源的可见浏览器预填；最终提交仍由本人在官网完成
- 教育、工作经历和项目经历尚不能在所有真实招聘系统中自动逐项创建
- 开放问题生成服务已实现事实约束，但真实页面填写需要逐网站验证
- 不包含 OCR、验证码破解、账号密码托管或无人监管批量提交
- BOSS直聘/猎聘使用“官网浏览 + 本地导入”，不提供未获授权的实时自动抓取；一次最多导入 50 个岗位
- 网站 HTML 变化后可能需要更新 Adapter；失败会记录并允许重试

## Roadmap

1. 为理想、联想、OPPO、百度等公开职位接口增加经过验证的校招 Adapter
2. 增加字节、华为、小鹏等飞书招聘/自建站的专项只读发现层
3. 支持教育、经历、项目等重复表单块的可靠增删与校验
4. 在严格证据约束下接入开放题生成、长度控制和页面回填
5. 增加脱敏截图、可恢复 Browser Context 和更完整的错误诊断
6. 增加可选的人工审核式岗位版简历生成与版本对比
7. 扩展更多有官方 API、用户导出或明确授权的数据来源

## Boss / 猎聘历史实验助手

`boss_ai_assistant.py` 是保留的历史本地规则实验入口，不属于 AI Job Agent 主流程。当前只允许使用离线示例，不应再用于两家平台的实时页面；主流程请使用 `scraper.py` 提供的官网入口和用户资料本地导入。

先使用离线示例验收：

```powershell
.\.venv\Scripts\python.exe boss_ai_assistant.py `
  --platform boss `
  --offline-jobs examples\boss_jobs.json `
  --output-list output\boss_application_list.json
```

未指定 `--offline-jobs` 时程序会在启动浏览器前拒绝执行；`--enable-actions` 只保留为旧参数兼容，不会解除这一限制。

## JobHuntBot 参考项目

本机的上游项目副本保存在 `vendor/JobHuntBot/`，只作为岗位池、申请进度、卡点和日程设计参考；它不是本项目的抓取或自动投递引擎。`vendor/` 不会进入部署仓库，原项目审查记录见 `docs/JOBHUNTBOT_REVIEW.md`，第三方声明见 `THIRD_PARTY_NOTICES.md`。

如果本机已经另行下载该参考目录，可启动其只读本地看板：

```powershell
.\run-jobhuntbot-reference.ps1
```

不要向参考看板写入真实简历、个人资料或外部申请 URL。

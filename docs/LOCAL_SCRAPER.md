# 本地 Playwright + 云端网页

云端网页接收 PDF 简历和标准化岗位 JSON；本机 Chromium 负责读取你有权访问的招聘页面。Streamlit Cloud 不直接控制你的浏览器。本框架不投递、不填写申请表，不上传 Cookie，也不需要 OpenAI Key。

## 先跑可验证的本地 Demo

在项目目录运行：

```powershell
python -m pip install playwright
python -m playwright install chromium
python local_scraper.py --demo --headless --output output/demo-local-jobs.json
```

Demo 确实启动 Playwright Chromium，读取程序自建的 `127.0.0.1` 页面，只允许该临时服务器的请求；不访问真实招聘网站。成功输出 `Exported 1 job(s), 0 warning(s)`。JSON 中的 `careers.example.com` 是保留的示例域名，绝非真实岗位。然后在网页的本地岗位 JSON 导入入口上传此文件，参与匹配。重复运行请换输出文件名，脚本不会覆盖旧导出。

## 配置真实来源

复制 `examples/local_scraper_config.json`，将 `start_urls` 改为你明确指定、有权读取的公司岗位详情页或列表页，将 `allowed_hosts` 改成对应的精确主机名。示例域名本身没有真实岗位。

```powershell
python local_scraper.py --config examples/local_scraper_config.json --authorized --output output/my-local-jobs.json
```

`--authorized` 表示你已检查网站条款及自己的访问权限；它不会跳过任何技术限制。没有该参数时不会启动浏览器。默认可见 Chromium；只有 Demo/无人值守测试建议使用 `--headless`。脚本只依赖 Python 标准库和 Playwright，下载单个脚本即可运行；保存文件旁边会生成 `data/browser_profiles/local_scraper` 和 `output`。

- 优先读取页面里的 Schema.org `JobPosting` JSON-LD；否则使用可配置的 CSS selector 提取 `title/company/location/description/requirements`。不知道的地点保持 `null`，不会编造。
- 详情页无需 `job_links`。列表页设置 `job_links`（例如 `a.job-link`），终端展示最多 30 条链接，由你输入编号选择，再读取其 JD。不自动翻无限页，不点击 Apply。
- 默认最多读取 5 页、导出 5 个岗位，硬上限各 10，页面访问至少相隔 3 秒。网站要求更慢时需要自行提高 `delay_seconds`（最大 30 秒），超过上限应停止或使用授权 API。
- 出现登录/可识别验证码时保持同一浏览器页面，提示本人处理。完成后在终端输入 `continue`；回车则跳过。不会识别验证码答案、伪装 `navigator.webdriver` 或修改 UA 避让检测。
- 正常登录由你本人完成，登录状态只保存在该工具专用的本地 Chromium 目录，后续运行可复用；不读取日常 Chrome 用户目录，不调用 `storage_state` 或导出 Cookie。不要同步、上传或公开此目录。
- 每个导航先检查精确主机允许列表及公开 `robots.txt`，禁止的 URL、403、429、未知 robots 状态会停止/跳过。需要登录才能取得 robots 时同样停止。404/410 表示无 robots 文件，但仍需遵守网站条款。robots 重定向采取保守停止策略。
- 外部 CDN/SSO/验证域名默认被允许列表阻挡；只有你确认用途和权限后才能加入 `allowed_hosts`，不支持通配符。登录后如果无法可靠识别登录/验证码或页面结构，请手动结束，不强行继续。
- 不监听本地远程控制端口，不暴露 Chromium 调试接口，不提供从公网执行本机代码的接口。

## 文件交换格式

```json
{
  "schema_version": 1,
  "kind": "charles-job-export",
  "exported_at": "2026-09-03T00:00:00+00:00",
  "source": "local-playwright",
  "jobs": [{
    "title": "岗位名称",
    "company": "公司名称",
    "location": null,
    "description": "纯文本 JD",
    "job_url": "https://careers.example.com/jobs/example",
    "source": "local-playwright"
  }],
  "warnings": []
}
```

可选字段有 `requirements/job_type/department/publish_date`。导出白名单只含岗位信息，不包含完整 HTML、截图、个人简历、账号密码或 Cookie。URL 不允许凭证及常见身份验证查询参数。岗位唯一依据 URL 去重，单文件最大 2 MB。上传前建议查看 JSON，确认只含允许分享的招聘信息；登录专属/保密 JD 不应传至云端。

## 测试与局限

```powershell
python -m pytest tests/test_local_scraper.py -q
```

这是保守的选择器/JSON-LD 框架，不保证任意网站开箱即用。复杂 iframe、封闭 Shadow DOM、交互筛选、无限滚动需要后续适配；不包含 Boss直聘/猎聘专用反爬规避。CSS 变化只会导致当前页面无结果/被跳过，不会自动尝试扩大抓取范围。真实提交始终不属于此脚本功能。

官方参考：[持久化浏览器上下文](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context)、[请求路由](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-route)。

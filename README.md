# QRZ CloudLog Sync 同步器

一个运行在 **Windows** 上的图形化小工具，帮你把 **CloudLog（Wavelog）** 里的电台日志（QSO）**定时自动上传到 QRZ Logbook**。

> 一句话：**CloudLog 里的通联记录 → 自动同步到 QRZ**，多账号、多台站，全自动，还有日志和邮件回执。

---

## ✨ 它能做什么

- **一键同步**：把 CloudLog 里的 QSO 定时、自动传到 QRZ Logbook。
- **多账号 / 多台站**：一个 QRZ 账号可以挂多个呼号 / CloudLog 台站。
- **智能去重**：只上传新增的通联，不会重复传。
- **内置浏览器**：程序自带 Chromium，**不需要你另外装浏览器**，任何 Windows 机器都能跑。
- **自动勾选 QRZ 回执**：上传成功后自动勾选 QRZ 的"邮件通知"，QRZ 会给你发结果邮件。
- **运行日志 + 一键导出**：每次同步都有日志，可导出。
- **可选开机自启**，**无头模式**（无桌面服务器也能跑）。

---

## 🚀 快速开始（直接用 EXE）

如果你拿到了打包好的 `QRZCloudlogSync_vX.Y.Z.exe`，**双击就能用**，不需要装 Python。

1. **登录 QRZ**：左边选一个账号 → 点 **「手动登录 QRZ」**，在弹出的浏览器里登录一次你的 QRZ 账号（之后会记住登录，不再需要登录）。
2. **添加台站**：给账号点 **「新建台站」**，填三项：
   - 呼号：你的呼号（例如 `W1AW`）
   - CloudLog 地址：你的 CloudLog 实例地址（例如 `https://your-cloudlog.example.com/`）
   - API Key：你的 CloudLog **只读** API Key
3. 也可以点 **「检测 CloudLog 台站」**，填好地址和 Key 后会自动填出呼号和台站 ID。
4. 点 **「立即同步」** 或 **「启动定时」**。
5. 在 **「同步日志」** 页看结果；点 **「导出日志」** 可保存。

---

## 🔧 从源码打包成 EXE

需要 **Python 3.10+**（打包机装一次即可），在项目根目录双击：

```
build_exe.bat
```

脚本会自动：装 Python 依赖 → 下载内置 Chromium → 打包成 `dist\QRZCloudlogSync_vX.Y.Z.exe`。

> 版本递增：`build_exe.bat patch` / `minor` / `major`。

---

## ⚙️ 设置项说明

| 设置 | 说明 |
| --- | --- |
| 同步间隔(分钟) | 多久自动同步一次 |
| 一次上传全部 | 开=把新增全部合并成一个 ADIF 一次上传（推荐）；关=按每批条数分批 |
| CloudLog 默认地址 | 新建台站时预填的 CloudLog 地址 |
| 无头模式 | 勾上则浏览器不显示窗口（适合无桌面服务器） |
| 出错保留浏览器 | 出错时保留浏览器便于排查 |
| 开机自启动 | 注册到 Windows，开机自动后台同步 |

---

## 🧠 工作原理

1. 用你的 CloudLog **API Key** 从 CloudLog 拉取整本 ADIF（自动重试，应对服务器波动）。
2. 按呼号过滤 + 本地去重，只取**新增**的 QSO。
3. 用**内置 Chromium** 模拟点击 QRZ Logbook 的导入：`设置齿轮 → Import ADI File → 选文件 → 勾选邮件通知 → 导入`。
4. QRZ 后台处理，程序检测到"上传成功"弹窗后记录状态并关闭浏览器。
5. 每次结果写入运行日志。

数据流：`CloudLog (ADIF) → 程序 → QRZ Logbook`。

---

## 📁 数据都存哪

运行后相关数据保存在：

```
%APPDATA%\QRZCloudlogSync\
├─ config.json   # 配置（账号/台站/CloudLog地址/API Key等）
├─ logs\sync.log # 运行日志
├─ state\        # 各台站已上传的 QSO 指纹（去重用）
└─ profiles\     # 各账号的浏览器登录会话
```

> **凭据提示**：QRZ 密码、CloudLog API Key 会用系统消息加密（Windows DPAPI）存储。
> 若在某些特殊运行环境下加密不可用，会退化成可逆编码存储，**请勿把 `config.json` 发给他人或上传到公开仓库**。

---

## ❓ 常见问题

- **点了登录但浏览器停在主页 / 一脸懵？** 程序用内置 Chromium，首次运行请留意新窗口。QRZ 访问较慢（尤其在境外网络），给 10~60 秒。
- **CloudLog 报 522 / 连不上？** 522 是 Cloudflare"源站超时"，通常是你的 CloudLog 服务器暂时波动，程序会自动重试；稍后再试或确认 CloudLog 网站能打开。
- **上传时需要登录吗？** 第一次先「手动登录 QRZ」保存会话；之后上传自动复用，无需再登录。
- **邮件没收到？** 程序会自动勾选 QRZ 的 "Send me a report by e-mail"，由 **QRZ** 发邮件（收件人是你 QRZ 账号的邮箱），无需配置 SMTP。
- **Windows 弹"智能应用控制已阻止"？** 那是 Windows 对**未签名 EXE** 的安全提示。可关闭系统的"智能应用控制"，或部署到 Windows Server（一般不拦）。**这是 Windows 策略，不是程序问题。**
- **包体为什么这么大？** 因为**内置了整个 Chromium 浏览器**，让它可以在任何机器上跑、不用装浏览器。

---

## 📌 说明

- 需要你自己的 **QRZ 账号** 与 **CloudLog 实例 + 只读 API Key**。
- 本项目仅供个人/业余无线电日志同步使用，请遵守 QRZ 与 CloudLog 的服务条款。

Enjoy! 73 🏁

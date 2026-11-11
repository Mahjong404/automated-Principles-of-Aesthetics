# 《美学原理》课程自动化

一个基于 Python 和 Playwright 的超星学习通课程助手，内置《美学原理》题库。它可以复用 Edge 或 Chrome 的登录会话，从课程目录或已打开的章节接着运行。

## 功能

- 按章节处理视频和测验。视频支持静音、最高可用倍速、后台播放，以及同一页内多个视频的顺序播放。
- 优先用题库匹配测验题目。题库缺项时可配置大模型补答，考试答题页只暂存，留待人工核对。
- 开始前预览课程进度和待办事项。结束或停止时输出运行回执，Windows 下同时发送系统通知。
- 提交中断后，重新运行会先核对平台结果。结果不明时会停在当前章节等待核对，避免重复提交。

## 快速开始

需要 Python 3.10 或更新版本，以及 Microsoft Edge 或 Google Chrome。以下命令使用 PowerShell。

```powershell
git clone https://github.com/Mahjong404/automated-Principles-of-Aesthetics.git
cd automated-Principles-of-Aesthetics
python -m pip install -r requirements.txt
python main.py --run
```

若从学习通个人空间进入，在终端输入课程编号。若程序复用了已打开的课程页面，会从当前页面开始。需要登录时，可在终端输入学习通账号和密码，输入的密码不会回显。

## 启动方式

| 命令 | 用途 |
| --- | --- |
| `python main.py --run` | 优先复用已有浏览器会话。没有可用连接时启动独立会话。 |
| `python main.py --standalone --run` | 直接启动独立浏览器会话。 |
| `python main.py --login --run` | 在终端输入账号和密码，并在独立浏览器会话登录。 |

要复用日常浏览器，在 Edge 的 `edge://inspect/#remote-debugging` 或 Chrome 的 `chrome://inspect/#remote-debugging` 中启用远程调试。浏览器提示时允许连接。程序也会检查本机的 `9222` 和 `9223` 调试端口。

## 可选配置

首次创建本地配置时，将 [配置模板](data/account.json) 复制到 `real_data/account.json`。

```powershell
New-Item -ItemType Directory -Force real_data
Copy-Item data/account.json real_data/account.json
```

可填写 `phone` 和 `password`，供默认模式和独立模式在需要登录时尝试使用。留空时可按终端提示输入。Git 会忽略 `real_data/`，其中也保存提交恢复记录和平台已确认正确的模型答案。

### 大模型补答

在本地配置的 `llm` 对象中填写以下字段，即可为题库未匹配的题目启用模型补答。

| 字段 | 用途 |
| --- | --- |
| `base_url` | Chat Completions 可填服务商的基础地址或完整的 `/chat/completions` 端点。流式 Responses 填以 `/responses` 结尾的完整端点。保留服务商要求的路径前缀，例如 `/v1`。 |
| `api_key`、`model` | 服务商提供的 API 密钥和模型名称。 |
| `auto_commit` | 默认 `false`。有模型补答的章节测验会暂存并等待核对。设为 `true` 时，程序提交该测验并核对平台判分。已打开的考试答题页仍只暂存。 |
| `reasoning_effort` | 可选。仅在服务商和模型支持时设置，例如 `"high"`。留为 `null` 时采用服务商默认设置。 |

补答请求会将课程名、章节名、缺失题目和选项发送给配置的服务商。程序会校验模型返回的题号与选项。章节测验提交后，只有平台确认正确的模型答案才会保存到本地题库，供同一课程后续匹配。

### 混淆字体

如果测验页面使用内嵌混淆字体，在本项目根目录运行以下命令获取字体映射表。

```powershell
git clone https://github.com/TellMeYourWish/chaoxing_solution_of_font_confusion.git
```

程序从该目录读取 `HanSansCN_glyfHashedTables.pkl` 和 `HanSansCN_CmapTables.pkl`。

## 反馈与贡献

欢迎在 GitHub 报告问题或提交 Pull Request。报告运行问题时，可附上章节、复现步骤和终端的「故障诊断摘要」。 [提交 Issue](https://github.com/Mahjong404/automated-Principles-of-Aesthetics/issues) · [查看 Pull Requests](https://github.com/Mahjong404/automated-Principles-of-Aesthetics/pulls)

修改代码后，可运行离线测试。测试使用模拟课程页面和本地模型接口，覆盖题库匹配、答题、判分、暂存和提交恢复。

```powershell
python course_demo.py
```

## 鸣谢

感谢 **TellMeYourWish** 的超星学习通混淆字体解码项目提供正确的字体映射。 [chaoxing_solution_of_font_confusion](https://github.com/TellMeYourWish/chaoxing_solution_of_font_confusion)

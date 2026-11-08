# 《美学原理》课程自动化

复用 Edge 或 Chrome 中的学习通会话，或在独立窗口登录。按章节处理视频和测验，题库缺项可选用兼容 Chat Completions 的模型接口。

## 安装和启动

需要 Python 3.10+ 及 Edge 或 Chrome。

```powershell
python -m pip install -r requirements.txt
python main.py --run
```

| 启动命令 | 登录流程 |
| --- | --- |
| `python main.py --run` | 检查 Edge 或 Chrome 的本机 CDP，优先复用课程、考试或个人空间标签。没有合适标签时打开学习通个人空间检查登录态。 |
| `python main.py --standalone --run` | 新开独立浏览器窗口和会话，从个人空间开始检查登录态。 |
| `python main.py --login --run` | 先提示输入账号和密码，再在独立窗口登录。 |

默认和独立模式遇到未登录时，先尝试 `real_data/account.json` 的 `phone` 和 `password`。凭据缺失或登录失败时在终端提示输入，密码不回显。`--login` 使用本次输入的凭据。配置文件无需填写课程 URL。

要复用日常浏览器，在 `edge://inspect/#remote-debugging` 或 `chrome://inspect/#remote-debugging` 启用远程调试，并在浏览器提示时允许连接。程序检查浏览器的本地调试端口文件以及 9222、9223。无法连接时会新开独立窗口。

从个人空间启动时，在终端输入课程编号。已打开课程时从当前页面继续。视频支持静音、选择最高可用倍速、后台播放和按顺序处理同页多个播放器。运行完成或故障时通过 Windows 系统通知提示。

## 课程体检、回执与提交恢复

- 开跑前显示页面能读取的任务点进度、下一项和目录中的后续锁定项。当前已显示题目时，也会预览题库匹配数。体检只读页面和本地题库。
- 结束时在终端列出本次确认完成的操作、停止的位置和下次该从哪里继续。发生故障时，可复制最后一行「故障诊断摘要」，其中只列章节、阶段和原因。
- 提交中断后，重新运行会优先核对当前课程和账号的待确认记录。平台显示已完成时，继续读取结果；结果不明确时，停在原章等待人工确认，避免重复提交。恢复模型判分时，只有平台回顾页能核对到对应选择和正确结果才会写入题库。旧版防重记录只能在打开对应章节时核对平台是否已提交。

## 可选 LLM 配置

把 `data/account.json` 复制到 `real_data/account.json`。需要使用模型时，添加或填写 `llm` 对象。默认的 `real_data/` 目录由 Git 忽略。

```json
{
  "phone": "",
  "password": "",
  "llm": {
    "base_url": "",
    "api_key": "",
    "model": "",
    "auto_commit": false,
    "reasoning_effort": null
  }
}
```

按服务商文档填写 API 地址，保留其路径前缀。脚本不会自动补 `/v1`。

| 服务商提供的接口 | `base_url` 填法 |
| --- | --- |
| Chat Completions | 填服务商提供的基础地址，脚本追加 `/chat/completions`。也可直接填以 `/chat/completions` 结尾的完整地址。 |
| Responses | 填以 `/responses` 结尾的完整地址，脚本使用流式 Responses。CC Switch 中的 `wire_api = "responses"` 是单独的协议设置，复制其基础地址时还需补上 `/responses`。 |

例如，[DeepSeek 的 Chat Completions 文档](https://api-docs.deepseek.com/) 给出的基础地址是 `https://api.deepseek.com`，请求发往 `https://api.deepseek.com/chat/completions`。若其他服务商的文档写的是 `https://api.example.com/v1`，则保留其 `/v1`。

只有服务商和模型支持时，才将 `reasoning_effort` 设置为 `"high"` 等支持的值。留为 `null` 或省略时，脚本使用服务商默认设置。

- **题库优先**。题库无法匹配时才向配置的服务商发送课程名、章节名、缺失题目和选项。模型必须按指定格式返回单个 JSON 代码块。题号、选项范围和数量全部通过校验后才会填写。
- **章节测验**。使用了 LLM 且 `auto_commit` 为 `true` 时提交，根据学习通逐题判分核对。判对的模型答案按课程写入 `real_data/submissions.sqlite3` 的 `verified_answers` 表，之后可供同一课程匹配。判错或无法确认判分时停止并提示核对。`auto_commit` 为 `false` 时暂存并停在当前页。
- **考试**。在已打开且可识别的考试答题页，核对整页所有题后只暂存，随后停在当前页等待本人检查。无法识别某道题、选项状态或唯一暂存按钮时，会停止并提示人工处理。该流程使用本地模拟考试验证。

## 题库和离线验证

内置 `data/questions.json` 对应《美学原理》。遇到内嵌混淆字体的测验时，请在本项目根目录运行以下命令，从上游获取字体映射表。普通测验无需这一步。

```powershell
git clone https://github.com/TellMeYourWish/chaoxing_solution_of_font_confusion.git
```

脚本从 `chaoxing_solution_of_font_confusion/` 读取 `HanSansCN_glyfHashedTables.pkl` 和 `HanSansCN_CmapTables.pkl`。

```powershell
python course_demo.py
```

离线演示使用模拟课程页面和本机模型接口验证题库、答题、逐题判分写回、考试暂存、运行回执和提交恢复。

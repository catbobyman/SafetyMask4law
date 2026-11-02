# HaS 模型官方资料核查

调研日期：2026-11-01（用户时区）。本次只读核查模型元数据、官网、技术报告和公开 CLI 源码；没有安装依赖、下载模型权重或运行推理。本文的“已核实”表示公开资料或源码确有该内容，不代表本机效果已验证。

## 1. 模型身份与固定版本

| 项目 | 核实结果 |
|---|---|
| 仓库 | `xuanwulab/HaS_Text_0209_0.6B_Q8` |
| 发布者 | Hugging Face 组织名 Tencent Xuanwu Lab |
| 格式/量化 | GGUF / Q8_0 |
| 底座声明 | `Qwen/Qwen3-0.6B`，不是仅根据名称猜测 |
| 架构/参数量 | HF GGUF 元数据：`qwen3` / `596049920` |
| 权重文件 | `has_text_model.gguf` |
| 精确大小 | `639446592` 字节，约 639.45 MB / 609.82 MiB |
| 当前仓库 revision | `507d9cfa85bbd67401b043d45c9f36a18c6a2f93` |
| 权重 SHA-256 | `c530e55f22d233d9838558b701a41c1860d344957e813d013246c7f6b350e939` |
| 模型卡许可证 | `apache-2.0`；本仓库文件列表没有独立 LICENSE 文件 |
| 元数据上下文 | `32768`；不能与官方推荐运行参数 `-c 8192` 混为一谈 |

上述字段直接核对了 [Hugging Face 模型 API](https://huggingface.co/api/models/xuanwulab/HaS_Text_0209_0.6B_Q8?blobs=true)。固定版本下载地址为 [GGUF 权重](https://huggingface.co/xuanwulab/HaS_Text_0209_0.6B_Q8/resolve/507d9cfa85bbd67401b043d45c9f36a18c6a2f93/has_text_model.gguf)。本次未下载，因此校验和属于服务器声明，尚未执行本地比对。

## 2. 模型能力与适用边界

模型面向文本隐私任务：识别、无历史映射脱敏、带历史映射脱敏、映射提取、复合标签拆分和还原。模型卡列出八种语言，并推荐使用 llama.cpp；推荐配置内存约 1.56 GB。速度数据来自 Apple M4，不能当作 Windows / RTX 4060 的实测。模型卡要求沿用固定训练模板，而不是自由添加业务指令。[模型卡](https://huggingface.co/xuanwulab/HaS_Text_0209_0.6B_Q8)

官方技术报告承认：小模型处理复杂指代和嵌套实体存在局限，公私区分不稳定；同一文档的生成式 Hide 分块依赖上一块的映射，因此串行。报告中的跨语言还原由模型承担，同语种可以使用确定性替换。报告没有为本项目提供法律文书漏检率或误脱敏率结论。[官方技术报告](https://has.md/report)

这些边界支持本项目 PRD 的做法：首先只用 NER 取得候选，再由程序定位和执行规则。是否必须脱敏公司、支行、律所，是否保留金额、日期、法院等，应服从本地《规则.md》，不能委托模型的默认“公私区分”替代。

## 3. 官方 CLI 确实有公开源码，但名称与旧文档不同

[HaS 官网](https://has.md/)链接至 [XuanwuSkill 的 ClawHub 发布页](https://clawhub.ai/xuanwuskill/skills/has-anonymizer)。截至本次查询，发布 API 的 latest 为 `1.0.3`，版本 API 列出 32 个文件，含 Python 包、shell 入口和测试文件；该 skill 版本许可证声明为 MIT-0。它与模型仓库的 Apache-2.0 是不同对象。[版本及文件清单](https://clawhub.ai/api/v1/skills/has-anonymizer/versions/1.0.3)

不要把它推断成可 `pip install has-text` 的 PyPI 包。本次未核实这类 PyPI 安装入口。公开发布方式是 ClawHub skill，当前入口为 `scripts/has.sh text`；旧技术报告里的 `has-text`、`--types` 和模型能力 `Seek`，不等于当前 CLI 的用户命令。当前 CLI 使用 `scan`、`hide`、`restore`，类别由重复的 `--type` 提供。[当前发布说明](https://clawhub.ai/xuanwuskill/skills/has-anonymizer)

以下是安装后、在 Bash 环境使用的示例；`SKILL_DIR` 需要替换成实际安装目录。命令未在本机执行：

```bash
# 官网给出的安装入口；安装前应按所用 ClawHub 版本确认版本锁定选项。
npx clawhub install has-anonymizer

SKILL_DIR=/absolute/path/to/has-anonymizer
export HAS_TEXT_MODEL_PATH=/absolute/path/to/has_text_model.gguf

bash "$SKILL_DIR/scripts/has.sh" text scan \
  --type "人名" --type "公司" --type "电话号码" --file sample.txt

bash "$SKILL_DIR/scripts/has.sh" text hide \
  --type "人名" --type "公司" --file sample.txt \
  --output sample.anonymized.txt --mapping-output sample.mapping.json

bash "$SKILL_DIR/scripts/has.sh" text restore \
  --file sample.anonymized.txt --mapping sample.mapping.json \
  --output sample.restored.txt
```

CLI 的 `--file` 处理纯文本；DOCX、PDF 应先提取文字。不能把 DOCX/PDF 二进制直接交给这个入口，也不能据此承诺保留原文件版式。目录模式只处理直接子文件；被跳过的文件不是“检查通过”。这与本项目需要额外开发文档读取、定位和导出模块一致。[CLI 说明](https://clawhub.ai/xuanwuskill/skills/has-anonymizer)

## 4. 源码核查对 Windows 接入的影响

实际读取了以下发布源码，而非只依据市场简介：

- [has_text_entry.py](https://clawhub.ai/api/v1/skills/has-anonymizer/file?path=scripts%2Fhas_text_entry.py&version=1.0.3)：Python 至少 3.10，使用 `requests>=2.31.0`，由 uv 管理脚本依赖。
- [server_runtime.py](https://clawhub.ai/api/v1/skills/has-anonymizer/file?path=scripts%2Fhas_text%2Fserver_runtime.py&version=1.0.3)：按需启动 llama-server；每 slot 8192 token，最多四 slot；调用 `lsof`、`ps`，日志路径写为 `/tmp/llama-server-{port}.log`。
- [client.py](https://clawhub.ai/api/v1/skills/has-anonymizer/file?path=scripts%2Fhas_text%2Fclient.py&version=1.0.3)：请求 `/v1/chat/completions`，payload 默认只有 `messages`；检查 HTTP 错误和 `finish_reason == "length"`；用 `/tokenize` 计数。
- [prompts.py](https://clawhub.ai/api/v1/skills/has-anonymizer/file?path=scripts%2Fhas_text%2Fprompts.py&version=1.0.3)：NER 为一条 user 消息；类别 JSON 使用 `ensure_ascii=False`、紧凑分隔符；无额外 system prompt。Hide 为 user → assistant NER结果 → user 的两轮会话。

由源码推断，官方 CLI 带有 Unix 环境假设；本次没有证据支持“原生 PowerShell 安装后即可完整运行”。适合本项目的路线是直接运行 Windows llama-server，再由应用的 HTTP 客户端调用 NER。Bash/WSL CLI 可作为独立比较路线，仍需实测。

## 5. 待验证项

1. 固定 llama.cpp 构建、GPU 后端及本机驱动能否正常加载模型。
2. 固定模板在代表性法律文本上的 JSON 完整性、漏检、类型误判和候选边界。
3. 分块后实体位置对齐、公司简称合并与项目内统一代号，不应从“支持指代消解”宣传直接推导为正确。
4. 思考模式与采样参数：当前官方客户端源码未显式设置 `enable_thinking` 或 `temperature`；自行增加参数应记为工程选择并做效果验证。
5. 原生 DOCX/PDF 的文字替换、底层内容清理和版式输出属于本项目实现，不属于文本模型已具备的能力。

本笔记不更改 PRD 或规则，也不引入第二个模型。

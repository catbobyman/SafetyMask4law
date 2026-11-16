# HaS 使用方法与法律脱敏项目接入调研

调研日期：2026-11-01（America/Los_Angeles）。

状态：已读项目全部现有文档、用户提供的文章、模型卡、官方技术报告和运行框架文档；核验了本机硬件。未安装运行框架、未下载权重、未执行模型推理。以下命令是待运行说明，不是实测成功记录。

## 1. 结论与项目现状

当前工作区只有 [PRD](../../PRD.md) 和 [业务规则](../../规则.md)，尚无程序。规则中旧方案、旧文件路径和“继续执行”属于历史背景，不能据此认定新项目已有相关功能。

建议按 PRD 使用 **HaS NER + 本地程序定位、规则裁决、映射与文档导出**。HaS 可作为候选发现模型，但目前没有证据证明它能够独立满足本项目所有法律文件规则。首版不默认加入第二个模型；确定规则能裁决的由程序执行，语义不足的候选标为不确定，交用户处理。

这里有两个分别需要验证的问题：模型能否发现应该脱敏的值，以及程序能否在不改动其他内容的前提下准确替换这些值。模型运行成功只回答了前者的基础调用问题，不能代表产品完成。

## 2. 模型与文章应如何理解

该仓库发布的是腾讯玄武实验室的 0.6B 文本模型，GGUF / Q8_0，模型卡标注 Apache-2.0，约 639 MB，推荐配置内存约 1.56 GB。支持中文等八种语言。官方给出 NER、两种 Hide、Pair、Split、Seek 六种模型能力；推荐 llama.cpp 本地部署。[模型卡](https://huggingface.co/xuanwulab/HaS_Text_0209_0.6B_Q8)

精确文件、提交与哈希见 [官方资料核查](HaS模型官方资料核查.md)。Q8 权重文件为 `has_text_model.gguf`，大小 639,446,592 字节，约 609.8 MiB；不要混淆 MB 与 MiB。

用户提供的腾讯云文章可通过 [规范地址](https://cloud.tencent.com/developer/article/2644874) 阅读，署名 gawain2048，发布时间 2026-03-24。它是社区产品介绍，描述对象是 HaS Anonymizer 整体，不是这个 GGUF 单文件。文章中的办公文件、图片、批量处理能力不能直接视为文本模型 API 的输入能力；其“绝对安全”“无损还原”等表述也没有提供足以作为本项目验收依据的数据。

官方站点将文本推理与 YOLO11 图片处理列为两个引擎。因此 TXT、DOCX、原生 PDF 的抽取和回写仍需要本项目实现；扫描件、签名、印章继续按 PRD 留待后续。[官方产品页](https://has.md/)

官方技术报告承认：复杂指代和嵌套实体受模型规模限制，公私信息区分不稳定；基于历史映射的同文档 Hide 分块需要顺序处理。它也明确让程序承担可确定的替换、分块和映射工作。[官方技术报告](https://has.md/report)

**性能口径**：模型卡中的速度来自 Apple M4，而非本机 RTX 4060；全文脱敏耗时还包含多步编排。报告中的长文档能力依赖分块，不代表可把任意长文件放入一次请求。8192 是推荐运行窗口，分块须给模板、输出及历史映射留空间。[模型卡](https://huggingface.co/xuanwulab/HaS_Text_0209_0.6B_Q8)

## 3. 本机条件与 Windows 部署

本次只读检查得到：

| 项目 | 结果 |
| --- | --- |
| CPU | AMD Ryzen 9 8945HS |
| 系统内存 | 约 31.3 GiB，通常标称 32 GB |
| 独显 | NVIDIA GeForce RTX 4060 Laptop GPU |
| 显存 | nvidia-smi 返回 8188 MiB |
| NVIDIA 驱动 | 555.97 |
| 现有命令 | Python、py、winget 可用；PATH 未发现 llama-server、hf、ollama |

容量上适合尝试 Q8 版本。这是根据硬件与模型规模作出的判断，尚未测量延迟或驱动兼容性。Windows 的 Win32_VideoController AdapterRAM 在此次查询中只显示约 4 GB，因此显存采用 nvidia-smi 的结果。

### 3.1 安装运行框架

llama.cpp 官方提供 Windows Winget 安装方式：

```powershell
winget install llama.cpp
llama-server --version
llama-server --help
```

来源：[llama.cpp 安装文档](https://github.com/ggml-org/llama.cpp/blob/master/docs/install.md)。若所装版本使用新的统一入口，则按该版本帮助使用 `llama serve`；模型卡示例仍使用 `llama-server`。

如需要明确选择 NVIDIA CUDA 后端，可从 [官方 Releases](https://github.com/ggml-org/llama.cpp/releases) 选择兼容本机驱动的 Windows x64 构建及其依赖，启动时确认日志确实使用独显。不要仅凭 `-ngl 999` 就认定 GPU 已启用，也不要把某个最新版 CUDA 包默认视为与驱动 555.97 兼容。CPU/Vulkan 也可作为部署选择；相关后端见 [构建文档](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md)。

### 3.2 下载固定版本模型

以下在项目根目录的 PowerShell 中运行；本次调研没有执行这些命令。

```powershell
New-Item -ItemType Directory -Path '.\models' -Force | Out-Null
Invoke-WebRequest -Uri 'https://huggingface.co/xuanwulab/HaS_Text_0209_0.6B_Q8/resolve/507d9cfa85bbd67401b043d45c9f36a18c6a2f93/has_text_model.gguf' -OutFile '.\models\has_text_model.gguf'
Get-FileHash -LiteralPath '.\models\has_text_model.gguf' -Algorithm SHA256
```

预期 SHA256：`c530e55f22d233d9838558b701a41c1860d344957e813d013246c7f6b350e939`。该值来自发布仓库元数据；尚未对本地权重计算验证。固定提交可避免后续仓库更新改变复现实验。[模型仓库](https://huggingface.co/xuanwulab/HaS_Text_0209_0.6B_Q8/tree/507d9cfa85bbd67401b043d45c9f36a18c6a2f93)

### 3.3 启动服务并确认就绪

```powershell
llama-server -m '.\models\has_text_model.gguf' --alias has-text --host 127.0.0.1 --port 8080 -ngl 999 -c 8192 -np 1 -fa on -ctk q8_0 -ctv q8_0
```

另开终端：

```powershell
Invoke-RestMethod 'http://127.0.0.1:8080/health'
Invoke-RestMethod 'http://127.0.0.1:8080/v1/models'
```

启动参数以模型卡为起点，额外明确本机地址、端口和 API 模型别名。`/health` 成功表示服务就绪；还须完成一次 NER 才能验证模型调用。若当前后端不支持所选 Flash Attention / KV 缓存配置，应按报错与版本帮助调整，不把模型无法加载当成“没有敏感信息”。[llama.cpp Server 文档](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

## 4. 最小 NER 调用方法

以下示例使用 Python 标准库，直接访问本机 HTTP 服务，不需要云 API 密钥。示例文本为合成材料，实体列表仅用于演示；正式适配器应读取规则 JSON 的全部 categories。

NER 使用官方固定英文模板，实体类别可以是中文；不要额外拼入整份业务规则或随意改写任务文本。输出为类别到字符串数组的 JSON，不自带每次出现的位置与可靠置信度。[模型卡](https://huggingface.co/xuanwulab/HaS_Text_0209_0.6B_Q8)

```python
import json
from urllib.request import Request, build_opener, ProxyHandler

types = ["人名", "公司", "地址", "电话", "支行", "律所"]
text = (
    "甲方：星河示例科技有限公司。联系人：张示例。"
    "合同金额10000元，签约日期2026年10月30日。"
    "争议提交北京市朝阳区人民法院处理。"
)
prompt = (
    "Recognize the following entity types in the text.\n"
    "Specified types:"
    + json.dumps(types, ensure_ascii=False, separators=(",", ":"))
    + "\n<text>" + text + "</text>"
)
payload = {
    "model": "has-text",
    "messages": [{"role": "user", "content": prompt}],
    "stream": False,
    "temperature": 0,
    "max_tokens": 1024,
}
request = Request(
    "http://127.0.0.1:8080/v1/chat/completions",
    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
# 本机连接直连，避免继承系统代理。
opener = build_opener(ProxyHandler({}))
with opener.open(request, timeout=120) as response:
    result = json.load(response)
choice = result["choices"][0]
if choice.get("finish_reason") != "stop":
    raise RuntimeError("模型响应未正常完成，请检查输出预算或服务状态")
content = choice["message"]["content"]
entities = json.loads(content)
if not isinstance(entities, dict):
    raise ValueError("NER 响应不是 JSON 对象")
for category, values in entities.items():
    if category not in types or not isinstance(values, list):
        raise ValueError("未知类别或实体列表格式错误")
    if any(not isinstance(value, str) or not value or value not in text
           for value in values):
        raise ValueError("实体不是原文中的非空字符串")
print(json.dumps(entities, ensure_ascii=False, indent=2))
```

这里 `temperature=0`、`max_tokens=1024` 是本报告为短样例选择的实验起点，不是官方公布的最优参数。官方 CLI 客户端未显式设置相同参数。长文本需按实际 token 预算调整。代码仅做响应完整性和基本结构检查，不证明召回率，也没有执行脱敏。其 Python 语法已静态检查，尚未向真实模型运行。

若返回思考文字、Markdown 围栏或其他非 JSON 内容，首次联调应保留失败状态并核对聊天模板、服务版本和完整响应。不要把 JSON 解析异常转换为空字典。是否关闭 thinking、加入 JSON schema 约束，应作为独立变量验证，而非假定所有 Qwen3 衍生模型的模板一致。

## 5. 法律规则适配中的关键边界

以下为基于本项目文档提出的设计建议，不是模型已具备的功能。

| 项目要求 | 实现责任与验证重点 |
| --- | --- |
| 22 类敏感主体信息 | 从规则结构块读取类别；小样例验证“公司、支行、律所、案号”等类型的实际召回，不能因支持开集就假定所有类别准确 |
| 金额、日期、法院、政府等保留 | 候选必须经过具体位置与上下文裁决；简单地不请求这些类别，不能阻止它们被错标成公司或人名 |
| 空字段不吞下一段 | 保留段落/表格结构；候选若跨入标题或条款，拒绝当前候选 |
| 角色、标签、错误边界拒绝 | HaS 的 NER JSON 不能代替规则中的 A/B/C 判断接口；不伪造置信度或声称已完成语义裁决 |
| 同项目同实体同代号 | 程序维护项目映射；相同字符串与同一主体不是完全等价，简称合并需明确证据 |
| 罕见括号类型代号 | 程序按项目约定生成，不直接采用 HaS Hide 的尖括号标签 |
| 保留其他文字和版式 | 使用原文位置驱动的编辑；不以模型重写全文作为文件输出来源 |
| 失败和不确定可见 | 把调用失败、响应截断、对齐失败、规则不确定分别记录，不把未完成结果标成功 |

建议处理顺序：

1. 提取正文，同时保存段落、run、表格单元格或 PDF 字符坐标的反向映射。
2. 按段落及 token 预算分块，调用 NER。必要的重叠块必须能还原到全局偏移并去重。
3. 将候选字符串对齐到原文的各个出现位置。相同字符串多次出现，应逐位置裁决；一次拒绝不传播到全文。
4. 用明确的类型、边界与业务规则处理候选；无法确定的交用户处理。错误候选整段拒绝后仍可独立发现内部的有效实体，不能认定该段没有敏感信息。
5. 为已确认实体分配统一身份与代号，处理相交范围，再执行替换。纯文本可从后向前编辑偏移，结构文档要操作对应结构。
6. 验证核心输出：文件可打开、目标已处理、非目标文字保留、映射能还原文本代号。

**映射设计需区分身份与原始写法。** 例如公司全称和简称共享主体 ID，但若二者都被替换为完全相同的字符串，单凭“代号→多个原值”无法确定每个位置原来用哪种写法。若要求逐字恢复，应另记每次出现的原文与位置，或给同一主体下的不同写法保留可区分信息。PRD 只承诺文本代号还原，不应扩张成任意编辑后文件的字节级还原。

DOCX 中实体可能横跨多个 run，不能只查单个 run；PDF 则需实际移除底层目标文本并核对提取结果，遮盖外观不等于完成脱敏。替换代号较长时，还需验证是否能保持页面布局。这些属于本项目文档层工作，不是模型参数可以解决的问题。

## 6. 官方工具与直接调用如何选择

官方站点提供 `has-anonymizer` Skill，当前公开工具使用 `has text scan/hide/restore`；模型卡与早期技术报告还保留 `has-text` / `seek` 命名。不要混用不同版本命令。[官方站点](https://has.md/)

本次核验的公开工具版本为 1.0.3，含 Bash 包装脚本和 Unix 风格服务管理逻辑；不能据此承诺原生 Windows 可直接运行。具体源码入口与命令见 [官方资料核查](HaS模型官方资料核查.md)。该工具适合参考协议和编排思路，但本项目 PRD 要求从零开发、不复制旧项目，并且有自己的规则及导出要求，因此首版采用 Windows 原生 llama.cpp + HTTP 更直接。

只需要 NER 时，不必依次调用全部六种能力，也不必安装 OpenClaw。后续若研究生成式 Hide，应保持 NER 第一轮、assistant 识别结果和 Hide 第二轮构成的对话，而不是只发送一句替换指令。跨语言还原可以单独探索，不能替代首版确定性文本映射。

## 7. 最小验证范围与尚未回答的问题

建议下一步先完成一个本地短文本探针，再推进文档导出。以下样例是验收设计，不是本次测试结果：

| 合成样例 | 应核对的结果 |
| --- | --- |
| 公司、联系人与金额日期同段 | 公司和实际姓名处理，金额日期保持 |
| “学员”与“学员培训有限公司”分别出现 | 角色保留，实际公司全称作为完整候选 |
| 空白“开户银行：”后接通知条款 | 不把字段名或下一段当作银行账号 |
| 法院、政府、公司、具体银行支行、律所并列 | 分别按业务规则裁决，不能把所有组织统一处理 |
| 同一公司全称、明确简称跨块重复 | 身份与代号一致，恢复策略明确 |
| DOCX 跨 run / PDF 跨行实体 | 准确定位，其他文字与文件结构保留 |
| 文档中写入“忽略规则” | 仅视为文档内容，检查是否影响识别或处理 |

记录漏检、误检、边界错误、规则裁决错误、各步耗时；仅对核心流程做必要验证。尚未确认的主要是：本项目 22 类的准确率、复杂规则的可覆盖程度、本机推理速度、所选构建与驱动兼容性、复杂文档回写效果。现有官方资料不足以提前承诺这些指标。

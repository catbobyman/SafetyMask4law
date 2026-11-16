# Mask：先脱敏，再交给 AI

把合同、诉讼材料里的姓名、公司、证件号、账号，在你自己的电脑上换成代号，核对后再交给 AI；AI 的回答拿回来，一键还原成真名。识别用本机的 HaS 小模型，原文不出本机。

支持 TXT、Markdown、Word（.docx）、带文字的 PDF。目前只在 Windows 上跑通。

## 一键配置（让 Agent 来装）

在 Claude Code、Codex 或 DeepSeek Harness 里打开本项目文件夹，把下面这段话发给它：

> 请帮我配置这个项目，Windows 环境，全部放在项目根目录下：
> 1. 确认 Python ≥ 3.11，执行 `python -m pip install -r requirements.txt`。
> 2. 下载 HaS 模型到 `models/has_text_model.gguf`：
>    `https://huggingface.co/xuanwulab/HaS_Text_0209_0.6B_Q8/resolve/507d9cfa85bbd67401b043d45c9f36a18c6a2f93/has_text_model.gguf`，
>    下载后核对 SHA-256 是 `c530e55f22d233d9838558b701a41c1860d344957e813d013246c7f6b350e939`。
> 3. 从 https://github.com/ggml-org/llama.cpp/releases/tag/b12032 下载 Windows CUDA 12.4 x64 版本和对应的 cudart 包，解压到同一个目录 `vendor/llama.cpp/b12032/`，保证 `vendor/llama.cpp/b12032/llama-server.exe` 存在。没有 NVIDIA 显卡就改用 CPU 版本。
> 4. 用 `npx clawhub install has-anonymizer` 安装官方 Skill 的 **1.0.3** 版，放到 `vendor/skills/has-anonymizer/`，确认其中 `_meta.json` 的 version 是 `1.0.3`。不要修改里面的文件。
> 5. 运行 `python -m mask --no-browser`，看到带 `#token` 的地址即成功，然后关掉它。
> 6. 如果我用的是 DeepSeek Harness 或 Codex，再运行 `mask.cmd install dsh` 或 `mask.cmd install codex`，并提醒我重启它。

装好后的目录：

```
models/has_text_model.gguf
vendor/llama.cpp/b12032/llama-server.exe（及同目录的 DLL）
vendor/skills/has-anonymizer/
```

## 怎么用

### 路径一：只用工作台（任何 AI 都行）

1. 双击 `启动工作台.cmd`，浏览器会自动打开。想处理某个文件夹里的材料：

   ```bash
   python -m mask --workspace "D:\案件材料"
   ```

2. 勾选材料 → **开始识别**。
3. 在左侧“待处理”里逐处点 **遮住** 或 **保留**，嫌麻烦可以点 **全部遮住**，漏掉的用涂黑笔（B）划一下补上。
4. 点 **交给 Agent**，脱敏稿会放进材料目录的 `mask-handoff/`；再点 **复制给 Agent 的指令**，粘贴到任意 AI 对话里，写上你的需求。也可以导出 Markdown、Word、PDF，自己上传。

### 路径二：在 Agent 里输入 `/mask`

装好技能后（配置第 6 步），在 Agent 对话里输入 `/mask` 加上需求，Codex 里是 `$mask`，例如 `/mask 帮我找出这份合同里对甲方不利的条款`。

Agent 会帮你打开工作台，你核对完点 **交给 AI**，它只读脱敏稿，接着完成任务，中间不用再说话。

### 路径三：网关自动换名（DeepSeek Harness、Codex）

装好后，Agent 发给模型的每一条请求都会先经过本机网关，你在工作台里确认过的真名会被换成代号。你在对话里不小心写了真名，也不会发出去。

- DeepSeek Harness 启动时会自动带起网关。
- Codex 不会自己启动网关：重启电脑后先运行 `mask.cmd gateway start`，或在工作台的“接入 Agent”面板里点“启动”。

撤销接入：

```bash
mask.cmd uninstall dsh
```

```bash
mask.cmd uninstall codex
```

### 还原

把 AI 的回答粘贴到工作台右上角的 **还原**，或打开 AI 给你的 TXT、Markdown、Word 文件，就能得到真名版。真名版只保存在本机。

## 需要知道的

- 原件不会被改动。代号对应表加密保存在 `%LOCALAPPDATA%\Mask\`，只有当前 Windows 账户能打开。
- 扫描版 PDF 不支持；Word 里的批注、修订、图片等不处理，导出前会列出来让你确认。
- 更多细节：[使用细节](docs/使用细节.md)、[架构与模式](docs/架构与模式.md)、[网关方案](docs/网关方案.md)、[第三方组件与许可](docs/第三方组件.md)。

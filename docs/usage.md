# 使用指南

主线工作流面向中短篇小说，主要支持约 3 万字故事；首次使用从仓库的 `examples/short-story.yaml` 开始。

## 简报

复制示例并修改标题、读者、前提、核心期待及必须保留的事实。`length.target` 是规划目标，`min` 为最低篇幅，`max: null` 表示未声明硬上限。不要为省调用把目标篇幅或导语改短。

程序按非空白可见字符计数，含标点；投稿前再核对目标平台的统计方式和当前活动要求。

`fanqie_short` 渠道会加载对应编辑指南和题材研究。内置资料保留出处和适用范围，活动期限及投稿政策以当前公开页面为准。

## 模型

项目创建后，将 `examples/model.example.yaml` 复制为项目的 `config/model.yaml`，填写 `base_url` 和 `model`。密钥使用 `LLM_API_KEY` 环境变量。

`max_tokens` 控制单次输出额度，`max_prompt_tokens` 为本地估算的输入预算，不代表服务商实际支持的上下文。示例采用较大的输入预算以容纳长稿审读；使用前确认模型支持输入及输出之和，必要时按服务限制调整。长稿会按完整段落安排审读；容量不足应调整配置后续跑，避免删减正文。

## 审核大纲与续跑

`develop` 比较选题和开篇，`plan` 生成并预审剧情，随后可在项目目录查看策划文件。确认人物欲望、主要交锋、中段变化和结尾回报后执行 `generate`。

```bash
short-fiction-agent inspect --project ./local-runs/demo
short-fiction-agent generate --project ./local-runs/demo --resume
```

续跑只复用仍与当前输入和正文版本匹配的检查点。需要停止新增调用或明确追加额度时：

```bash
short-fiction-agent stop-story --project ./local-runs/demo
short-fiction-agent continue-story --project ./local-runs/demo --extra-calls 20 --one-repair
```

已确认的导语可用 `approve-lead` 锁定；正文变动后仍需检查事实与衔接。

## 投稿资料

正式导出检查当前审稿与发布准备。可先用自己的标题和简介执行检查：

```bash
short-fiction-agent prepare-publication --project ./local-runs/demo --input publication-input.json
short-fiction-agent export --project ./local-runs/demo --channel fanqie_short
```

输入文件包含 `channel`、`titles` 字符串数组、`synopsis` 和 `category`。标准导出会按当前模型和合同确定元数据；自定义准备记录只有与最终元数据一致时才能复用。`export --draft` 用于取出完整待修改稿。

导出不自动向平台投稿。作者核对标题、导语、全文、分类与原创要求后自行提交。

可选图文卡片导出需要中文字体。Ubuntu/Debian 可安装 `fonts-noto-cjk`；纯 TXT 投稿包不需要字体。

## 跨平台投稿

番茄使用 `fanqie_short`，知乎使用通用文本 `generic_text`，起点使用 `qidian`。先在简报 `channels` 中声明目标渠道，导出时选择同一渠道。更换目标平台时核对分类、导语、字数和原创要求，并重新检查相应投稿资料。知乎当前提供通用 TXT 导出。

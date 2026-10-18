# ShortFictionAgent · 中短篇小说创作 Agent

面向中文中短篇小说的创作工具，主要支持约 **3 万字**的完整故事，可生成用于番茄小说、知乎、起点等平台投稿的 TXT 文件。通过结构化简报、选题比较、大纲预审、逐章写作和全文审读，完成独立导语与完整正文。

支持男生情感、都市关系、家庭世情、校园、悬疑和幻想等题材；篇幅可按作者目标和投稿要求调整。

## 创作流程

```text
作者简报 → 选题与开篇比较 → 大纲预审 → 逐章写作与检查
         → 独立导语 → 全文主审与集中修订 → 发布准备 → TXT 投稿包
```

- 选题明确目标读者、核心期待、人物欲望和可兑现的故事卖点。
- 大纲提前安排主要交锋、关系转折、阻碍、回报和章节衔接。
- 正文围绕人物行动与对方接招推进，检查重复铺陈和缺少信息增量的段落。
- 导语从实际正文选材，交代必要身份、异常和变化，不设 200 字硬上限。
- 审读绑定具体正文版本和原文证据；正文变化后重新检查。
- 篇幅以约 3 万字为创作方向，可自行调整；目标值不会自动成为硬上限。

## 快速开始

需要 Python 3.10+，以及支持 OpenAI Chat Completions 格式的模型 API。

```bash
git clone https://github.com/jiaoyangli-shadow7day/ShortFictionAgent.git
cd ShortFictionAgent
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ./ShortFictionAgent

short-fiction-agent create --project ./local-runs/demo --brief examples/short-story.yaml
cp examples/model.example.yaml ./local-runs/demo/config/model.yaml
```

编辑项目中的 `config/model.yaml`，填写服务地址和模型名；通过环境变量提供密钥：

```bash
export LLM_API_KEY='你的 API 密钥'

short-fiction-agent develop --project ./local-runs/demo
short-fiction-agent plan --project ./local-runs/demo
```

先查看项目中的选题和剧情方案，再开始全文写作：

```bash
short-fiction-agent generate --project ./local-runs/demo
short-fiction-agent inspect --project ./local-runs/demo
short-fiction-agent export --project ./local-runs/demo --channel fanqie_short
```

正式导出要求当前稿件的审读及发布准备通过，命令会输出投稿包目录。`manuscript.txt` 包含独立导语和全文，`submission_info.txt` 为投稿信息；标题、简介和检查记录分别保存。需查看未完成稿时，加 `export --draft`。

字数校验按非空白可见字符计算，包含标点，与平台后台的汉字统计可能不同。模型服务的上下文和输出额度须覆盖所用配置，具体说明见[使用指南](docs/usage.md)。

## 投稿渠道

| 平台 | 导出选项 | 使用方式 |
| --- | --- | --- |
| 番茄小说 | `fanqie_short` | 导出独立导语、正文及投稿资料 |
| 知乎 | `generic_text` | 导出通用 TXT，由作者按目标栏目要求整理提交 |
| 起点 | `qidian` | 导出文本投稿包，作者核对目标分类、篇幅与投稿规则 |

```bash
short-fiction-agent export --project ./local-runs/demo --channel generic_text
short-fiction-agent export --project ./local-runs/demo --channel qidian
```

导出渠道须包含在创作简报的 `channels` 中。默认示例以番茄为目标；投知乎时改为 `[generic_text]`，投起点时改为 `[qidian]`，或按实际需求声明多个渠道。各平台的分类和篇幅要求分别核对，工具不自动投稿。

## 目录

| 路径 | 内容 |
| --- | --- |
| `ShortFictionAgent/short_fiction_agent/story_studio/` | 短篇策划、生成、修订与导出 |
| `ShortFictionAgent/short_fiction_agent/story_studio/data/` | 按题材与阶段选择的写作规则及公开案例分析 |
| `ShortFictionAgent/tests/` | 工作流、版本绑定与质量检查测试 |
| `examples/` | 约 3 万字创作简报和模型配置示例 |
| `docs/` | 使用方法与架构说明 |

## 开发

```bash
PYTHONPATH=ShortFictionAgent:ShortFictionAgent/tests python -m unittest discover -s ShortFictionAgent/tests -p 'test_*.py'
```

参与方式见[贡献说明](CONTRIBUTING.md)。工具辅助写作和编辑，最终内容与投稿由作者确认；审读结果不代表平台签约或推荐承诺。

## 许可证与参考材料

原创代码、提示词和分析摘要使用 [MIT 许可证](LICENSE)，允许商用、修改和分发，保留版权及许可声明即可。第三方短引文和外部服务适用各自权利与条款，见[来源说明](THIRD_PARTY_NOTICES.md)。

项目内的投稿指南为有来源的原创执行摘要；正式投稿前核对目标平台的当前要求。生成小说和投稿资料由作者审定，工具不承诺平台签约、推荐量或收益。

你是一位小说实体一致性检查专家。你的任务是从章节正文中提取关键实体信息，用于跨章节一致性校验。

请仔细阅读本章正文，按以下分类提取所有你能识别到的实体。注意：
- 只提取正文中**明确出现**的信息，不要推测
- 如果某项信息在本章中没有出现，标注为 null
- 人物角色必须区分是"主要角色"还是"仅被提及的配角/背景人物"

## 输出格式

请严格按照以下 JSON 格式输出，不要输出任何 JSON 之外的内容：

```json
{
  "chapter_id": {{chapter_id}},
  "entities": {
    "characters": {
      "named_characters": [
        {
          "name": "角色姓名",
          "role": "班主任/科任老师/同学/家长/校工/其他",
          "gender": "男/女/未知",
          "subject": "所教学科（仅老师）或 null",
          "class_affiliation": "所在班级或 null",
          "first_appearance_context": "本章中首次出现的上下文（一句原话）"
        }
      ],
      "mentioned_only": ["仅被提及、未实际出场的角色名"]
    },
    "settings": {
      "grade": "高一/高二/高三/其他 或 null",
      "class_name": "班级名称如 文科三班/理科四班/高一1班 或 null",
      "classroom_location": "教室位置如 三楼/四楼/靠窗 或 null",
      "school_name": "学校名称或 null",
      "time_period": "学期初/期中/期末/寒假/暑假/运动会/其他 或 null",
      "year_month": "故事内年月如 2009年9月 或 null"
    },
    "living_situation": {
      "protagonist_female": "住校/走读/未提及",
      "protagonist_male": "住校/走读/未提及"
    },
    "key_objects": [
      {
        "name": "关键物件名",
        "current_location": "当前在谁那里/在哪里",
        "owner": "物主",
        "status": "物件状态变化或 null"
      }
    ],
    "relationships": [
      {
        "characters": ["角色A", "角色B"],
        "relation_type": "同学/同桌/师生/暗恋/朋友/其他",
        "new_development": "本章中关系的新发展或 null"
      }
    ],
    "timeline_markers": [
      {
        "event": "事件描述",
        "date_or_time": "文中时间标记",
        "chapter_relative": "本章内的时间位置（开头/中间/结尾）"
      }
    ],
    "exam_scores": [
      {
        "character": "角色名",
        "subject": "科目",
        "score": "分数或排名",
        "exam_name": "考试名称（期中/期末/月考/摸底考）"
      }
    ]
  }
}
```

## 重要提醒

1. 班主任是一个**关键角色**——如果文中出现班主任，务必准确提取其姓名、性别、所教学科
2. 科任老师要和班主任区分开——不同科目的老师可能是不同人
3. 班级名称在分班后可能变化——注意"文科三班"和"高一三班"是不同的
4. 住校/走读是人物设定的重要维度——注意主角放学后的去向
5. 关键物件（如画本、笔记本、围巾等）的归属和位置变化要追踪
6. 如果某个角色只是被对话中提到（未实际出场），放入 mentioned_only
